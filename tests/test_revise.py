"""AC-7..AC-9 (nops-cycle-3) — versioned revision.

``revise_page`` is the non-destructive way to change what a published page says.
What these tests hold it to:

- **The supersede link is written on exactly one side** (AC-7). ``Next``/
  ``Previous`` are a *dual* relation: writing one side makes Notion populate the
  other. Writing both would be a second request whose only effect is to look
  redundant when it agrees and to corrupt the chain when it does not. The library
  half is bound here; the server half is AC-7-live in ``test_e2e_smoke.py``.
- **The superseded page is retitled and status-stamped, and its body is never
  touched** (AC-8) — no delete, no append, and never ``pages.archive``/
  ``in_trash``, which would take the discussion out of reach and re-create the
  loss this cycle exists to prevent.
- **Nothing is hardcoded** (AC-7). Every property name comes from the caller's
  ``RevisionSchema``; a fixture that swaps the names swaps the asserted keys.
- **Snapshot mode reads before it writes** (AC-9), keeps the hub page's id, and
  is the only caller of ``allow_destructive=True``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from notion_ops.exceptions import IncompleteSnapshotError
from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.publish import _children_of, _without_children
from notion_ops.utils.revise import (
    _UNCOPYABLE_BLOCK_TYPES,
    _UNTYPED_BLOCK,
    NEW_CANONICAL,
    SNAPSHOT_IN_PLACE,
    RevisionResult,
    RevisionSchema,
    revise_page,
)

#: The fake operations that change something. A refusal must issue none of them.
_WRITE_CALLS = frozenset({"pages.create", "pages.update", "blocks.append", "blocks.delete"})

SOURCE = "11111111-1111-1111-1111-111111111111"
SOURCE_ID = extract_notion_id(SOURCE)
DATA_SOURCE = "99999999-9999-9999-9999-999999999999"

#: The AgenticOS Atoms binding, supplied by the test as a *caller* would supply
#: it. Note that none of these strings live in the library (see
#: ``test_republish_guard.TestLibraryCarriesNoWorkspace``).
ATOMS_LIKE = RevisionSchema(
    title_property="Name",
    supersede_property="Next",
    predecessor_property="Previous",
    status_property="Status",
    archived_status_value="Archived",
    reason_property="Description",
    carry_properties=("Type", "Action Item"),
)

#: The same shape under different names, to prove the names are data.
MIRROR = RevisionSchema(
    title_property="Titel",
    supersede_property="Nachfolger",
    predecessor_property="Vorgaenger",
    status_property="Zustand",
    archived_status_value="Archiviert",
    carry_properties=("Art",),
)


def _para(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _toggle(text: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "children": children,
        },
    }


#: Plausible bodies for the uncopyable types that carry one. This is a table of
#: *shapes*, not a second copy of the membership list -- which types are
#: uncopyable is read from `_UNCOPYABLE_BLOCK_TYPES` itself, and a type absent
#: here still builds, with an empty body.
_UNCOPYABLE_BODIES = {
    "child_page": {"title": "Sub-page"},
    "child_database": {"title": "Sub-db"},
    "synced_block": {"synced_from": {"type": "block_id", "block_id": "blk-elsewhere"}},
}


def _uncopyable(btype: str) -> dict[str, Any]:
    """A block of *btype* in the shape the API returns it.

    The body is decoration: `_sanitize_block` refuses on the *type*, before it
    reads anything else, which is the whole reason a type the API cannot
    re-create is unsafe to snapshot around.
    """
    return {"object": "block", "type": btype, btype: _UNCOPYABLE_BODIES.get(btype, {})}


def _title_prop(text: str) -> dict[str, Any]:
    return {
        "id": "title",
        "type": "title",
        "title": [{"type": "text", "text": {"content": text}, "plain_text": text}],
    }


def _relation(*ids: str, has_more: bool = False) -> dict[str, Any]:
    return {"type": "relation", "relation": [{"id": i} for i in ids], "has_more": has_more}


def _hydrate(properties: dict[str, Any]) -> dict[str, Any]:
    """A write payload as the API would hand it back on the next read.

    The API adds ``type`` and, on every text span, the ``plain_text`` it derived —
    which is what ``_title_text`` reads. Without this a page created by the fake
    has an unreadable title, and a second revision of it would look like a
    revision of an untitled page.
    """
    out = copy.deepcopy(properties)
    for value in out.values():
        if not isinstance(value, dict):
            continue
        for key in ("title", "rich_text"):
            spans = value.get(key)
            if isinstance(spans, list):
                value.setdefault("type", key)
                for span in spans:
                    if isinstance(span, dict):
                        span.setdefault(
                            "plain_text", (span.get("text") or {}).get("content", "")
                        )
        if "relation" in value:
            value.setdefault("type", "relation")
            value.setdefault("has_more", False)
        if "select" in value:
            value.setdefault("type", "select")
    return out


class ReviseFakeClient:
    """A fake SDK with pages, blocks and comments, and an ordered call log.

    ``calls`` is the sequence of ``(operation, target_id)`` pairs, which is what
    the ordering assertions read: "the comments read happened before the first
    write" is a statement about this list, not about a mock's call count.
    """

    #: The dual relation pairs the fixture workspace defines, in both directions.
    #: Notion populates the inverse side of a dual relation itself, and the whole
    #: version chain is built out of that behaviour, so a fake that does not model
    #: it cannot express a second revision at all (hostile-4's reason for grading
    #: its own finding medium rather than high).
    DUAL_PAIRS = {
        "Next": "Previous",
        "Previous": "Next",
        "Nachfolger": "Vorgaenger",
        "Vorgaenger": "Nachfolger",
    }

    def __init__(
        self,
        *,
        properties: dict[str, Any] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        comments: list[dict[str, Any]] | None = None,
        parent: dict[str, Any] | None = None,
        drop_append_ids: bool = False,
        children_envelope: Callable[[str, dict[str, Any], int], Any] | None = None,
    ) -> None:
        self.drop_append_ids = drop_append_ids
        #: Rewrites the well-formed envelope ``blocks.children.list`` would have
        #: returned, so a test can express a *malformed* one — the shape a server
        #: contract violation actually arrives in. Called with the block id, the
        #: envelope, and which children read this is (1-based).
        self.children_envelope = children_envelope
        self._children_reads = 0
        self.calls: list[tuple[str, str]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.creates: list[dict[str, Any]] = []
        self._comments = list(comments or [])
        self._n = 0
        self._nodes: dict[str, dict[str, Any]] = {}
        self._children: dict[str, list[str]] = {SOURCE_ID: []}
        self._pages: dict[str, dict[str, Any]] = {
            SOURCE_ID: {
                "object": "page",
                "id": SOURCE_ID,
                "parent": parent or {"type": "data_source_id", "data_source_id": DATA_SOURCE},
                "properties": copy.deepcopy(properties or {"Name": _title_prop("Report")}),
                "archived": False,
                "in_trash": False,
            }
        }
        if blocks:
            self._insert(SOURCE_ID, blocks)
        client = self

        class _Pages:
            def retrieve(self, *, page_id: str) -> dict[str, Any]:
                client.calls.append(("pages.retrieve", page_id))
                if page_id not in client._pages:
                    # A relation entry always points at a page that exists. An id
                    # the fixture never registered models an ancestor carrying no
                    # properties of its own — the end of a chain, not an error.
                    client._pages[page_id] = {
                        "object": "page",
                        "id": page_id,
                        "parent": {
                            "type": "data_source_id",
                            "data_source_id": DATA_SOURCE,
                        },
                        "properties": {},
                    }
                return copy.deepcopy(client._pages[page_id])

            def create(self, **payload: Any) -> dict[str, Any]:
                client._n += 1
                # Dash-free on purpose: extract_notion_id() strips dashes, so a
                # dashed fake id would not round-trip back to this store.
                new_id = f"page{client._n}"
                client.calls.append(("pages.create", new_id))
                client.creates.append(copy.deepcopy(payload))
                client._pages[new_id] = {
                    "object": "page",
                    "id": new_id,
                    "parent": copy.deepcopy(payload.get("parent", {})),
                    "properties": _hydrate(payload.get("properties", {})),
                }
                client._children.setdefault(new_id, [])
                client._apply_dual_inverse(new_id, payload.get("properties", {}), {})
                return copy.deepcopy(client._pages[new_id])

            def update(self, *, page_id: str, **payload: Any) -> dict[str, Any]:
                client.calls.append(("pages.update", page_id))
                client.updates.append((page_id, copy.deepcopy(payload)))
                stored = client._pages.setdefault(page_id, {"id": page_id, "properties": {}})
                written = payload.get("properties", {})
                before = copy.deepcopy(stored.setdefault("properties", {}))
                stored["properties"].update(_hydrate(written))
                for flag in ("archived", "in_trash"):
                    if flag in payload:
                        stored[flag] = payload[flag]
                client._apply_dual_inverse(page_id, written, before)
                return copy.deepcopy(stored)

        class _Children:
            def append(self, *, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
                client.calls.append(("blocks.append", block_id))
                ids = client._insert(block_id, children)
                if client.drop_append_ids:
                    # The DroppingClient shape (tests/test_republish.py): the
                    # append lands but returns no ids, so a deferred follow-up
                    # cannot resolve its parent and the publish is `partial`.
                    return {"results": []}
                return {"results": [{"id": i, "type": client._nodes[i].get("type")} for i in ids]}

            def list(
                self,
                *,
                block_id: str,
                page_size: int = 100,
                start_cursor: str | None = None,
            ) -> Any:
                client.calls.append(("blocks.list", block_id))
                client._children_reads += 1
                kids = client._children.get(block_id, [])
                envelope: dict[str, Any] = {
                    "results": [client._apiify(i) for i in kids],
                    "has_more": False,
                    "next_cursor": None,
                }
                if client.children_envelope is None:
                    return envelope
                return client.children_envelope(
                    block_id, envelope, client._children_reads
                )

        class _Blocks:
            children = _Children()

            def delete(self, *, block_id: str) -> None:
                client.calls.append(("blocks.delete", block_id))
                client._remove(block_id)

        class _Comments:
            def list(self, **params: Any) -> dict[str, Any]:
                client.calls.append(("comments.list", params.get("block_id", "")))
                return {
                    "results": client._comments,
                    "has_more": False,
                    "next_cursor": None,
                }

        class _API:
            pages = _Pages()
            blocks = _Blocks()
            comments = _Comments()

        self.api = _API()

    # -- storage -----------------------------------------------------------
    def _apply_dual_inverse(
        self,
        page_id: str,
        written: dict[str, Any],
        before: dict[str, Any],
    ) -> None:
        """Populate the other side of every dual relation this write touched.

        A relation write is a SET operation, so a target dropped from the array
        loses its inverse entry too — which is the mechanism that orphaned the
        earlier snapshots in round 1 and the reason this fake now models it.
        """
        for name, value in written.items():
            inverse = self.DUAL_PAIRS.get(name)
            if not inverse or not isinstance(value, dict) or "relation" not in value:
                continue
            new_ids = [r["id"] for r in value["relation"] if isinstance(r, dict)]
            old_ids = [
                r["id"]
                for r in (before.get(name) or {}).get("relation") or []
                if isinstance(r, dict)
            ]
            for target in set(old_ids) - set(new_ids):
                entries = (
                    self._pages.get(target, {}).get("properties", {}).get(inverse, {})
                ).get("relation")
                if isinstance(entries, list):
                    entries[:] = [e for e in entries if e.get("id") != page_id]
            for target in new_ids:
                page = self._pages.setdefault(
                    target, {"object": "page", "id": target, "properties": {}}
                )
                prop = page.setdefault("properties", {}).setdefault(
                    inverse, {"type": "relation", "relation": [], "has_more": False}
                )
                if page_id not in [e.get("id") for e in prop["relation"]]:
                    prop["relation"].append({"id": page_id})

    def _insert(self, parent_id: str, blocks: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        self._children.setdefault(parent_id, [])
        for blk in blocks:
            self._n += 1
            bid = f"blk-{self._n}"
            self._nodes[bid] = _without_children(blk)
            self._children[parent_id].append(bid)
            self._children.setdefault(bid, [])
            grandkids = _children_of(blk)
            if grandkids:
                self._insert(bid, grandkids)
            ids.append(bid)
        return ids

    def _remove(self, block_id: str) -> None:
        for kids in self._children.values():
            if block_id in kids:
                kids.remove(block_id)
        self._nodes.pop(block_id, None)
        self._children.pop(block_id, None)

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
        if isinstance(body, dict):
            for span in body.get("rich_text", []) or []:
                span.setdefault("plain_text", (span.get("text") or {}).get("content", ""))
                span.setdefault("href", None)
        return b

    # -- observation -------------------------------------------------------
    def text_of(self, page_id: str) -> list[str]:
        out = []
        for bid in self._children.get(page_id, []):
            body = self._nodes[bid].get(self._nodes[bid].get("type", ""), {})
            spans = body.get("rich_text", []) if isinstance(body, dict) else []
            out.append("".join((s.get("text") or {}).get("content", "") for s in spans))
        return out

    def block_ids(self, page_id: str) -> list[str]:
        return list(self._children.get(page_id, []))

    def title_of(self, page_id: str, name: str = "Name") -> str:
        """The plain text of a page's title property, as a reader would see it."""
        spans = (self._pages[page_id]["properties"].get(name) or {}).get("title") or []
        return "".join((s.get("text") or {}).get("content", "") for s in spans)

    def relation_of(self, page_id: str, name: str) -> list[str]:
        """The ids currently held by a page's relation property."""
        prop = self._pages.get(page_id, {}).get("properties", {}).get(name) or {}
        return [r["id"] for r in prop.get("relation") or []]

    def updates_to(self, page_id: str) -> list[dict[str, Any]]:
        return [payload for pid, payload in self.updates if pid == page_id]

    def relation_writes(self) -> list[tuple[str, str, list[str]]]:
        """Every ``(page_id, property, [target ids])`` relation write issued."""
        out = []
        for pid, payload in self.updates:
            for name, value in (payload.get("properties") or {}).items():
                if isinstance(value, dict) and "relation" in value:
                    out.append((pid, name, [r["id"] for r in value["relation"]]))
        return out


def _malform_the_first_hub_read(
    shape: str,
    *,
    keep: int = 0,
) -> Callable[[str, dict[str, Any], int], Any]:
    """A ``children_envelope`` hook that malforms the FIRST read of the hub page.

    Only the first read, because that is what makes the loss total: the snapshot
    copy sees part of the body while the republish's own diff read, one call
    later, sees all of it and deletes all of it. A hook that malformed every read
    would leave the rewrite under-deleting too, and would understate the damage.

    Shapes, all of them things a server can hand back:

    - ``truncated`` — ``has_more: true`` with no ``next_cursor``, keeping *keep*
      of the results (``keep=0`` is the truncated-to-empty variant, hostile-5's
      scenario transplanted onto the destructive path);
    - ``non-dict`` — not a JSON object at all;
    - ``results-not-a-list`` — an envelope whose ``results`` is not a list.
    """
    state = {"spent": False}

    def hook(block_id: str, envelope: dict[str, Any], nth: int) -> Any:
        if block_id != SOURCE_ID or state["spent"]:
            return envelope
        state["spent"] = True
        if shape == "truncated":
            return {
                "results": envelope["results"][:keep],
                "has_more": True,
                "next_cursor": None,
            }
        if shape == "non-dict":
            return None
        if shape == "results-not-a-list":
            return {"results": "hub block 1", "has_more": False, "next_cursor": None}
        raise AssertionError(f"unknown malformed shape {shape!r}")

    return hook


def _comment(cid: str, text: str, *, block_id: str | None = None) -> dict[str, Any]:
    parent = (
        {"type": "block_id", "block_id": block_id}
        if block_id
        else {"type": "page_id", "page_id": SOURCE_ID}
    )
    return {
        "object": "comment",
        "id": cid,
        "parent": parent,
        "discussion_id": f"d-{cid}",
        "created_by": {"object": "user", "id": "user-1"},
        "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
    }


# ---------------------------------------------------------------------------
# Argument validation — every ValueError before any request
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"new_markdown": "a", "new_blocks": [_para("a")]},
        ],
        ids=["neither", "both"],
    )
    def test_content_argument_is_exactly_one(self, kwargs):
        client = ReviseFakeClient()
        with pytest.raises(ValueError, match="new_markdown"):
            revise_page(client, SOURCE, **kwargs)
        assert client.calls == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"new_markdown": ""},
            {"new_markdown": "   \n\n  "},
            {"new_blocks": []},
        ],
        ids=["empty-markdown", "whitespace-markdown", "empty-blocks"],
    )
    def test_empty_content_is_refused_before_any_request(self, kwargs):
        """An empty body is a failed render, not a revision.

        ``markdown_to_blocks("")`` is ``[]``, the publish was skipped, and the
        result came back green with ``content_error=None`` — an empty successor
        promoted to canonical and the real content stamped ``Archived``. Nothing
        is deleted, so it is not ISS-029 again; it is the silent wrong answer on
        the happy path of the function whose whole selling point is not losing
        content silently. It now raises before the first request.
        """
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Report"), "Previous": _relation()},
            blocks=[_para("the real content")],
        )

        with pytest.raises(ValueError, match="empty"):
            revise_page(client, SOURCE, schema=ATOMS_LIKE, **kwargs)

        assert client.calls == []

    def test_unknown_mode_raises_before_any_request(self):
        client = ReviseFakeClient()
        with pytest.raises(ValueError, match="mode"):
            revise_page(client, SOURCE, new_markdown="x", mode="in-place")
        assert client.calls == []

    def test_snapshot_without_predecessor_property_raises(self):
        """A snapshot with no back-link would be orphaned, so it is refused."""
        client = ReviseFakeClient()
        schema = RevisionSchema(supersede_property="Next")
        with pytest.raises(ValueError, match="predecessor_property"):
            revise_page(
                client, SOURCE, new_markdown="x", schema=schema, mode=SNAPSHOT_IN_PLACE
            )
        assert client.calls == []


# ---------------------------------------------------------------------------
# AC-7 — the supersede link: one write, on the old page, under the caller's name
# ---------------------------------------------------------------------------


class TestSupersedeLink:
    def test_exactly_one_relation_write_on_the_old_page(self):
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Report"), "Previous": _relation()},
            blocks=[_para("old body")],
        )

        result = revise_page(
            client, SOURCE, new_markdown="fresh content", schema=ATOMS_LIKE
        )

        writes = client.relation_writes()
        assert len(writes) == 1
        page_id, prop, targets = writes[0]
        assert page_id == SOURCE_ID
        assert prop == "Next"
        assert targets == [result.canonical_page_id]

        # The inverse is the server's job: nothing writes Previous on the new page.
        assert not [
            w for w in writes if w[0] == result.canonical_page_id or w[1] == "Previous"
        ]

    def test_the_property_names_are_data_not_code(self):
        """Same fixture, different schema — the asserted key moves with it."""
        client = ReviseFakeClient(
            properties={"Titel": _title_prop("Bericht"), "Vorgaenger": _relation()},
            blocks=[_para("alt")],
        )

        result = revise_page(client, SOURCE, new_markdown="neu", schema=MIRROR)

        (page_id, prop, targets), = client.relation_writes()
        assert (page_id, prop, targets) == (
            SOURCE_ID,
            "Nachfolger",
            [result.canonical_page_id],
        )

    def test_no_relation_write_when_the_schema_names_none(self):
        """The minimal call: create the successor, return the ids, touch nothing."""
        client = ReviseFakeClient(blocks=[_para("old")])

        result = revise_page(client, SOURCE, new_markdown="new")

        assert client.relation_writes() == []
        assert result.canonical_page_id != SOURCE_ID
        assert result.archived_page_id == SOURCE_ID
        assert result.version == 1


# ---------------------------------------------------------------------------
# AC-8 — retitle + status on the superseded page; its body untouched
# ---------------------------------------------------------------------------


class TestSupersededPage:
    def test_retitle_status_and_version(self):
        client = ReviseFakeClient(
            properties={
                "Name": _title_prop("Report"),
                "Previous": _relation("older-1"),
                "Type": {"type": "select", "select": {"name": "Report"}},
            },
            blocks=[_para("old body")],
        )

        result = revise_page(
            client, SOURCE, new_markdown="new body", schema=ATOMS_LIKE
        )

        assert result.version == 2
        assert client.title_of(SOURCE_ID) == "Report (v2)"
        assert client._pages[SOURCE_ID]["properties"]["Status"]["select"] == {
            "name": "Archived"
        }

    def test_old_body_is_never_touched_and_the_page_is_never_trashed(self):
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Report"), "Previous": _relation()},
            blocks=[_para("keep me"), _para("and me")],
        )
        before = client.block_ids(SOURCE_ID)

        revise_page(client, SOURCE, new_markdown="new body", schema=ATOMS_LIKE)

        assert client.block_ids(SOURCE_ID) == before
        assert client.text_of(SOURCE_ID) == ["keep me", "and me"]
        assert [c for c in client.calls if c[0] == "blocks.delete"] == []
        assert [c for c in client.calls if c == ("blocks.append", SOURCE_ID)] == []
        # D-6: "Archived" is a status VALUE, never Notion's page flag.
        assert client._pages[SOURCE_ID].get("archived") is False
        assert client._pages[SOURCE_ID].get("in_trash") is False
        for payload in client.updates_to(SOURCE_ID):
            assert "archived" not in payload
            assert "in_trash" not in payload

    def test_status_write_is_skipped_when_the_schema_omits_it(self):
        schema = RevisionSchema(
            title_property="Name",
            supersede_property="Next",
            predecessor_property="Previous",
        )
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Report"), "Previous": _relation()},
            blocks=[_para("old")],
        )

        revise_page(client, SOURCE, new_markdown="new", schema=schema)

        assert "Status" not in client._pages[SOURCE_ID]["properties"]
        assert client.title_of(SOURCE_ID) == "Report (v1)"

    def test_version_suffix_does_not_compound(self):
        client = ReviseFakeClient(
            properties={
                "Name": _title_prop("Report (v2)"),
                "Previous": _relation("a", "b"),
            },
            blocks=[_para("old")],
        )

        result = revise_page(client, SOURCE, new_markdown="new", schema=ATOMS_LIKE)

        assert result.version == 3
        assert client.title_of(SOURCE_ID) == "Report (v3)"
        # The successor carries the CLEAN title.
        created = client.creates[0]["properties"]
        assert created["Name"] == {
            "title": [{"type": "text", "text": {"content": "Report"}}]
        }


class TestSuccessor:
    def test_body_parent_and_carried_properties(self):
        client = ReviseFakeClient(
            properties={
                "Name": _title_prop("Report"),
                "Previous": _relation(),
                "Type": {"id": "t", "type": "select", "select": {"name": "Report"}},
                "Action Item": _relation("ai-1"),
                "Creation Date": {"type": "created_time", "created_time": "2026-01-01"},
            },
            blocks=[_para("old")],
        )

        result = revise_page(
            client,
            SOURCE,
            new_markdown="the new content",
            schema=ATOMS_LIKE,
            reason="corrected the figures",
        )

        (payload,) = client.creates
        assert payload["parent"] == {
            "type": "data_source_id",
            "data_source_id": DATA_SOURCE,
        }
        props = payload["properties"]
        assert props["Type"] == {"select": {"name": "Report"}}
        assert props["Action Item"] == {"relation": [{"id": "ai-1"}]}
        assert props["Description"] == {
            "rich_text": [{"type": "text", "text": {"content": "corrected the figures"}}]
        }
        # Computed types Notion rejects on write are not carried.
        assert "Creation Date" not in props
        assert client.text_of(result.canonical_page_id) == ["the new content"]

    def test_a_computed_property_named_in_carry_properties_is_skipped(self):
        """The skip is only *reached* when ``carry_properties`` names one.

        The round-1 test asserted ``"Creation Date" not in props`` under a schema
        whose ``carry_properties`` was ``("Type", "Action Item")`` — so
        ``_carried_properties`` never visited the name and the assertion held for
        the wrong reason. Deleting the branch left the suite green. This schema
        names two computed properties, so the branch is the only thing keeping
        them out of the create payload, and Notion would 400 the whole page
        creation on either of them.
        """
        schema = replace(ATOMS_LIKE, carry_properties=("Type", "Creation Date", "Serial"))
        client = ReviseFakeClient(
            properties={
                "Name": _title_prop("Report"),
                "Previous": _relation(),
                "Type": {"id": "t", "type": "select", "select": {"name": "Report"}},
                "Creation Date": {"type": "created_time", "created_time": "2026-01-01"},
                "Serial": {"type": "unique_id", "unique_id": {"prefix": "A", "number": 7}},
            },
            blocks=[_para("old")],
        )

        revise_page(client, SOURCE, new_markdown="new", schema=schema)

        props = client.creates[0]["properties"]
        assert "Creation Date" not in props
        assert "Serial" not in props
        # The writable neighbour still travels, so this is a skip and not a
        # collapse of the whole carry step.
        assert props["Type"] == {"select": {"name": "Report"}}

    def test_absent_carry_property_is_skipped_not_nulled(self):
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Report"), "Previous": _relation()},
            blocks=[_para("old")],
        )

        revise_page(client, SOURCE, new_markdown="new", schema=ATOMS_LIKE)

        props = client.creates[0]["properties"]
        assert "Type" not in props
        assert "Action Item" not in props

    def test_parent_override_targets_a_data_source(self):
        client = ReviseFakeClient(blocks=[_para("old")])

        revise_page(client, SOURCE, new_markdown="new", parent="other-data-source-id")

        assert client.creates[0]["parent"] == {
            "type": "data_source_id",
            "data_source_id": "otherdatasourceid",
        }

    def test_successor_is_created_and_filled_before_the_old_page_is_touched(self):
        """Failure atomicity: an interruption leaves an orphan, never a dangling link."""
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Report"), "Previous": _relation()},
            blocks=[_para("old")],
        )

        revise_page(client, SOURCE, new_markdown="new", schema=ATOMS_LIKE)

        kinds = [k for k, _ in client.calls]
        first_update = kinds.index("pages.update")
        assert kinds.index("pages.create") < first_update
        assert kinds.index("blocks.append") < first_update

    def test_new_blocks_are_accepted_directly(self):
        client = ReviseFakeClient(blocks=[_para("old")])

        result = revise_page(
            client, SOURCE, new_blocks=[_para("one"), _para("two")]
        )

        assert client.text_of(result.canonical_page_id) == ["one", "two"]

    def test_result_shape(self):
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Report"), "Previous": _relation()},
            blocks=[_para("old")],
        )

        result = revise_page(client, SOURCE, new_markdown="new", schema=ATOMS_LIKE)

        assert isinstance(result, RevisionResult)
        assert result.mode == NEW_CANONICAL
        assert result.archived_page_id == SOURCE_ID
        assert result.canonical_page_id.startswith("page")
        assert result.request_count > 0
        assert result.content_error is None
        # New-canonical puts nothing at risk, so it does not read the discussion.
        assert [c for c in client.calls if c[0] == "comments.list"] == []


# ---------------------------------------------------------------------------
# The version chain — a relation write is a SET, so it must be read-modify-write
# ---------------------------------------------------------------------------


class TestVersionChainNewCanonical:
    """Four consecutive revisions must leave ONE chain, correctly numbered.

    Every test in round 1 revised exactly once, which is the only number of
    revisions at which a chain has nothing to say. ``len(Previous) + 1`` is a
    version number only if ``Previous`` accumulates the whole chain; under a dual
    relation it holds exactly the immediate predecessor, so the count saturated
    and the third revision onward stamped a duplicate ``(v2)``.
    """

    def test_four_consecutive_revisions_build_one_numbered_chain(self):
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Report"), "Previous": _relation()},
            blocks=[_para("body 1")],
        )

        canonical = SOURCE_ID
        superseded: list[str] = []
        for n in range(1, 5):
            result = revise_page(
                client, canonical, new_markdown=f"body {n + 1}", schema=ATOMS_LIKE
            )
            assert result.version == n
            assert result.archived_page_id == canonical
            superseded.append(canonical)
            canonical = result.canonical_page_id

        # Distinct and monotonic: (v1), (v2), (v3), (v4) — not (v1), (v2), (v2).
        titles = [client.title_of(p) for p in superseded]
        assert titles == ["Report (v1)", "Report (v2)", "Report (v3)", "Report (v4)"]
        assert len(set(titles)) == len(titles)

        # Every link resolves, in both directions, and the newest page is the
        # only one without a successor.
        chain = [*superseded, canonical]
        for older, newer in zip(chain, chain[1:]):
            assert client.relation_of(older, "Next") == [newer]
            assert client.relation_of(newer, "Previous") == [older]
        assert client.relation_of(canonical, "Next") == []

        # No orphan: every page this run created is in the chain.
        created = {pid for pid in client._pages if pid.startswith("page")}
        assert created == set(chain[1:])

    def test_an_existing_supersede_link_is_merged_not_replaced(self):
        """Revising an already-superseded page must not unlink its successor.

        A relation write replaces the array, and ``Next``/``Previous`` are dual,
        so overwriting ``Next`` clears the matching ``Previous`` on the page that
        was already linked — silently, on the server, from a payload that never
        mentioned it.
        """
        client = ReviseFakeClient(
            properties={
                "Name": _title_prop("Report"),
                "Previous": _relation(),
                "Next": _relation("earlier"),
            },
            blocks=[_para("old")],
        )
        client._pages["earlier"] = {
            "object": "page",
            "id": "earlier",
            "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE},
            "properties": {"Name": _title_prop("Report (v1)"), "Previous": _relation(SOURCE_ID)},
        }

        result = revise_page(client, SOURCE, new_markdown="new", schema=ATOMS_LIKE)

        ((page_id, prop, targets),) = client.relation_writes()
        assert (page_id, prop) == (SOURCE_ID, "Next")
        assert targets == ["earlier", result.canonical_page_id]
        assert client.relation_of("earlier", "Previous") == [SOURCE_ID]

    def test_a_truncated_relation_refuses_before_any_write(self):
        """Read-modify-write on a truncated array would drop what it cannot see.

        The page object caps a relation at 25 entries and says so with
        ``has_more``; merging into that payload would write back a 25-entry array
        over a longer one. Refuse instead, before the first write.
        """
        client = ReviseFakeClient(
            properties={
                "Name": _title_prop("Report"),
                "Previous": _relation(),
                "Next": _relation("a", has_more=True),
            },
            blocks=[_para("old")],
        )

        with pytest.raises(ValueError, match="truncated"):
            revise_page(client, SOURCE, new_markdown="new", schema=ATOMS_LIKE)

        assert [c for c in client.calls if c[0] != "pages.retrieve"] == []


class TestVersionChainSnapshotInPlace:
    """The hub accumulates its snapshots; nothing it linked may be unlinked.

    This is the mode the chain bug actually bit, by definition: snapshot mode
    exists for a page that gets revised repeatedly, and a relation write that
    replaces the array unlinked the previous snapshot on every single revision —
    in both directions, because the pair is dual — leaving orphans reachable from
    nothing and a version number frozen at 2.
    """

    def test_four_consecutive_snapshots_all_stay_linked(self):
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Package Hub"), "Previous": _relation()},
            blocks=[_para("hub body one"), _para("hub body two")],
        )

        snapshots: list[str] = []
        for n in range(1, 5):
            result = revise_page(
                client,
                SOURCE,
                new_markdown=f"replacement {n}",
                schema=ATOMS_LIKE,
                mode=SNAPSHOT_IN_PLACE,
            )
            assert result.version == n
            assert result.canonical_page_id == SOURCE_ID
            snapshots.append(result.archived_page_id)

            # The hub's Previous grows by exactly one each time, and keeps every
            # earlier entry in order.
            assert client.relation_of(SOURCE_ID, "Previous") == snapshots

        titles = [client.title_of(p) for p in snapshots]
        assert titles == [
            "Package Hub (v1)",
            "Package Hub (v2)",
            "Package Hub (v3)",
            "Package Hub (v4)",
        ]
        assert len(set(titles)) == len(titles)

        # No orphan: every snapshot still points back at the hub through the dual
        # inverse, so each is reachable from the page it belongs to.
        for snapshot in snapshots:
            assert client.relation_of(snapshot, "Next") == [SOURCE_ID]

        # The hub keeps its id and carries only the newest content.
        assert client.text_of(SOURCE_ID) == ["replacement 4"]

    def test_a_truncated_predecessor_relation_refuses_before_any_write(self):
        """The arm guarding the array snapshot mode actually merges into.

        Snapshot mode read-modify-writes the hub's `Previous`, and a page object
        caps a relation at 25 entries and flags the rest with `has_more`. A hub
        whose history has passed the cap therefore arrives already truncated, and
        merging into what came back would write that truncation over the real
        array — through the dual inverse, unlinking every snapshot past the cap at
        both ends, which is the exact loss `_merged_relation` was added to prevent.

        The sibling test in `TestVersionChainNewCanonical` truncates `Next` in
        new-canonical mode and so exercises the other arm only.
        """
        client = ReviseFakeClient(
            properties={
                "Name": _title_prop("Package Hub"),
                "Previous": _relation("older-snapshot", has_more=True),
            },
            blocks=[_para("hub body")],
        )

        with pytest.raises(ValueError, match="truncated") as caught:
            revise_page(
                client,
                SOURCE,
                new_markdown="replacement",
                schema=ATOMS_LIKE,
                mode=SNAPSHOT_IN_PLACE,
            )

        # The refusal names the relation it refused, not just "a relation".
        assert "Previous" in str(caught.value)

        # Nothing was written: no snapshot page, no rewrite of the hub, no
        # relinking — and the refusal beat even the transcript read.
        assert [c for c in client.calls if c[0] in _WRITE_CALLS] == []
        assert [c for c in client.calls if c[0] != "pages.retrieve"] == []
        assert client.creates == []
        assert client.updates == []
        assert client.text_of(SOURCE_ID) == ["hub body"]


# ---------------------------------------------------------------------------
# AC-9 — snapshot-in-place
# ---------------------------------------------------------------------------


class TestSnapshotInPlace:
    def _client(self) -> ReviseFakeClient:
        return ReviseFakeClient(
            properties={"Name": _title_prop("Package Hub"), "Previous": _relation()},
            blocks=[_para("hub body one"), _para("hub body two")],
            comments=[
                _comment("c-1", "check the second line", block_id="blk-2"),
                _comment("c-2", "and the header"),
            ],
        )

    def test_hub_keeps_its_id_and_the_snapshot_carries_the_old_body(self):
        client = self._client()
        old_text = client.text_of(SOURCE_ID)

        result = revise_page(
            client,
            SOURCE,
            new_markdown="the replacement content",
            schema=ATOMS_LIKE,
            mode=SNAPSHOT_IN_PLACE,
        )

        assert result.mode == SNAPSHOT_IN_PLACE
        assert result.canonical_page_id == SOURCE_ID
        assert result.archived_page_id != SOURCE_ID
        snapshot_text = client.text_of(result.archived_page_id)
        assert snapshot_text[: len(old_text)] == old_text
        assert client.text_of(SOURCE_ID) == ["the replacement content"]

    def test_transcript_is_captured_before_the_first_write(self):
        client = self._client()

        result = revise_page(
            client,
            SOURCE,
            new_markdown="replacement",
            schema=ATOMS_LIKE,
            mode=SNAPSHOT_IN_PLACE,
        )

        assert result.discussion_count == 2
        assert result.transcript == (
            "check the second line",
            "and the header",
        )

        kinds = [k for k, _ in client.calls]
        first_read = kinds.index("comments.list")
        writes = [
            i
            for i, k in enumerate(kinds)
            if k in {"pages.create", "pages.update", "blocks.append", "blocks.delete"}
        ]
        assert first_read < min(writes)

    def test_the_transcript_text_lands_on_the_snapshot(self):
        client = self._client()

        result = revise_page(
            client,
            SOURCE,
            new_markdown="replacement",
            schema=ATOMS_LIKE,
            mode=SNAPSHOT_IN_PLACE,
        )

        rendered = "\n".join(client.text_of(result.archived_page_id))
        assert "check the second line" in rendered
        assert "and the header" in rendered
        # The anchor cannot be recreated, so it is recorded as text.
        assert "blk-2" in rendered

    def test_back_link_runs_the_other_way(self):
        client = self._client()

        result = revise_page(
            client,
            SOURCE,
            new_markdown="replacement",
            schema=ATOMS_LIKE,
            mode=SNAPSHOT_IN_PLACE,
        )

        (page_id, prop, targets), = client.relation_writes()
        assert page_id == SOURCE_ID
        assert prop == "Previous"
        assert targets == [result.archived_page_id]

    def test_the_snapshot_is_the_versioned_archived_record(self):
        client = self._client()

        revise_page(
            client,
            SOURCE,
            new_markdown="replacement",
            schema=ATOMS_LIKE,
            mode=SNAPSHOT_IN_PLACE,
        )

        created = client.creates[0]["properties"]
        assert created["Name"] == {
            "title": [{"type": "text", "text": {"content": "Package Hub (v1)"}}]
        }
        assert created["Status"] == {"select": {"name": "Archived"}}
        # The hub page keeps its own title.
        for payload in client.updates_to(SOURCE_ID):
            assert "Name" not in (payload.get("properties") or {})

    def test_the_in_place_rewrite_goes_through_the_override(self):
        """Snapshot mode is the guard's only legitimate internal caller.

        The hub page carries two comments; without ``allow_destructive=True`` the
        guard would refuse the very rewrite this mode exists to perform.
        """
        client = self._client()

        result = revise_page(
            client,
            SOURCE,
            new_markdown="replacement",
            schema=ATOMS_LIKE,
            mode=SNAPSHOT_IN_PLACE,
        )

        assert client.text_of(SOURCE_ID) == ["replacement"]
        assert result.discussion_count == 2
        # Exactly one comments read: the transcript. The republish did not add one.
        assert [c for c in client.calls if c[0] == "comments.list"] == [
            ("comments.list", SOURCE_ID)
        ]

    @pytest.mark.parametrize(
        "block_types",
        [
            *([btype] for btype in sorted(_UNCOPYABLE_BLOCK_TYPES)),
            ["child_page", "child_database"],
        ],
        ids=lambda types: "+".join(types),
    )
    def test_an_uncopyable_block_refuses_before_anything_is_written(self, block_types):
        """A snapshot that cannot carry the content cannot justify the rewrite.

        `_sanitize_block` returns None for every member of
        `_UNCOPYABLE_BLOCK_TYPES`, so the snapshot simply lacked the block — and
        then the `allow_destructive` rewrite deleted it at source. Deleting a
        `child_page` block trashes the child page; deleting a `child_database`
        block trashes the database. The mode that exists to avoid loss was
        trashing sub-pages and reporting it afterwards in `content_error`, on
        exactly the page shape ("an index, a package cover") that holds them.

        Each member gets its own case, as the ONLY uncopyable block on an
        otherwise copyable body, because a fixture carrying two of them proves
        nothing about the other three: the refusal fires on the first type it
        meets, so a set narrowed to `child_page`/`child_database` reads green
        against a fixture that publishes both. The final case keeps the original
        pair, which is what binds the sorted aggregation of several types.

        The membership itself is pinned in the same breath, and it has to be
        pinned *here* rather than in a test of its own: the parametrization reads
        `_UNCOPYABLE_BLOCK_TYPES`, so it stays exhaustive however the set grows —
        but a parametrization that follows the set cannot watch the set shrink.
        Narrow it and the cases narrow with it, every one of them still green,
        while a type the `allow_destructive` rewrite deletes at source quietly
        stops being refused. Asserted inside the case, both directions go red.
        """
        documented = {"child_page", "child_database", "unsupported", "synced_block", "ai_block"}
        assert _UNCOPYABLE_BLOCK_TYPES == frozenset(documented)

        client = ReviseFakeClient(
            properties={"Name": _title_prop("Package Hub"), "Previous": _relation()},
            blocks=[_para("hub body"), *(_uncopyable(btype) for btype in block_types)],
        )
        before = client.block_ids(SOURCE_ID)
        before_text = client.text_of(SOURCE_ID)

        with pytest.raises(IncompleteSnapshotError) as caught:
            revise_page(
                client,
                SOURCE,
                new_markdown="replacement",
                schema=ATOMS_LIKE,
                mode=SNAPSHOT_IN_PLACE,
            )

        # The refusal names what it could not copy, so the caller can act on it.
        assert caught.value.block_types == tuple(sorted(block_types))
        for btype in block_types:
            assert btype in str(caught.value)
        assert caught.value.page_id == SOURCE_ID
        # Nothing was created, so there is not even a half-snapshot to clean up.
        assert caught.value.snapshot_page_id is None

        # Zero writes of any kind, and the hub page is byte-for-byte as it was.
        writes = [c for c in client.calls if c[0] in _WRITE_CALLS]
        assert writes == []
        assert client.block_ids(SOURCE_ID) == before
        assert client.text_of(SOURCE_ID) == before_text
        assert client.updates == []
        assert client.creates == []

    @pytest.mark.parametrize(
        "shape, keep, hub_blocks, phrase",
        [
            ("truncated", 2, 5, "next_cursor"),
            ("truncated", 0, 3, "next_cursor"),
            ("non-dict", 0, 3, "rather than a list envelope"),
            ("results-not-a-list", 0, 3, "no usable 'results'"),
        ],
        ids=[
            "truncated-with-content",
            "truncated-to-empty",
            "non-dict-envelope",
            "results-not-a-list",
        ],
    )
    def test_a_malformed_children_read_refuses_before_anything_is_written(
        self, shape, keep, hub_blocks, phrase
    ):
        """hostile-22: the copy this mode destroys the original on the strength of.

        `_list_children_blocks` used to warn and stop on a `has_more`-without-
        `next_cursor` envelope and to substitute `{}` for a non-dict one, so the
        snapshot silently carried part of the body — or none of it — and the
        `allow_destructive=True` rewrite then deleted the whole body at source.
        Executed against this fixture before the fix: five hub blocks in, two on
        the snapshot, one `replacement` block on the hub, `content_error=None`.

        The truncated-to-empty case is the one that matters: it is hostile-5's
        scenario (fixed in rev2 for the comments reader, which answers "is anyone
        talking about this page") transplanted onto the reader that answers "what
        is on this page before I delete it", where the consequence is not a wrong
        guard verdict but destroyed content.

        A truncated listing is *unknown*, not *empty*. Each shape must therefore
        reach the refusal that already sits before `_create_page`, leaving the hub
        page byte-for-byte as it was.
        """
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Package Hub"), "Previous": _relation()},
            blocks=[_para(f"hub block {i + 1}") for i in range(hub_blocks)],
            children_envelope=_malform_the_first_hub_read(shape, keep=keep),
        )
        before_ids = client.block_ids(SOURCE_ID)
        before_text = client.text_of(SOURCE_ID)

        with pytest.raises(IncompleteSnapshotError) as caught:
            revise_page(
                client,
                SOURCE,
                new_markdown="replacement",
                schema=ATOMS_LIKE,
                mode=SNAPSHOT_IN_PLACE,
            )

        # The refusal names the truncation rather than the block types, so the
        # caller can tell "I cannot copy this" from "I could not read this".
        assert phrase in str(caught.value)
        assert caught.value.page_id == SOURCE_ID
        assert caught.value.block_types == ()
        # Nothing was created, so there is not even a half-snapshot to clean up.
        assert caught.value.snapshot_page_id is None

        # Zero writes of any kind, and the hub page is exactly as it was.
        assert [c for c in client.calls if c[0] in _WRITE_CALLS] == []
        assert client.block_ids(SOURCE_ID) == before_ids
        assert client.text_of(SOURCE_ID) == before_text
        assert client.creates == []
        assert client.updates == []

    def test_an_untyped_block_refuses_like_any_other_uncopyable_one(self):
        """hostile-39: a block the sanitizer cannot classify must not just vanish.

        `_sanitize_block` returned `None` for a block with no `type` *without*
        recording anything in `skipped`, so the uncopyable refusal could not fire
        and the rewrite deleted the block at source. An unclassifiable block is
        the one case where the library has least idea what it is destroying, so it
        is the last case that should be handled by dropping it silently.
        """
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Package Hub"), "Previous": _relation()},
            blocks=[_para("hub body"), {"object": "block"}],
        )
        before_ids = client.block_ids(SOURCE_ID)

        with pytest.raises(IncompleteSnapshotError) as caught:
            revise_page(
                client,
                SOURCE,
                new_markdown="replacement",
                schema=ATOMS_LIKE,
                mode=SNAPSHOT_IN_PLACE,
            )

        assert caught.value.block_types == (_UNTYPED_BLOCK,)
        assert _UNTYPED_BLOCK in str(caught.value)
        assert caught.value.snapshot_page_id is None
        assert [c for c in client.calls if c[0] in _WRITE_CALLS] == []
        assert client.block_ids(SOURCE_ID) == before_ids

    def test_a_genuinely_empty_hub_page_snapshots_without_a_refusal(self):
        """hostile-23: `if snapshot_body:` is gone, and an empty read is a fact now.

        The short-circuit was defensible only if an empty read always meant an
        empty page — which is precisely what hostile-22 showed it did not. With
        the reader strict, an empty body reaching the publish means the source
        genuinely has none, so the publish is unconditional (the same argument
        rev2 used to delete `if blocks:` from the successor path) and the
        revision proceeds normally.
        """
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Package Hub"), "Previous": _relation()},
            blocks=[],
        )

        result = revise_page(
            client,
            SOURCE,
            new_markdown="replacement",
            schema=ATOMS_LIKE,
            mode=SNAPSHOT_IN_PLACE,
            capture_transcript=False,
        )

        assert result.canonical_page_id == SOURCE_ID
        assert result.content_error is None
        assert client.text_of(result.archived_page_id) == []
        assert client.text_of(SOURCE_ID) == ["replacement"]

    def test_a_partial_snapshot_publish_stops_before_the_rewrite(self):
        """D-5's own ordering principle: check before the irreversible write.

        `publish_block_tree` onto the snapshot can come back `partial` — a nested
        sub-tree never landed. The old code noted that in `content_error` and
        republished over the source anyway, so the content that failed to copy was
        then destroyed at its only remaining location.
        """
        client = ReviseFakeClient(
            properties={"Name": _title_prop("Package Hub"), "Previous": _relation()},
            blocks=[_toggle("outer", [_toggle("mid", [_toggle("inner", [_para("leaf")])])])],
            drop_append_ids=True,
        )
        before = client.block_ids(SOURCE_ID)

        with pytest.raises(IncompleteSnapshotError) as caught:
            revise_page(
                client,
                SOURCE,
                new_markdown="replacement",
                schema=ATOMS_LIKE,
                mode=SNAPSHOT_IN_PLACE,
            )

        # The snapshot page is named and left in place: deleting a page to tidy up
        # after a refusal is the behaviour this module exists to avoid.
        snapshot_id = caught.value.snapshot_page_id
        assert snapshot_id is not None
        assert snapshot_id in str(caught.value)
        assert snapshot_id in client._pages

        # The hub received no write at all.
        assert client.block_ids(SOURCE_ID) == before
        assert [c for c in client.calls if c[0] in _WRITE_CALLS and c[1] == SOURCE_ID] == []
        assert client.updates == []

    def test_capture_transcript_false_skips_the_read(self):
        client = self._client()

        result = revise_page(
            client,
            SOURCE,
            new_markdown="replacement",
            schema=ATOMS_LIKE,
            mode=SNAPSHOT_IN_PLACE,
            capture_transcript=False,
        )

        assert result.discussion_count == 0
        assert result.transcript == ()
        assert [c for c in client.calls if c[0] == "comments.list"] == []
