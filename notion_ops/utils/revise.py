"""Versioned page revision — publish new content without destroying the old page.

Notion has no "replace this page's body" operation, so the obvious way to change
what a published page says is to clear its blocks and append new ones. That is
also the way to destroy every block-anchored comment on it: the anchors are block
ids, the ids are gone, and the API offers no way to move a comment or re-anchor
one (ISS-029).

:func:`revise_page` is the non-destructive alternative. It creates a **successor**
page carrying the new content, links the two with the caller's own supersede
relation, and leaves the superseded page's blocks — and therefore its
discussion — exactly where they were.

Schema-free by construction
---------------------------
Property names are a *workspace* convention, not a Notion primitive, so none are
baked in here. :class:`RevisionSchema` carries the names, every relation/status
field defaults to ``None`` meaning "skip that step", and a caller who supplies
nothing gets create-successor-and-return-ids. The AgenticOS binding of these
names lives in that workspace's own code, not in this library.

Two modes, and the difference matters
-------------------------------------
``mode="new-canonical"`` (the default) suits a **leaf** document — a report,
a note, anything referenced by being read. The successor becomes the canonical
page; the original keeps its id, its blocks, its comments **and its anchors** as
the ``(vN)`` record. Nothing is destroyed.

``mode="snapshot-in-place"`` suits a **hub** page — an index, a package cover,
anything referenced *by id* from across the workspace, where churning the id
would silently break inbound relations and ``@``-mentions. The hub page keeps its
id and is rewritten in place; the old body is copied onto a newly created
``(vN)`` snapshot. It is honest about its cost: **rewriting the hub page detaches
its block-anchored comments**, because the blocks they point at are replaced. The
API cannot move them. So this mode reads the discussion *before* it writes and
publishes the transcript onto the snapshot, so the text survives next to the
content it was about — and it is an explicit opt-in, never a default.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from notion_client import APIResponseError

from notion_ops.exceptions import map_api_error
from notion_ops.operations.comments import Discussion, list_discussions
from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.markdown import markdown_to_blocks
from notion_ops.utils.publish import (
    _list_children_blocks,
    publish_block_tree,
    republish_block_tree,
)
from notion_ops.utils.responses import sync_dict
from notion_ops.utils.retry import retry_on_transient

logger = logging.getLogger(__name__)

#: Modes :func:`revise_page` accepts.
NEW_CANONICAL = "new-canonical"
SNAPSHOT_IN_PLACE = "snapshot-in-place"

#: Trailing ``" (vN)"`` version suffix, stripped before a new one is applied so a
#: page revised twice reads ``"Report (v2)"`` and not ``"Report (v1) (v2)"``.
_VERSION_SUFFIX = re.compile(r"\s*\(v\d+\)\s*$")

#: Property types Notion computes and rejects on write. Carrying one across to a
#: successor page would 400 the whole page creation.
_COMPUTED_PROPERTY_TYPES = frozenset(
    {
        "formula",
        "rollup",
        "created_time",
        "last_edited_time",
        "created_by",
        "last_edited_by",
        "unique_id",
        "button",
    }
)


@dataclass(frozen=True)
class RevisionSchema:
    """The property **names** a revision writes, for one caller's workspace.

    Every relation/status field defaults to ``None``, which means "skip that
    step" rather than "use a default name": a library default of ``"Next"`` would
    be one workspace's convention wearing a generic costume, and a caller in
    another workspace would get an opaque 400 instead of a clear no-op.

    Attributes:
        title_property: Name of the title property. Used to read the source
            page's title and to write the superseded page's ``(vN)`` title.
        supersede_property: Relation on the **superseded** page pointing forward
            to its successor (new-canonical mode). ``None`` skips the link.
        predecessor_property: Relation pointing **back** to an earlier version.
            Read to derive the version number, and written on the hub page in
            snapshot-in-place mode (which requires it).
        status_property: Select property to stamp on the superseded page.
        archived_status_value: The select value written to ``status_property``.
            Both must be set for the status write to happen.
        reason_property: Rich-text property on the **successor** carrying the
            caller's ``reason``.
        version_title_template: Format string for the superseded page's title.
            Receives ``title`` (with any prior ``(vN)`` suffix stripped) and
            ``n``.
        carry_properties: Property names copied verbatim from the source page
            onto the successor. Names absent from the source, and computed types
            Notion rejects on write, are skipped.
    """

    title_property: str = "Name"
    supersede_property: str | None = None
    predecessor_property: str | None = None
    status_property: str | None = None
    archived_status_value: str | None = None
    reason_property: str | None = None
    version_title_template: str = "{title} (v{n})"
    carry_properties: tuple[str, ...] = ()


@dataclass(frozen=True)
class RevisionResult:
    """Outcome of a :func:`revise_page` call.

    ``canonical_page_id`` is the page that now holds the new content, and
    ``archived_page_id`` the one holding the old — which of the two is the
    *original* depends on the mode, so neither is called ``id``.

    Attributes:
        canonical_page_id: The page carrying the new content. In new-canonical
            mode this is the freshly created successor; in snapshot-in-place mode
            it is the original page id, unchanged.
        archived_page_id: The page carrying the superseded content. In
            new-canonical mode this is the original; in snapshot-in-place mode it
            is the freshly created snapshot.
        version: The version number stamped on the superseded page.
        mode: The mode this revision ran in.
        request_count: API requests issued by this call.
        discussion_count: Open comments found on the source page. Always ``0``
            when ``capture_transcript=False`` or in new-canonical mode, where the
            discussion is never at risk and is not read.
        transcript: The text of those comments, captured **before** any write.
        content_error: Set when the successor's body published only partially
            (:class:`~notion_ops.utils.publish.PublishResult` reported
            ``partial``). The page exists — do not retry the revision.
    """

    canonical_page_id: str
    archived_page_id: str
    version: int
    mode: str
    request_count: int
    discussion_count: int = 0
    transcript: tuple[str, ...] = ()
    content_error: str | None = None


class _Requests:
    """A tally of the API requests one :func:`revise_page` call issues."""

    def __init__(self) -> None:
        self.count = 0

    def bump(self, n: int = 1) -> None:
        self.count += n


@retry_on_transient
def _retrieve_page(client: Any, page_id: str) -> dict[str, Any]:
    """Read a page, retry-wrapped, mapping API errors to the library's types."""
    try:
        return sync_dict(client.api.pages.retrieve(page_id=page_id))
    except APIResponseError as e:
        raise map_api_error(e, resource_type="Page", resource_id=page_id) from e


@retry_on_transient
def _create_page(
    client: Any,
    parent: dict[str, Any],
    properties: dict[str, Any],
) -> dict[str, Any]:
    """Create a page, retry-wrapped."""
    try:
        return sync_dict(client.api.pages.create(parent=parent, properties=properties))
    except APIResponseError as e:
        target = str(parent.get(str(parent.get("type", "")), ""))
        raise map_api_error(e, resource_type="Page", resource_id=target) from e


@retry_on_transient
def _update_page(client: Any, page_id: str, properties: dict[str, Any]) -> None:
    """Write page properties, retry-wrapped.

    Only ``properties`` is ever sent. ``archived`` / ``in_trash`` are deliberately
    unreachable from here (D-6): trashing a superseded page would take its
    discussion out of view, which is precisely the loss this module exists to
    prevent. "Archived" here is a *status value*, a label on a live page.
    """
    try:
        client.api.pages.update(page_id=page_id, properties=properties)
    except APIResponseError as e:
        raise map_api_error(e, resource_type="Page", resource_id=page_id) from e


def _title_text(properties: dict[str, Any], name: str) -> str:
    """The plain text of a title property, or ``""`` when absent or empty."""
    spans = (properties.get(name) or {}).get("title") or []
    return "".join(
        span.get("plain_text", "") for span in spans if isinstance(span, dict)
    )


def _clean_title(raw: str) -> str:
    """*raw* with one trailing ``" (vN)"`` removed, so versions do not compound."""
    return _VERSION_SUFFIX.sub("", raw).strip() or raw


def _title_value(text: str) -> dict[str, Any]:
    """A title property in API write shape."""
    return {"title": [{"type": "text", "text": {"content": text}}]}


def _rich_text_value(text: str) -> dict[str, Any]:
    """A rich-text property in API write shape."""
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


def _version_number(properties: dict[str, Any], predecessor_property: str | None) -> int:
    """``len(predecessor chain) + 1``, read live off the page, never stored.

    A counter kept anywhere else drifts after a partial failure; the relation
    chain is the truth, and an empty chain is version 1.
    """
    if not predecessor_property:
        return 1
    chain = (properties.get(predecessor_property) or {}).get("relation") or []
    return len(chain) + 1


def _parent_payload(parent: str | None, page: dict[str, Any]) -> dict[str, Any]:
    """The ``parent`` argument for ``pages.create``.

    An explicit *parent* is read as a **data source id**, the parent kind page
    creation takes under the data-sources API. With no override the source page's
    own parent is reused verbatim, so the successor lands beside the original
    whatever kind of container that is.
    """
    if parent:
        return {
            "type": "data_source_id",
            "data_source_id": extract_notion_id(parent),
        }
    source_parent = page.get("parent") or {}
    kind = source_parent.get("type")
    if kind and kind in source_parent:
        return {"type": kind, kind: source_parent[kind]}
    raise ValueError(
        f"cannot derive a parent for the new page from {source_parent!r}; "
        f"pass parent=<data source id> explicitly"
    )


def _carried_properties(
    properties: dict[str, Any],
    names: tuple[str, ...],
) -> dict[str, Any]:
    """The subset of *properties* named by *names*, in API write shape.

    A name absent from the source is skipped rather than written as null, and a
    computed type is skipped because Notion rejects it on write — which would
    fail the whole page creation over a field the caller never meant to set.
    """
    carried: dict[str, Any] = {}
    for name in names:
        prop = properties.get(name)
        if not isinstance(prop, dict):
            continue
        kind = prop.get("type")
        if not isinstance(kind, str) or kind not in prop:
            continue
        if kind in _COMPUTED_PROPERTY_TYPES:
            logger.debug("Not carrying computed property %r (%s) to the successor", name, kind)
            continue
        carried[name] = {kind: prop[kind]}
    return carried


def _text_block(text: str, block_type: str = "paragraph") -> dict[str, Any]:
    """One API-format text block, built without a markdown round trip."""
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _transcript_blocks(discussions: Sequence[Discussion]) -> list[dict[str, Any]]:
    """The captured discussion, rendered as blocks for the snapshot page.

    The API can neither move a comment nor originate a block-anchored one, so the
    anchor itself cannot be reproduced — it is recorded as text beside the
    comment, which is the most an API-realizable transcript can honestly claim.
    """
    if not discussions:
        return []
    heading = (
        f"Discussion transcript captured at revision "
        f"({len(discussions)} open comment(s))"
    )
    note = (
        "Copied from the page this snapshot supersedes. The Notion API cannot move "
        "or re-anchor a comment, so the threads themselves stay on that page and "
        "only their text is reproduced here."
    )
    blocks = [
        {"object": "block", "type": "divider", "divider": {}},
        _text_block(heading, "heading_2"),
        _text_block(note),
    ]
    for discussion in discussions:
        where = (
            f"anchored to block {discussion.parent_block_id}"
            if discussion.parent_block_id
            else "page-level"
        )
        author = discussion.created_by or "unknown author"
        blocks.append(_text_block(f"[{where}, by {author}] {discussion.plain_text}"))
    return blocks


#: Keys the API returns inside a block body that it will not accept back.
_READ_ONLY_BLOCK_BODY_KEYS = frozenset({"id", "created_time", "last_edited_time"})

#: Block types the API returns but cannot re-create from their own payload. A
#: snapshot copy drops them and names them in ``content_error`` rather than
#: failing the whole revision over content it was never able to copy.
_UNCOPYABLE_BLOCK_TYPES = frozenset(
    {"child_page", "child_database", "unsupported", "synced_block", "ai_block"}
)


def _sanitize_rich_text(spans: Any) -> list[Any]:
    """Strip the API-only fields (``plain_text``, ``href``) off a rich-text array."""
    out: list[Any] = []
    for span in spans or []:
        if isinstance(span, dict):
            out.append({k: v for k, v in span.items() if k not in ("plain_text", "href")})
        else:
            out.append(span)
    return out


def _sanitize_block(block: dict[str, Any], skipped: set[str]) -> dict[str, Any] | None:
    """An API-returned block reduced to something ``children.append`` accepts.

    Returns ``None`` for a block type the API cannot re-create from its own
    payload; the type is recorded in *skipped* so the caller can say what did not
    make it onto the snapshot.
    """
    btype = block.get("type", "")
    if not btype or btype in _UNCOPYABLE_BLOCK_TYPES:
        if btype:
            skipped.add(btype)
        return None
    body = block.get(btype)
    if not isinstance(body, dict):
        return {"object": "block", "type": btype, btype: {}}

    clean: dict[str, Any] = {}
    for key, value in body.items():
        if key in _READ_ONLY_BLOCK_BODY_KEYS or key == "children":
            continue
        if key in ("rich_text", "caption"):
            clean[key] = _sanitize_rich_text(value)
        elif key == "cells" and isinstance(value, list):
            clean[key] = [_sanitize_rich_text(cell) for cell in value]
        else:
            clean[key] = value
    return {"object": "block", "type": btype, btype: clean}


def _copy_page_body(
    client: Any,
    page_id: str,
    requests: _Requests,
    skipped: set[str],
) -> list[dict[str, Any]]:
    """Read *page_id*'s block tree and return a re-publishable copy of it."""
    children = _list_children_blocks(client, page_id)
    requests.bump()
    copied: list[dict[str, Any]] = []
    for block in children:
        clean = _sanitize_block(block, skipped)
        if clean is None:
            continue
        if block.get("has_children") and block.get("id"):
            nested = _copy_page_body(client, block["id"], requests, skipped)
            if nested:
                clean[clean["type"]]["children"] = nested
        copied.append(clean)
    return copied


def _resolve_content(
    new_markdown: str | None,
    new_blocks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """The new body as blocks, with exactly one of the two inputs required."""
    if (new_markdown is None) == (new_blocks is None):
        raise ValueError(
            "revise_page requires exactly one of new_markdown or new_blocks"
        )
    if new_markdown is not None:
        return markdown_to_blocks(new_markdown)
    return list(new_blocks or [])


def _read_transcript(
    client: Any,
    page_id: str,
    requests: _Requests,
) -> list[Discussion]:
    """The page's open discussions, read **before** anything is written."""
    discussions = list_discussions(client, page_id)
    requests.bump()
    return discussions


def _superseded_title_properties(
    schema: RevisionSchema,
    title: str,
    version: int,
) -> dict[str, Any]:
    """Title (and status, when the schema names one) for the superseded page."""
    properties: dict[str, Any] = {
        schema.title_property: _title_value(
            schema.version_title_template.format(title=title, n=version)
        )
    }
    if schema.status_property and schema.archived_status_value:
        properties[schema.status_property] = {
            "select": {"name": schema.archived_status_value}
        }
    return properties


def revise_page(
    client: Any,
    page_id: str,
    *,
    new_markdown: str | None = None,
    new_blocks: list[dict[str, Any]] | None = None,
    schema: RevisionSchema = RevisionSchema(),
    mode: str = NEW_CANONICAL,
    reason: str | None = None,
    parent: str | None = None,
    capture_transcript: bool = True,
) -> RevisionResult:
    """Publish *new content* as a new version, without destroying the old page.

    Args:
        client: A ``NotionOps``-like client (only ``client.api`` is used).
        page_id: The page being revised. A URL or a bare id.
        new_markdown: The new content as markdown. Mutually exclusive with
            ``new_blocks``; exactly one is required.
        new_blocks: The new content as an API-format block list.
        schema: The property names to write. See :class:`RevisionSchema`.
        mode: ``"new-canonical"`` (default) or ``"snapshot-in-place"``.
        reason: Free text recorded on the page carrying the new content, via
            ``schema.reason_property``. Ignored when that name is unset.
        parent: Data source id for the created page. Defaults to the source
            page's own parent, so the new page lands beside the original.
        capture_transcript: Read the page's open discussions before writing.
            Only ``snapshot-in-place`` reads them at all — that is the mode where
            the discussion is at risk and the transcript is the only record that
            survives. New-canonical touches no block a comment is anchored to, so
            it spends no request asking.

    Returns:
        A :class:`RevisionResult`.

    Raises:
        ValueError: Neither or both of ``new_markdown``/``new_blocks``; an
            unknown ``mode``; ``mode="snapshot-in-place"`` without
            ``schema.predecessor_property`` (which would orphan the snapshot); or
            a source page whose parent cannot be derived.
        NotFoundError: ``page_id`` cannot be read.
        OversizedContentError: Propagated from the markdown conversion.

    A re-run is **not idempotent** — revising twice legitimately means two
    versions — but it is never destructive: in new-canonical mode the superseded
    page's blocks are untouched, and the version number is derived from the live
    predecessor chain rather than a stored counter, so a re-run after a partial
    failure produces ``(vN+1)`` and a consistent chain rather than a duplicate.
    The new page is created and populated **before** the original is retitled or
    relinked, so an interruption leaves an orphan page and an untouched original
    — recoverable by hand, never lossy — rather than a relinked original pointing
    at nothing.
    """
    if mode not in (NEW_CANONICAL, SNAPSHOT_IN_PLACE):
        raise ValueError(
            f"unknown mode {mode!r}; expected {NEW_CANONICAL!r} or {SNAPSHOT_IN_PLACE!r}"
        )
    if mode == SNAPSHOT_IN_PLACE and schema.predecessor_property is None:
        raise ValueError(
            "mode='snapshot-in-place' needs schema.predecessor_property: without a "
            "back-link from the page to its snapshot, the snapshot is orphaned"
        )
    blocks = _resolve_content(new_markdown, new_blocks)

    source_id = extract_notion_id(page_id)
    requests = _Requests()

    page = _retrieve_page(client, source_id)
    requests.bump()
    properties = page.get("properties") or {}
    title = _clean_title(_title_text(properties, schema.title_property))
    version = _version_number(properties, schema.predecessor_property)
    new_page_parent = _parent_payload(parent, page)

    if mode == NEW_CANONICAL:
        return _revise_new_canonical(
            client,
            source_id=source_id,
            blocks=blocks,
            properties=properties,
            title=title,
            version=version,
            new_page_parent=new_page_parent,
            schema=schema,
            reason=reason,
            requests=requests,
        )
    return _revise_snapshot_in_place(
        client,
        source_id=source_id,
        blocks=blocks,
        properties=properties,
        title=title,
        version=version,
        new_page_parent=new_page_parent,
        schema=schema,
        reason=reason,
        capture_transcript=capture_transcript,
        requests=requests,
    )


def _revise_new_canonical(
    client: Any,
    *,
    source_id: str,
    blocks: list[dict[str, Any]],
    properties: dict[str, Any],
    title: str,
    version: int,
    new_page_parent: dict[str, Any],
    schema: RevisionSchema,
    reason: str | None,
    requests: _Requests,
) -> RevisionResult:
    """LEAF: the successor becomes canonical; the original is left intact.

    Nothing here writes a block on the original page, so its comments keep both
    their text and their anchors — the property ISS-029 lost.
    """
    successor_properties = _carried_properties(properties, schema.carry_properties)
    successor_properties[schema.title_property] = _title_value(title)
    if schema.reason_property and reason:
        successor_properties[schema.reason_property] = _rich_text_value(reason)

    successor = _create_page(client, new_page_parent, successor_properties)
    requests.bump()
    successor_id = extract_notion_id(str(successor.get("id", "")))

    content_error: str | None = None
    if blocks:
        published = publish_block_tree(client, successor_id, blocks)
        requests.bump(published.request_count)
        if published.partial:
            content_error = (
                f"{published.skipped_followups} nested append(s) were skipped while "
                f"publishing the successor page {successor_id}; the page exists — "
                f"do not retry the revision"
            )

    # The supersede link first: it is the machine-readable half, so an
    # interruption after it leaves a navigable chain rather than an orphan
    # labelled "(vN)" that points nowhere.
    if schema.supersede_property:
        _update_page(
            client,
            source_id,
            {schema.supersede_property: {"relation": [{"id": successor_id}]}},
        )
        requests.bump()

    _update_page(
        client,
        source_id,
        _superseded_title_properties(schema, title, version),
    )
    requests.bump()

    return RevisionResult(
        canonical_page_id=successor_id,
        archived_page_id=source_id,
        version=version,
        mode=NEW_CANONICAL,
        request_count=requests.count,
        content_error=content_error,
    )


def _revise_snapshot_in_place(
    client: Any,
    *,
    source_id: str,
    blocks: list[dict[str, Any]],
    properties: dict[str, Any],
    title: str,
    version: int,
    new_page_parent: dict[str, Any],
    schema: RevisionSchema,
    reason: str | None,
    capture_transcript: bool,
    requests: _Requests,
) -> RevisionResult:
    """HUB: the page keeps its id; its old body is snapshotted to a new page.

    Honest about its cost: replacing the page's blocks detaches every comment
    anchored to them, and no API call can re-attach one. So the discussion is
    read **before** the first write and published onto the snapshot, where the
    text at least sits beside the content it was about.
    """
    discussions: list[Discussion] = []
    if capture_transcript:
        discussions = _read_transcript(client, source_id, requests)

    skipped_types: set[str] = set()
    old_body = _copy_page_body(client, source_id, requests, skipped_types)

    snapshot_properties = _carried_properties(properties, schema.carry_properties)
    snapshot_properties.update(_superseded_title_properties(schema, title, version))

    snapshot = _create_page(client, new_page_parent, snapshot_properties)
    requests.bump()
    snapshot_id = extract_notion_id(str(snapshot.get("id", "")))

    notes: list[str] = []
    if skipped_types:
        notes.append(
            f"block type(s) {sorted(skipped_types)} could not be copied onto the "
            f"snapshot {snapshot_id}: the API cannot re-create them from their own "
            f"payload"
        )

    snapshot_body = old_body + _transcript_blocks(discussions)
    if snapshot_body:
        published = publish_block_tree(client, snapshot_id, snapshot_body)
        requests.bump(published.request_count)
        if published.partial:
            notes.append(
                f"{published.skipped_followups} nested append(s) were skipped while "
                f"publishing the snapshot {snapshot_id}"
            )

    # The only legitimate in-library use of the override: this rewrite is exactly
    # what the guard exists to stop by accident, and exactly what this mode is
    # for on purpose. Everything recoverable has already been recovered above.
    republished = republish_block_tree(
        client, source_id, blocks, allow_destructive=True
    )
    requests.bump(republished.request_count)
    if republished.partial:
        notes.append(
            f"{republished.skipped_followups} nested append(s) were skipped while "
            f"rewriting {source_id}"
        )

    hub_properties: dict[str, Any] = {}
    if schema.predecessor_property:
        hub_properties[schema.predecessor_property] = {
            "relation": [{"id": snapshot_id}]
        }
    if schema.reason_property and reason:
        hub_properties[schema.reason_property] = _rich_text_value(reason)
    if hub_properties:
        _update_page(client, source_id, hub_properties)
        requests.bump()

    return RevisionResult(
        canonical_page_id=source_id,
        archived_page_id=snapshot_id,
        version=version,
        mode=SNAPSHOT_IN_PLACE,
        request_count=requests.count,
        discussion_count=len(discussions),
        transcript=tuple(d.plain_text for d in discussions),
        content_error="; ".join(notes) or None,
    )


__all__ = [
    "RevisionSchema",
    "RevisionResult",
    "revise_page",
    "NEW_CANONICAL",
    "SNAPSHOT_IN_PLACE",
]
