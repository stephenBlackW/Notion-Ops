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
from dataclasses import dataclass
from typing import Any

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
        reason: Free text recorded on the successor via
            ``schema.reason_property``, when that name is set.
        parent: Data source id for the created page. Defaults to the source
            page's own parent, so the successor lands beside the original.
        capture_transcript: Read the page's open discussions before writing and
            return them on the result. Snapshot-in-place mode is where this
            matters — it is the only record that survives the rewrite.

    Returns:
        A :class:`RevisionResult`.

    Raises:
        ValueError: Neither or both of ``new_markdown``/``new_blocks``; an
            unknown ``mode``; or ``mode="snapshot-in-place"`` without
            ``schema.predecessor_property`` (which would orphan the snapshot).
        NotFoundError: ``page_id`` cannot be read.
        OversizedContentError: Propagated from the markdown conversion.

    A re-run is **not idempotent** — revising twice legitimately means two
    versions — but it is never destructive: the superseded page's blocks are
    untouched in new-canonical mode, and the version number is derived from the
    live predecessor chain rather than a stored counter, so a re-run after a
    partial failure produces ``(vN+1)`` and a consistent chain. The successor is
    created and populated **before** the original is retitled or relinked, so an
    interruption leaves an orphan successor and an untouched original, never a
    relinked original pointing at nothing.
    """
    raise NotImplementedError


__all__ = [
    "RevisionSchema",
    "RevisionResult",
    "revise_page",
    "NEW_CANONICAL",
    "SNAPSHOT_IN_PLACE",
]
