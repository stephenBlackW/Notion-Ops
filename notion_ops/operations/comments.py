"""Read-only access to a page's comment threads (nops-cycle-3).

Notion's Comments API is the only Notion-native, workspace-agnostic answer to
"has a human said something about this page?" — which is exactly the question a
destructive content rewrite has to ask before it rewrites anything (ISS-029).

**Scope, and it is load-bearing.** ``GET /v1/comments`` returns **un-resolved**
comments only. :meth:`CommentOperations.has_discussion` therefore means "carries
an *open* discussion", never "has ever been discussed": a page whose every thread
was resolved reads as un-discussed here. Callers that treat this as a safety
signal are protecting live conversations, not archived ones.

The endpoint takes a **block id**, and a page id is a valid block id, so one call
covers both the page-level threads and the block-anchored threads on that page's
own blocks.

Read-only by design. The API can *create* a page-level comment or reply into an
existing thread, but it cannot move a comment, re-anchor one, or originate a
block-anchored thread — so there is no write operation here that would let a
caller believe a discussion had been carried across a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from notion_ops.client import AsyncNotionOps, NotionOps


@dataclass(frozen=True)
class Discussion:
    """One comment on a page, flattened to the fields a guard or a transcript needs.

    ``parent_block_id`` is the id of the block the comment is anchored to, or
    ``None`` for a page-level thread. ``discussion_id`` groups the comments that
    belong to the same thread. ``plain_text`` is the comment's rich text
    concatenated — enough to transcribe, not enough to re-render formatting.
    """

    id: str
    discussion_id: str
    parent_block_id: str | None
    created_by: str | None
    plain_text: str


def _to_discussion(payload: dict[str, Any]) -> Discussion:
    """Map one API comment object to a :class:`Discussion`.

    Every field is read defensively: the comment object's exact shape was
    documented but not re-verified against a live populated thread, so a missing
    or renamed key degrades one field rather than raising.
    """
    parent = payload.get("parent") or {}
    parent_block_id = parent.get("block_id") if parent.get("type") == "block_id" else None
    created_by = (payload.get("created_by") or {}).get("id")
    spans = payload.get("rich_text") or []
    plain_text = "".join(
        span.get("plain_text", "") for span in spans if isinstance(span, dict)
    )
    return Discussion(
        id=str(payload.get("id", "")),
        discussion_id=str(payload.get("discussion_id", "")),
        parent_block_id=parent_block_id,
        created_by=created_by,
        plain_text=plain_text,
    )


def list_discussions(client: Any, block_id: str) -> list[Discussion]:
    """List the open discussions on *block_id*, paginated and retry-wrapped.

    Goes through the raw SDK (``client.api``) rather than the operations layer so
    that :mod:`notion_ops.utils.publish`'s guard can call it with the same
    duck-typed ``client`` the publisher already accepts.
    ``CommentOperations.list`` is the public sugar over this function.
    """
    raise NotImplementedError


async def list_discussions_async(client: Any, block_id: str) -> list[Discussion]:
    """Async twin of :func:`list_discussions`."""
    raise NotImplementedError


class CommentOperations:
    """Read operations for Notion comments."""

    def __init__(self, client: "NotionOps"):
        self._client = client

    def list(self, block_id: str) -> list[Discussion]:
        """Return the **un-resolved** comments on a page or block.

        Args:
            block_id: A page id or block id (a page id is a valid block id, and
                passing one covers the page-level threads plus the block-anchored
                threads on that page's own blocks).

        Returns:
            The comments in API order, oldest first. Empty when the page carries
            no open discussion.

        Raises:
            PermissionError: The integration lacks the read-comments capability.
        """
        raise NotImplementedError

    def has_discussion(self, block_id: str) -> bool:
        """True when *block_id* carries at least one **un-resolved** comment."""
        raise NotImplementedError


class AsyncCommentOperations:
    """Async read operations for Notion comments."""

    def __init__(self, client: "AsyncNotionOps") -> None:
        self._client = client

    async def list(self, block_id: str) -> list[Discussion]:
        """Return the **un-resolved** comments on a page or block (async)."""
        raise NotImplementedError

    async def has_discussion(self, block_id: str) -> bool:
        """True when *block_id* carries at least one un-resolved comment (async)."""
        raise NotImplementedError


__all__ = [
    "Discussion",
    "CommentOperations",
    "AsyncCommentOperations",
    "list_discussions",
    "list_discussions_async",
]