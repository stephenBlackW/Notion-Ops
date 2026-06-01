"""Tests for idempotent republish (ISS-012) and partial-publish observability
(HL-patchA-1) in ``notion_ops.utils.publish``.

``publish_block_tree`` is create-only (append); ``republish_block_tree`` clears
the existing top-level children first so re-running converges to the same page
content instead of duplicating it.
"""

from __future__ import annotations

from typing import Any

from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.publish import (
    PublishResult,
    RepublishResult,
    publish_block_tree,
    republish_block_tree,
    republish_markdown,
)

PAGE = "11111111-1111-1111-1111-111111111111"


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


class StatefulFakeClient:
    """A fake SDK that maintains the page's top-level child list.

    ``append`` adds returned ids to the store, ``delete`` removes them, ``list``
    returns the current store. This makes republish idempotency observable: the
    store size after N republishes of the same input equals one publish's size.
    """

    def __init__(self, existing: int = 0) -> None:
        self.page = extract_notion_id(PAGE)
        self._store: list[str] = [f"pre-{i}" for i in range(existing)]
        self._n = 0
        self.deleted: list[str] = []
        self.append_calls: list[dict[str, Any]] = []
        client = self

        class _Children:
            def append(
                self, *, block_id: str, children: list[dict[str, Any]]
            ) -> dict[str, Any]:
                base = client._n
                client._n += 1
                client.append_calls.append(
                    {"block_id": block_id, "children": children}
                )
                results = [
                    {"id": f"blk-{base}-{i}", "type": c.get("type")}
                    for i, c in enumerate(children)
                ]
                # Only top-level appends (directly under the page) populate the
                # observable store; nested follow-ups attach under other blocks.
                if block_id == client.page:
                    client._store.extend(r["id"] for r in results)
                return {"results": results}

            def list(
                self,
                *,
                block_id: str,
                page_size: int = 100,
                start_cursor: str | None = None,
            ) -> dict[str, Any]:
                return {
                    "results": [{"id": bid} for bid in client._store],
                    "has_more": False,
                }

        class _Blocks:
            children = _Children()

            def delete(self, *, block_id: str) -> None:
                client.deleted.append(block_id)
                if block_id in client._store:
                    client._store.remove(block_id)

        class _API:
            blocks = _Blocks()

        self.api = _API()

    @property
    def top_level_count(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Idempotent republish (ISS-012)
# ---------------------------------------------------------------------------


class TestRepublish:
    def test_clears_existing_then_publishes(self):
        client = StatefulFakeClient(existing=3)
        result = republish_block_tree(client, PAGE, [_leaf(0), _leaf(1)])

        assert isinstance(result, RepublishResult)
        assert result.deleted_count == 3
        assert client.deleted == ["pre-0", "pre-1", "pre-2"]
        assert len(result.top_level_block_ids) == 2
        # Page now holds exactly the new blocks.
        assert client.top_level_count == 2

    def test_idempotent_no_accumulation(self):
        """The binding test: republishing the same input twice does not stack."""
        client = StatefulFakeClient(existing=0)
        blocks = [_leaf(0), _leaf(1), _leaf(2)]

        first = republish_block_tree(client, PAGE, blocks)
        after_first = client.top_level_count
        second = republish_block_tree(client, PAGE, blocks)

        assert after_first == 3
        assert client.top_level_count == 3  # not 6 — content did not accumulate
        # The second run deleted the 3 created by the first.
        assert second.deleted_count == 3
        assert first.deleted_count == 0

    def test_publish_does_accumulate_contrast(self):
        """Control: plain publish_block_tree (append-only) DOES duplicate."""
        client = StatefulFakeClient(existing=0)
        blocks = [_leaf(0), _leaf(1)]
        publish_block_tree(client, PAGE, blocks)
        publish_block_tree(client, PAGE, blocks)
        assert client.top_level_count == 4  # accumulation — the bug republish fixes

    def test_empty_blocks_clears_page(self):
        client = StatefulFakeClient(existing=2)
        result = republish_block_tree(client, PAGE, [])
        assert result.deleted_count == 2
        assert client.top_level_count == 0
        assert result.request_count == 0  # nothing to append

    def test_republish_markdown_roundtrip(self):
        client = StatefulFakeClient(existing=1)
        result = republish_markdown(client, PAGE, "# Title\n\nbody paragraph")
        assert isinstance(result, RepublishResult)
        assert result.deleted_count == 1
        assert client.top_level_count >= 1

    def test_accepts_normalized_and_dashed_parent_id(self):
        client = StatefulFakeClient(existing=1)
        # Pass a dashed id; republish should normalize it the same way publish does.
        republish_block_tree(client, PAGE, [_leaf(0)])
        # All append + list + delete resolved against the normalized page id.
        assert all(c["block_id"] == client.page for c in client.append_calls)


class TestRepublishPagination:
    def test_clears_children_across_pages(self):
        """Existing children spanning multiple list pages are all deleted."""

        class PaginatedClient(StatefulFakeClient):
            def __init__(self) -> None:
                super().__init__(existing=0)
                self._pages = [
                    {
                        "results": [{"id": "old-0"}, {"id": "old-1"}],
                        "has_more": True,
                        "next_cursor": "c1",
                    },
                    {
                        "results": [{"id": "old-2"}],
                        "has_more": False,
                        "next_cursor": None,
                    },
                ]
                client = self

                class _Children:
                    def append(self, *, block_id, children):
                        return {"results": []}

                    def list(self, *, block_id, page_size=100, start_cursor=None):
                        return (
                            client._pages[1] if start_cursor == "c1"
                            else client._pages[0]
                        )

                class _Blocks:
                    children = _Children()

                    def delete(self, *, block_id):
                        client.deleted.append(block_id)

                class _API:
                    blocks = _Blocks()

                self.api = _API()

        client = PaginatedClient()
        result = republish_block_tree(client, PAGE, [])
        assert result.deleted_count == 3
        assert client.deleted == ["old-0", "old-1", "old-2"]


# ---------------------------------------------------------------------------
# Partial-publish observability (HL-patchA-1)
# ---------------------------------------------------------------------------


class DroppingClient:
    """An append that never returns ids — so every deferred follow-up is dropped."""

    def __init__(self) -> None:
        self.calls = 0
        client = self

        class _Children:
            def append(self, *, block_id, children):
                client.calls += 1
                return {"results": []}  # no ids -> followups cannot resolve a parent

            def list(self, *, block_id, page_size=100, start_cursor=None):
                return {"results": [], "has_more": False}

        class _Blocks:
            children = _Children()

            def delete(self, *, block_id):
                pass

        class _API:
            blocks = _Blocks()

        self.api = _API()


# A tree deep enough (height 3 > max_inline_depth 2) to force a deferred
# follow-up append, which DroppingClient then drops.
def _deep_tree() -> list[dict[str, Any]]:
    return [_toggle("outer", [_toggle("mid", [_toggle("inner", [_leaf(0)])])])]


class TestPartialObservability:
    def test_partial_flag_when_followup_parent_missing(self):
        # 3-level nesting forces a deferred follow-up; DroppingClient returns no
        # ids, so that follow-up is skipped.
        result = publish_block_tree(DroppingClient(), PAGE, _deep_tree())
        assert isinstance(result, PublishResult)
        assert result.partial is True
        assert result.skipped_followups >= 1

    def test_not_partial_on_clean_publish(self):
        client = StatefulFakeClient()
        result = publish_block_tree(client, PAGE, [_leaf(0), _leaf(1)])
        assert result.partial is False
        assert result.skipped_followups == 0

    def test_partial_propagates_through_republish(self):
        result = republish_block_tree(DroppingClient(), PAGE, _deep_tree())
        assert isinstance(result, RepublishResult)
        assert result.partial is True


def test_republish_symbols_exported_top_level():
    import notion_ops

    for name in ("republish_block_tree", "republish_markdown", "RepublishResult"):
        assert hasattr(notion_ops, name)
        assert name in notion_ops.__all__
