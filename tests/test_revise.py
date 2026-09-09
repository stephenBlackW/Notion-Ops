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
from typing import Any

import pytest

from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.publish import _children_of, _without_children
from notion_ops.utils.revise import (
    NEW_CANONICAL,
    SNAPSHOT_IN_PLACE,
    RevisionResult,
    RevisionSchema,
    revise_page,
)

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


def _title_prop(text: str) -> dict[str, Any]:
    return {
        "id": "title",
        "type": "title",
        "title": [{"type": "text", "text": {"content": text}, "plain_text": text}],
    }


def _relation(*ids: str) -> dict[str, Any]:
    return {"type": "relation", "relation": [{"id": i} for i in ids], "has_more": False}


class ReviseFakeClient:
    """A fake SDK with pages, blocks and comments, and an ordered call log.

    ``calls`` is the sequence of ``(operation, target_id)`` pairs, which is what
    the ordering assertions read: "the comments read happened before the first
    write" is a statement about this list, not about a mock's call count.
    """

    def __init__(
        self,
        *,
        properties: dict[str, Any] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        comments: list[dict[str, Any]] | None = None,
        parent: dict[str, Any] | None = None,
    ) -> None:
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
                    "properties": copy.deepcopy(payload.get("properties", {})),
                }
                client._children.setdefault(new_id, [])
                return copy.deepcopy(client._pages[new_id])

            def update(self, *, page_id: str, **payload: Any) -> dict[str, Any]:
                client.calls.append(("pages.update", page_id))
                client.updates.append((page_id, copy.deepcopy(payload)))
                stored = client._pages.setdefault(page_id, {"id": page_id, "properties": {}})
                stored.setdefault("properties", {}).update(payload.get("properties", {}))
                for flag in ("archived", "in_trash"):
                    if flag in payload:
                        stored[flag] = payload[flag]
                return copy.deepcopy(stored)

        class _Children:
            def append(self, *, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
                client.calls.append(("blocks.append", block_id))
                ids = client._insert(block_id, children)
                return {"results": [{"id": i, "type": client._nodes[i].get("type")} for i in ids]}

            def list(
                self,
                *,
                block_id: str,
                page_size: int = 100,
                start_cursor: str | None = None,
            ) -> dict[str, Any]:
                client.calls.append(("blocks.list", block_id))
                kids = client._children.get(block_id, [])
                return {
                    "results": [client._apiify(i) for i in kids],
                    "has_more": False,
                    "next_cursor": None,
                }

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
        stored = client._pages[SOURCE_ID]["properties"]
        assert stored["Name"] == {
            "title": [{"type": "text", "text": {"content": "Report (v2)"}}]
        }
        assert stored["Status"] == {"select": {"name": "Archived"}}

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

        stored = client._pages[SOURCE_ID]["properties"]
        assert "Status" not in stored
        assert stored["Name"] == {
            "title": [{"type": "text", "text": {"content": "Report (v1)"}}]
        }

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
        assert client._pages[SOURCE_ID]["properties"]["Name"] == {
            "title": [{"type": "text", "text": {"content": "Report (v3)"}}]
        }
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
