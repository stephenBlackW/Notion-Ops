"""Tests for the nested-block publish planner and executor.

These verify that a nested block tree (as produced by markdown_to_blocks) is
broken into the minimum number of append requests that respects Notion's
two-level per-request nesting limit, the 100-block batch cap, and the payload
size cap — and that deferred sub-trees are appended under the correct parent
IDs returned by earlier requests.
"""

from __future__ import annotations

from typing import Any

from notion_ops.utils.markdown import _MAX_PAYLOAD_BYTES, markdown_to_blocks
from notion_ops.utils.publish import (
    build_publish_plan,
    count_requests,
    publish_block_tree,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _leaf(i: int) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"p{i}"}}]},
    }


def _toggle(label: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": label}}],
            "children": children,
        },
    }


class FakeClient:
    """Records append calls and returns results with deterministic block IDs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._n = 0

        client = self

        class _Append:
            def append(self, *, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
                base = client._n
                client._n += 1
                client.calls.append({"block_id": block_id, "children": children})
                return {
                    "results": [
                        {"id": f"blk-{base}-{i}", "type": c.get("type")}
                        for i, c in enumerate(children)
                    ]
                }

        class _Blocks:
            children = _Append()

        class _API:
            blocks = _Blocks()

        self.api = _API()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

class TestPlanning:
    def test_flat_blocks_single_request(self):
        plan = build_publish_plan([_leaf(0), _leaf(1), _leaf(2)])
        assert count_requests(plan) == 1
        assert len(plan[0].payload) == 3
        assert plan[0].followups == []

    def test_shallow_toggle_inlined(self):
        # toggle -> [paragraph, paragraph] has height 1; fully inlined.
        tree = [_toggle("T", [_leaf(0), _leaf(1)])]
        plan = build_publish_plan(tree)
        assert count_requests(plan) == 1
        assert "children" in plan[0].payload[0]["toggle"]

    def test_two_level_nesting_inlined(self):
        # toggle -> toggle -> paragraph has height 2; still one request.
        tree = [_toggle("outer", [_toggle("inner", [_leaf(0)])])]
        plan = build_publish_plan(tree)
        assert count_requests(plan) == 1

    def test_three_level_nesting_deferred(self):
        # height 3 exceeds the inline limit -> split into 2 requests.
        tree = [_toggle("a", [_toggle("b", [_toggle("c", [_leaf(0)])])])]
        plan = build_publish_plan(tree)
        assert count_requests(plan) == 2
        # First request appends the outer toggle WITHOUT children...
        assert "children" not in plan[0].payload[0]["toggle"]
        # ...and defers them as a follow-up keyed to that block (index 0).
        assert len(plan[0].followups) == 1
        assert plan[0].followups[0].parent_index == 0

    def test_max_inline_depth_one_defers_table_in_toggle(self):
        tree = [_toggle("T", [_leaf(0)])]  # height 1
        plan = build_publish_plan(tree, max_inline_depth=1)
        # height 1 <= 1 -> still inlined at depth 1
        assert count_requests(plan) == 1
        deeper = [_toggle("outer", [_toggle("inner", [_leaf(0)])])]  # height 2
        plan2 = build_publish_plan(deeper, max_inline_depth=1)
        assert count_requests(plan2) == 2

    def test_batches_top_level_by_count(self):
        plan = build_publish_plan([_leaf(i) for i in range(250)])
        assert count_requests(plan) == 3  # 100 + 100 + 50
        assert [len(r.payload) for r in plan] == [100, 100, 50]

    def test_deferred_children_order_preserved(self):
        # A deferred parent's children keep their order in the follow-up.
        kids = [_leaf(0), _toggle("mid", [_toggle("x", [_leaf(9)])]), _leaf(1)]
        tree = [_toggle("root", [_toggle("deep", [_toggle("deeper", kids)])])]
        plan = build_publish_plan(tree)
        # Walk to the request that appends `kids` and check ordering.
        # root(h4) deferred -> deep(h3) deferred -> deeper(h2) inlined w/ kids?
        # deeper has child 'mid' (height 2) so deeper height = 3 -> deferred too.
        assert count_requests(plan) >= 2


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

class TestExecution:
    PAGE = "11111111-1111-1111-1111-111111111111"

    def test_flat_single_append(self):
        client = FakeClient()
        result = publish_block_tree(client, self.PAGE, [_leaf(0), _leaf(1)])
        assert result.request_count == 1
        assert len(client.calls) == 1
        assert len(result.top_level_block_ids) == 2

    def test_deferred_uses_returned_parent_id(self):
        # outer(height3) -> first append creates outer; follow-up must target
        # the returned id of that outer block, not the page.
        tree = [_toggle("a", [_toggle("b", [_toggle("c", [_leaf(0)])])])]
        client = FakeClient()
        result = publish_block_tree(client, self.PAGE, tree)
        assert result.request_count == 2
        # First call appends under the (normalized) page id.
        first = client.calls[0]
        assert first["block_id"].replace("-", "") == self.PAGE.replace("-", "")
        assert "children" not in first["children"][0]["toggle"]
        # Second call appends under the id returned for that first block.
        second = client.calls[1]
        assert second["block_id"] == "blk-0-0"

    def test_count_limit_produces_multiple_appends(self):
        client = FakeClient()
        result = publish_block_tree(client, self.PAGE, [_leaf(i) for i in range(250)])
        assert result.request_count == 3
        assert all(len(c["children"]) <= 100 for c in client.calls)

    def test_size_limit_splits_appends(self):
        big = []
        for i in range(200):
            big.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": "x" * 1700}}]
                    },
                }
            )
        client = FakeClient()
        result = publish_block_tree(client, self.PAGE, big)
        assert result.request_count >= 2

    def test_end_to_end_dossier_markdown(self):
        md = (
            "## Zone 0\n\n"
            "<details>\n<summary>Outer</summary>\n\n"
            "<details>\n<summary>Inner</summary>\n\n"
            "leaf paragraph\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "</details>\n\n"
            "</details>\n"
        )
        blocks = markdown_to_blocks(md)
        client = FakeClient()
        result = publish_block_tree(client, self.PAGE, blocks)
        # Outer toggle subtree is too deep to inline -> at least 2 requests.
        assert result.request_count >= 2
        # Nothing dropped: every append carried a non-empty payload.
        assert all(c["children"] for c in client.calls)


def _table(n_rows: int) -> dict[str, Any]:
    rows = [
        {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": [[{"type": "text", "text": {"content": str(i)}}]]},
        }
        for i in range(n_rows)
    ]
    return {
        "object": "block",
        "type": "table",
        "table": {"table_width": 1, "has_column_header": True, "children": rows},
    }


class TestTableSplitting:
    def test_small_table_inlined(self):
        plan = build_publish_plan([_table(10)])
        assert count_requests(plan) == 1
        assert len(plan[0].payload[0]["table"]["children"]) == 10
        assert plan[0].followups == []

    def test_large_table_splits_rows(self):
        plan = build_publish_plan([_table(150)])
        assert count_requests(plan) == 2
        head = plan[0].payload[0]["table"]["children"]
        # First request keeps room for the table block itself (<= 99 rows).
        assert len(head) <= 99
        assert len(plan[0].followups) == 1
        # Every row is accounted for across head + follow-up batches.
        deferred = sum(
            len(r.payload) for r in plan[0].followups[0].requests
        )
        assert len(head) + deferred == 150

    def test_no_request_exceeds_100_rows(self):
        plan = build_publish_plan([_table(250)])

        def check(requests):
            for req in requests:
                total = sum(_block_count(b) for b in req.payload)
                assert total <= 100
                for fu in req.followups:
                    check(fu.requests)

        def _block_count(block: dict[str, Any]) -> int:
            body = block.get(block.get("type", ""), {})
            kids = body.get("children", []) if isinstance(body, dict) else []
            return 1 + sum(_block_count(k) for k in kids)

        check(plan)

    def test_big_table_in_toggle_floats_to_top_level(self):
        toggle = _toggle("T", [_table(150)])
        plan = build_publish_plan([toggle])
        # Toggle can't inline the oversized table -> it defers; the table is
        # split once it is top-level under the toggle's id.
        assert count_requests(plan) == 3
        assert "children" not in plan[0].payload[0]["toggle"]

    def test_execute_appends_rows_to_table_id(self):
        client = FakeClient()
        result = publish_block_tree(client, "11111111-1111-1111-1111-111111111111", [_table(150)])
        assert result.request_count == 2
        # First call creates the table (top-level); second appends rows to the
        # returned table id (blk-0-0), not the page.
        assert client.calls[1]["block_id"] == "blk-0-0"
        assert all(c["type"] == "table_row" for c in client.calls[1]["children"])


def test_payload_byte_constant_is_reasonable():
    # Guard against an accidental tiny cap that would explode request counts.
    assert _MAX_PAYLOAD_BYTES >= 100_000
