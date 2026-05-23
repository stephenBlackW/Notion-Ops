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
