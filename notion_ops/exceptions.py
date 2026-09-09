"""Custom exceptions for Notion Operations library."""

from __future__ import annotations

from typing import Any

from notion_client import APIResponseError


class NotionOpsError(Exception):
    """Base exception for all Notion Operations errors."""

    def __init__(self, message: str, code: str | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


class AuthenticationError(NotionOpsError):
    """Raised when authentication fails (invalid API key)."""

    def __init__(self, message: str = "Invalid or missing API key"):
        super().__init__(message, code="unauthorized")


class NotFoundError(NotionOpsError):
    """Raised when a resource is not found."""

    def __init__(self, resource_type: str, resource_id: str):
        message = f"{resource_type} not found: {resource_id}"
        super().__init__(message, code="object_not_found")
        self.resource_type = resource_type
        self.resource_id = resource_id


class RateLimitError(NotionOpsError):
    """Raised when rate limit is exceeded."""

    def __init__(self, retry_after: float = 1.0):
        message = f"Rate limit exceeded. Retry after {retry_after} seconds."
        super().__init__(message, code="rate_limited")
        self.retry_after = retry_after


class ValidationError(NotionOpsError):
    """Raised when input validation fails."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message, code="validation_error")
        self.field = field


class ConflictError(NotionOpsError):
    """Raised when there's a conflict (e.g., duplicate unique value)."""

    def __init__(self, message: str):
        super().__init__(message, code="conflict_error")


class PermissionError(NotionOpsError):
    """Raised when the integration lacks required permissions."""

    def __init__(self, message: str = "Insufficient permissions for this operation"):
        super().__init__(message, code="restricted_resource")


class OversizedContentError(NotionOpsError):
    """Raised when markdown contains a text run too large to split safely.

    Notion caps a single rich_text/text block at ~2000 characters. Normal
    prose splits cleanly at newlines or spaces, but an unbroken run with no
    whitespace (e.g. a pasted base64 blob or minified payload in a paragraph)
    cannot be split without cutting mid-token. That is a red flag for malformed
    input, so it is escalated rather than silently chopped into fragments.
    """

    def __init__(self, run_length: int, limit: int, preview: str):
        message = (
            f"Unsplittable text run of {run_length} chars exceeds the "
            f"{limit}-char block limit (no newline or space to break on). "
            f"This usually means malformed input. Preview: {preview!r}"
        )
        super().__init__(message, code="oversized_content")
        self.run_length = run_length
        self.limit = limit
        self.preview = preview


class DestructiveRepublishError(NotionOpsError):
    """Raised when a destructive republish targets a page it must not destroy.

    ``republish_block_tree`` converges a page's blocks by deleting the ones that
    changed, which permanently detaches every comment anchored to them — the
    Notion API cannot move or re-anchor a comment (ISS-029). So a republish that
    *would write* refuses by default when the page carries an open discussion, or
    when the caller's own ``protected`` predicate flags it.

    The message names the page, the trigger and the discussion count, and
    deliberately carries **no comment text and no client attribute** — a refusal
    is not a place to leak either.

    Args:
        page_id: The page the republish targeted.
        trigger: ``"discussion"`` or ``"protected"``.
        discussion_count: Open comments found. ``0`` for a ``protected`` refusal,
            which does not consult the comments endpoint.
    """

    def __init__(self, page_id: str, trigger: str, discussion_count: int = 0):
        if trigger == "discussion":
            why = (
                f"it carries {discussion_count} open discussion "
                f"comment(s), whose anchors a republish would destroy"
            )
        else:
            why = "the caller's protected predicate flags it"
        message = (
            f"Refusing to republish {page_id}: {why}. "
            f"Revise it as a new version instead (notion_ops.revise_page), or "
            f"pass allow_destructive=True to overwrite it anyway."
        )
        super().__init__(message, code="destructive_republish")
        self.page_id = page_id
        self.trigger = trigger
        self.discussion_count = discussion_count


class IncompleteSnapshotError(NotionOpsError):
    """Raised when a snapshot cannot faithfully carry a page's old body.

    ``revise_page(mode="snapshot-in-place")`` rewrites the hub page destructively,
    so the snapshot is the **only** copy of what was there. A copy known to be
    incomplete therefore cannot justify the rewrite, and the rewrite is refused
    *before* it happens: the page is left exactly as it was — blocks, anchors and
    all — and the caller decides what to do next (revise in ``new-canonical``
    mode, move the offending blocks by hand, or accept the loss by some route that
    says so out loud).

    Two conditions raise it:

    - the source carries a block type the API cannot re-create from its own
      payload — ``child_page``, ``child_database``, ``synced_block``,
      ``unsupported``, ``ai_block``. These matter more than the missing content:
      deleting a ``child_page`` block trashes the child page and deleting a
      ``child_database`` block trashes the database, and a hub page is precisely
      the page shape that holds them;
    - the snapshot's own publish came back ``partial``, i.e. some nested content
      never landed on the snapshot.

    Args:
        page_id: The page that was **not** modified. That is the point of it.
        reason: Short phrase naming which condition fired.
        snapshot_page_id: The snapshot, when one had already been created. It is
            left in place for inspection rather than cleaned up — deleting a page
            to tidy up after a refusal is the behaviour this module exists to
            avoid. Delete it by hand if you do not want it.
        block_types: The uncopyable types found, when that is the reason.
    """

    def __init__(
        self,
        page_id: str,
        reason: str,
        *,
        snapshot_page_id: str | None = None,
        block_types: tuple[str, ...] = (),
    ):
        where = (
            f" The snapshot {snapshot_page_id} was created and is left in place for "
            f"inspection."
            if snapshot_page_id
            else ""
        )
        types = f" Block type(s): {', '.join(block_types)}." if block_types else ""
        message = (
            f"Refusing to rewrite {page_id} in place: {reason}, so the snapshot "
            f"would not be a complete copy of what the rewrite is about to "
            f"destroy.{types}{where} The page has NOT been modified. Revise it with "
            f"mode='new-canonical' instead, which destroys nothing."
        )
        super().__init__(message, code="incomplete_snapshot")
        self.page_id = page_id
        self.reason = reason
        self.snapshot_page_id = snapshot_page_id
        self.block_types = block_types


def map_api_error(
    error: APIResponseError,
    resource_type: str = "resource",
    resource_id: str = "",
) -> NotionOpsError:
    """Map a Notion API error to the appropriate custom exception.

    Uses the HTTP status code and Notion error code from the
    ``APIResponseError`` to select a specific ``NotionOpsError`` subclass.

    Args:
        error: The ``APIResponseError`` raised by the notion-client SDK.
        resource_type: Human-readable resource type (e.g. "Page", "Block").
        resource_id: The ID of the resource the operation targeted.

    Returns:
        An instance of the appropriate ``NotionOpsError`` subclass.
    """
    status = error.status
    # error.code may be an APIErrorCode enum or a plain string; normalise.
    code = str(error.code.value) if hasattr(error.code, "value") else str(error.code)

    if status == 404 or code == "object_not_found":
        return NotFoundError(resource_type, resource_id)

    if status == 401 or code == "unauthorized":
        return AuthenticationError()

    if status == 403 or code == "restricted_resource":
        return PermissionError()

    if status == 429 or code == "rate_limited":
        retry_after = 1.0
        if hasattr(error, "headers") and error.headers is not None:
            raw = error.headers.get("Retry-After")
            if raw is not None:
                try:
                    retry_after = float(raw)
                except (ValueError, TypeError):
                    pass
        return RateLimitError(retry_after=retry_after)

    if status == 400 or code in ("validation_error", "invalid_json", "invalid_request"):
        return ValidationError(str(error))

    if status == 409 or code == "conflict_error":
        return ConflictError(str(error))

    return NotionOpsError(str(error), code=code)
