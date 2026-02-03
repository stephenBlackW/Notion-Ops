"""Custom exceptions for Notion Operations library."""

from typing import Any


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
