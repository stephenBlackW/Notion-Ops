"""Minimal-write republish diff + atomicity (nops-cycle-2).

Binds nops-cycle-1-HL-1 (no minimal-write content diff) and nops-cycle-1-HL-2
(not atomic). ``republish_block_tree`` now:

- does **zero writes** when the page already holds the requested content (AC-1),
- preserves the ids of the **unchanged leading blocks** (AC-2),
- appends the new suffix **before** deleting the old one (AC-3),
- keeps the listing paginated + retry-wrapped and reports the **actual** delete
  count (AC-4).

The fake client below stores a real block tree and returns **API-realistic**
list responses — each span carries ``plain_text``, the full default-annotation
set, and ``href`` — so the tests prove the diff's content hash genuinely bridges
``markdown_to_blocks``-shape against Notion-API-shape (not a rigged echo).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.publish import (
    RepublishResult,
    _children_of,
    _without_children,
    republish_block_tree,
)

PAGE = "11111111-1111-1111-1111-111111111111"

_DEFAULT_ANNOTATIONS = {
    "bold": False,
    "italic": False,
    "strikethrough": False,
    "underline": False,
    "code": False,
    "color": "default",
}


def _para(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
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


class ContentFakeClient:
    """A content-aware fake SDK that models a page's block tree.

    ``append`` stores blocks under a parent and assigns ids; ``list`` returns
    API-realistic dicts; ``delete`` removes a block. ``ops`` is the ordered log
    of ("append", id) / ("delete", id) so write ordering is observable.
    """

    def __init__(
        self, initial: list[dict[str, Any]] | None = None, page_cap: int = 100
    ) -> None:
        self.page = extract_notion_id(PAGE)
        self._n = 0
        self._nodes: dict[str, dict[str, Any]] = {}          # id -> stored body
        self._children: dict[str, list[str]] = {self.page: []}  # parent -> child ids
        self.ops: list[tuple[str, str]] = []
        self.append_calls = 0
        self.list_calls = 0
        self._page_cap = page_cap
        client = self

        class _Children:
            def append(
                self, *, block_id: str, children: list[dict[str, Any]]
            ) -> dict[str, Any]:
                client.append_calls += 1
                ids = client._insert(block_id, children, log=True)
                return {
                    "results": [
                        {
                            "id": i,
                            "type": client._nodes[i].get("type"),
                            "has_children": bool(client._children.get(i)),
                        }
                        for i in ids
                    ]
                }

            def list(
                self,
                *,
                block_id: str,
                page_size: int = 100,
                start_cursor: str | None = None,
            ) -> dict[str, Any]:
                client.list_calls += 1
                kids = client._children.get(block_id, [])
                start = int(start_cursor) if start_cursor else 0
                size = min(page_size, client._page_cap)
                chunk = kids[start : start + size]
                has_more = start + size < len(kids)
                return {
                    "results": [client._apiify(i) for i in chunk],
                    "has_more": has_more,
                    "next_cursor": str(start + size) if has_more else None,
                }

        class _Blocks:
            children = _Children()

            def delete(self, *, block_id: str) -> None:
                client.ops.append(("delete", block_id))
                client._remove(block_id)

        class _API:
            blocks = _Blocks()

        self.api = _API()
        if initial:
            self._insert(self.page, initial, log=False)

    # -- storage helpers ---------------------------------------------------
    def _insert(
        self, parent_id: str, blocks: list[dict[str, Any]], *, log: bool
    ) -> list[str]:
        ids: list[str] = []
        self._children.setdefault(parent_id, [])
        for blk in blocks:
            self._n += 1
            bid = f"id-{self._n}"
            self._nodes[bid] = _without_children(blk)
            self._children[parent_id].append(bid)
            self._children.setdefault(bid, [])
            if log:
                self.ops.append(("append", bid))
            grandkids = _children_of(blk)
            if grandkids:
                self._insert(bid, grandkids, log=False)
            ids.append(bid)
        return ids

    def _remove(self, block_id: str) -> None:
        for kids in self._children.values():
            if block_id in kids:
                kids.remove(block_id)
        self._nodes.pop(block_id, None)
        self._children.pop(block_id, None)

    def _apiify_span(self, span: dict[str, Any]) -> dict[str, Any]:
        s = copy.deepcopy(span)
        text = s.get("text") or {}
        content = text.get("content", "")
        s["plain_text"] = content
        link = text.get("link")
        s["href"] = link.get("url") if isinstance(link, dict) else None
        s["annotations"] = {**_DEFAULT_ANNOTATIONS, **(s.get("annotations") or {})}
        return s

    def _apiify(self, bid: str) -> dict[str, Any]:
        b = copy.deepcopy(self._nodes[bid])
        b["id"] = bid
        b["object"] = "block"
        b["has_children"] = bool(self._children.get(bid))
        b["created_time"] = "2026-01-01T00:00:00.000Z"
        b["last_edited_time"] = "2026-01-01T00:00:00.000Z"
        b["archived"] = False
        btype = b.get("type", "")
        body = b.get(btype)
        if isinstance(body, dict) and "rich_text" in body:
            body["rich_text"] = [self._apiify_span(s) for s in body["rich_text"]]
            body.setdefault("color", "default")
        return b

    # -- observation helpers ----------------------------------------------
    @property
    def top_ids(self) -> list[str]:
        return list(self._children[self.page])

    @property
    def appended(self) -> list[str]:
        return [i for kind, i in self.ops if kind == "append"]

    @property
    def deleted(self) -> list[str]:
        return [i for kind, i in self.ops if kind == "delete"]


# ---------------------------------------------------------------------------
# AC-1 — no-op on identical content
# ---------------------------------------------------------------------------


class TestNoOpOnIdentical:
    def test_identical_flat_content_is_zero_write(self):
        blocks = [_para("alpha"), _para("beta"), _para("gamma")]
        client = ContentFakeClient(initial=blocks)
        before = client.top_ids

        result = republish_block_tree(client, PAGE, copy.deepcopy(blocks))

        assert isinstance(result, RepublishResult)
        assert result.request_count == 0
        assert result.deleted_count == 0
        assert client.ops == []                       # no appends, no deletes
        assert result.top_level_block_ids == before   # existing ids preserved
        assert client.top_ids == before

    def test_identical_nested_content_is_zero_write(self):
        """Recursive subtree hashing + child fetch must also see 'unchanged'."""
        blocks = [
            _para("intro"),
            _toggle("details", [_para("a"), _para("b")]),
        ]
        client = ContentFakeClient(initial=blocks)
        before = client.top_ids

        result = republish_block_tree(client, PAGE, copy.deepcopy(blocks))

        assert result.request_count == 0
        assert result.deleted_count == 0
        assert client.ops == []
        assert client.top_ids == before

    def test_inline_markup_survives_api_noise(self):
        """A bold span (annotations) must still hash equal across API-shape noise."""
        blocks = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "x"}},
                        {
                            "type": "text",
                            "text": {"content": "y"},
                            "annotations": {"bold": True},
                        },
                    ]
                },
            }
        ]
        client = ContentFakeClient(initial=blocks)
        result = republish_block_tree(client, PAGE, copy.deepcopy(blocks))
        assert result.request_count == 0
        assert client.ops == []


# ---------------------------------------------------------------------------
# AC-2 — prefix id preservation
# ---------------------------------------------------------------------------


class TestPrefixPreservation:
    def test_tail_change_rewrites_only_tail(self):
        client = ContentFakeClient(
            initial=[_para("p0"), _para("p1"), _para("p2")]
        )
        kept = client.top_ids[:2]

        result = republish_block_tree(
            client, PAGE, [_para("p0"), _para("p1"), _para("CHANGED")]
        )

        # The unchanged prefix ids survive; only the 3rd block churned.
        assert client.top_ids[:2] == kept
        assert result.deleted_count == 1
        assert len(client.appended) == 1
        assert result.top_level_block_ids[:2] == kept

    def test_pure_append_rewrites_nothing_existing(self):
        client = ContentFakeClient(initial=[_para("p0"), _para("p1")])
        kept = client.top_ids

        result = republish_block_tree(
            client, PAGE, [_para("p0"), _para("p1"), _para("p2")]
        )

        assert result.deleted_count == 0
        assert len(client.appended) == 1
        assert client.top_ids[:2] == kept           # prefix untouched
        assert client.top_ids[2:] == client.appended

    def test_changed_block_is_not_falsely_matched(self):
        """A one-character edit must NOT be treated as unchanged (over-norm guard)."""
        client = ContentFakeClient(initial=[_para("hello")])
        result = republish_block_tree(client, PAGE, [_para("hellp")])
        assert result.request_count >= 1
        assert result.deleted_count == 1


# ---------------------------------------------------------------------------
# AC-3 — publish-before-delete atomicity
# ---------------------------------------------------------------------------


class TestAtomicOrdering:
    def test_every_append_precedes_every_delete(self):
        client = ContentFakeClient(
            initial=[_para("a"), _para("b"), _para("c")]
        )
        # Full divergence (k=0): all old deleted, all new appended.
        republish_block_tree(client, PAGE, [_para("x"), _para("y")])

        kinds = [kind for kind, _ in client.ops]
        last_append = max(i for i, k in enumerate(kinds) if k == "append")
        first_delete = min(i for i, k in enumerate(kinds) if k == "delete")
        assert last_append < first_delete

    def test_interrupt_during_delete_leaves_new_content(self):
        """If a delete raises mid-clear, the page keeps the new content (never empty)."""

        class RaisingDeleteClient(ContentFakeClient):
            def __init__(self) -> None:
                super().__init__(initial=[_para("old0"), _para("old1")])
                inner = self.api.blocks

                def boom(*, block_id: str) -> None:
                    raise RuntimeError("delete interrupted")

                inner.delete = boom  # type: ignore[method-assign]

        client = RaisingDeleteClient()
        with pytest.raises(RuntimeError):
            republish_block_tree(client, PAGE, [_para("new0")])

        # The new content was appended before the (failing) delete phase, so the
        # page is non-empty and contains the new block — not the empty-page state
        # the v0.1.0 clear-first order would leave.
        assert client.top_ids, "page must not be empty after interrupted delete"
        new_bodies = [
            client._nodes[i]["paragraph"]["rich_text"][0]["text"]["content"]
            for i in client.top_ids
            if client._nodes[i].get("type") == "paragraph"
        ]
        assert "new0" in new_bodies


# ---------------------------------------------------------------------------
# AC-4 — paginated, retry-wrapped, actual delete count, backward-compat
# ---------------------------------------------------------------------------


class TestListingAndCounts:
    def test_deleted_count_is_actual_not_full_clear(self):
        client = ContentFakeClient(
            initial=[_para("p0"), _para("p1"), _para("p2"), _para("p3")]
        )
        result = republish_block_tree(client, PAGE, [_para("p0"), _para("p1")])
        assert result.deleted_count == 2          # not 4 — prefix kept
        assert len(client.top_ids) == 2

    def test_diff_reads_existing_across_pages(self):
        """A no-op must hold even when existing children span multiple list pages."""
        blocks = [_para(f"p{i}") for i in range(5)]
        client = ContentFakeClient(initial=blocks, page_cap=2)  # force pagination
        result = republish_block_tree(client, PAGE, copy.deepcopy(blocks))
        assert result.request_count == 0
        assert client.ops == []
        assert client.list_calls >= 3             # 5 blocks / 2 per page

    def test_empty_new_clears_page(self):
        client = ContentFakeClient(initial=[_para("a"), _para("b")])
        result = republish_block_tree(client, PAGE, [])
        assert result.deleted_count == 2
        assert result.request_count == 0
        assert client.top_ids == []

    def test_list_and_delete_are_retry_wrapped(self, monkeypatch):
        """A single transient 503 on list + delete is absorbed (retry_on_transient)."""
        import notion_ops.utils.retry as retry_mod

        monkeypatch.setattr(retry_mod.time, "sleep", lambda *_: None)

        class FlakyClient(ContentFakeClient):
            def __init__(self) -> None:
                super().__init__(initial=[_para("old")])
                self._list_failed = False
                self._delete_failed = False
                inner = self.api.blocks
                real_list = inner.children.list
                real_delete = inner.delete

                def flaky_list(**kw):
                    if not self._list_failed:
                        self._list_failed = True
                        raise RuntimeError("HTTP 503 service unavailable")
                    return real_list(**kw)

                def flaky_delete(**kw):
                    if not self._delete_failed:
                        self._delete_failed = True
                        raise RuntimeError("HTTP 503 service unavailable")
                    return real_delete(**kw)

                inner.children.list = flaky_list      # type: ignore[method-assign]
                inner.delete = flaky_delete           # type: ignore[method-assign]

        client = FlakyClient()
        result = republish_block_tree(client, PAGE, [_para("new")])
        assert result.deleted_count == 1
        assert client._list_failed and client._delete_failed
