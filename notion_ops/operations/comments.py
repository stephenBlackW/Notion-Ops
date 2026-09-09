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

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from notion_client import APIResponseError

from notion_ops.exceptions import NotionOpsError, map_api_error
from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.responses import sync_dict
from notion_ops.utils.retry import retry_on_transient_api, retry_on_transient_api_async

logger = logging.getLogger(__name__)

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


def _list_params(block_id: str, cursor: str | None) -> dict[str, Any]:
    """Query parameters for one ``GET /v1/comments`` page."""
    params: dict[str, Any] = {"block_id": block_id, "page_size": 100}
    if cursor:
        params["start_cursor"] = cursor
    return params


def _results_of(response: dict[str, Any]) -> list[dict[str, Any]]:
    """The comment objects in one list envelope."""
    return [c for c in response.get("results", []) or [] if isinstance(c, dict)]


def _next_cursor(response: dict[str, Any], block_id: str) -> str | None:
    """The cursor for the next page, or ``None`` when the listing is complete.

    **Fails closed.** A malformed envelope — no ``results`` key at all, or
    ``has_more`` with no cursor to follow it with — raises rather than returning
    what was accumulated so far. Returning it would be indistinguishable from a
    complete listing, and the caller that matters here is
    :func:`~notion_ops.utils.publish._guard_destructive_republish`, which reads an
    empty list as "nobody is talking about this page, go ahead and delete its
    blocks". A truncated listing is *unknown*, not *empty*, and unknown is the one
    answer this guard must never round down (nops-cycle-3 rev2, hostile-5).
    """
    if "results" not in response:
        raise NotionOpsError(
            f"comments.list returned an envelope with no 'results' for {block_id}: "
            f"the comment listing is unusable, and treating it as an empty listing "
            f"would report an undiscussed page",
            code="malformed_response",
        )
    if not response.get("has_more"):
        return None
    cursor = response.get("next_cursor")
    if not cursor:
        raise NotionOpsError(
            f"comments.list reported has_more=True but returned no next_cursor for "
            f"{block_id}: the comment listing is truncated, and what was read so "
            f"far cannot be reported as the whole discussion",
            code="malformed_response",
        )
    return str(cursor)


def list_discussions(client: Any, block_id: str) -> list[Discussion]:
    """List the open discussions on *block_id*, paginated and retry-wrapped.

    Goes through the raw SDK (``client.api``) rather than the operations layer so
    that :mod:`notion_ops.utils.publish`'s guard can call it with the same
    duck-typed ``client`` the publisher already accepts.
    ``CommentOperations.list`` is the public sugar over this function.
    """
    block = extract_notion_id(block_id)

    # The retry wrapper sits INSIDE the mapping, not outside it: a mapped 503 is a
    # bare NotionOpsError carrying Notion's body text, which the retry predicate
    # cannot recognise, so mapping first would spend one attempt where the repo
    # rule asks for four (nops-cycle-3 rev2, contract-5).
    @retry_on_transient_api
    def _raw(cursor: str | None) -> dict[str, Any]:
        return sync_dict(client.api.comments.list(**_list_params(block, cursor)))

    def _list(cursor: str | None) -> dict[str, Any]:
        try:
            return _raw(cursor)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Comment", resource_id=block) from e

    found: list[Discussion] = []
    cursor: str | None = None
    while True:
        response = _list(cursor)
        found.extend(_to_discussion(c) for c in _results_of(response))
        cursor = _next_cursor(response, block)
        if cursor is None:
            return found


async def list_discussions_async(client: Any, block_id: str) -> list[Discussion]:
    """Async twin of :func:`list_discussions`."""
    block = extract_notion_id(block_id)

    @retry_on_transient_api_async
    async def _raw(cursor: str | None) -> Any:
        return await client.api.comments.list(**_list_params(block, cursor))

    async def _list(cursor: str | None) -> dict[str, Any]:
        try:
            response = await _raw(cursor)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Comment", resource_id=block) from e
        if not isinstance(response, dict):
            # The sync twin lets an unexpected shape explode at the first .get();
            # substituting {} here would report "no discussion" instead (hostile-9).
            raise NotionOpsError(
                f"comments.list returned {type(response).__name__}, not an envelope, "
                f"for {block}",
                code="malformed_response",
            )
        return response

    found: list[Discussion] = []
    cursor: str | None = None
    while True:
        response = await _list(cursor)
        found.extend(_to_discussion(c) for c in _results_of(response))
        cursor = _next_cursor(response, block)
        if cursor is None:
            return found


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
        return list_discussions(self._client, block_id)

    def has_discussion(self, block_id: str) -> bool:
        """True when *block_id* carries at least one **un-resolved** comment."""
        return bool(self.list(block_id))


class AsyncCommentOperations:
    """Async read operations for Notion comments."""

    def __init__(self, client: "AsyncNotionOps") -> None:
        self._client = client

    async def list(self, block_id: str) -> list[Discussion]:
        """Return the **un-resolved** comments on a page or block (async)."""
        return await list_discussions_async(self._client, block_id)

    async def has_discussion(self, block_id: str) -> bool:
        """True when *block_id* carries at least one un-resolved comment (async)."""
        return bool(await self.list(block_id))


__all__ = [
    "Discussion",
    "CommentOperations",
    "AsyncCommentOperations",
    "list_discussions",
    "list_discussions_async",
]