"""Tests for the map_api_error() helper and APIResponseError-based error mapping.

Verifies that Notion API errors are correctly translated to the appropriate
custom exception types across different HTTP status codes and Notion error codes.
"""

import httpx
import pytest
from notion_client import APIResponseError
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    NotionOpsError,
    PermissionError,
    RateLimitError,
    ValidationError,
    map_api_error,
)


def _make_error(
    status: int,
    code: str | APIErrorCode,
    message: str = "error",
    *,
    headers: dict[str, str] | None = None,
) -> APIResponseError:
    """Convenience constructor for test APIResponseError instances."""
    hdrs = httpx.Headers(headers or {"content-type": "application/json"})
    code_str = code.value if isinstance(code, APIErrorCode) else code
    body = f'{{"object":"error","code":"{code_str}","message":"{message}"}}'
    return APIResponseError(
        code=code,
        status=status,
        message=message,
        headers=hdrs,
        raw_body_text=body,
    )


# ---------------------------------------------------------------------------
# map_api_error unit tests
# ---------------------------------------------------------------------------


class TestMapApiError:
    """Direct unit tests for the map_api_error helper."""

    def test_404_object_not_found(self):
        """404 + object_not_found -> NotFoundError."""
        err = _make_error(404, APIErrorCode.ObjectNotFound)
        result = map_api_error(err, resource_type="Page", resource_id="abc123")

        assert isinstance(result, NotFoundError)
        assert result.resource_type == "Page"
        assert result.resource_id == "abc123"

    def test_401_unauthorized(self):
        """401 + unauthorized -> AuthenticationError."""
        err = _make_error(401, APIErrorCode.Unauthorized)
        result = map_api_error(err)

        assert isinstance(result, AuthenticationError)

    def test_403_restricted_resource(self):
        """403 + restricted_resource -> PermissionError."""
        err = _make_error(403, APIErrorCode.RestrictedResource)
        result = map_api_error(err)

        assert isinstance(result, PermissionError)

    def test_429_rate_limited(self):
        """429 + rate_limited -> RateLimitError with default retry_after."""
        err = _make_error(429, APIErrorCode.RateLimited)
        result = map_api_error(err)

        assert isinstance(result, RateLimitError)
        assert result.retry_after == 1.0

    def test_429_rate_limited_with_retry_after_header(self):
        """429 with Retry-After header populates retry_after."""
        err = _make_error(
            429,
            APIErrorCode.RateLimited,
            headers={"content-type": "application/json", "Retry-After": "3.5"},
        )
        result = map_api_error(err)

        assert isinstance(result, RateLimitError)
        assert result.retry_after == 3.5

    def test_400_validation_error(self):
        """400 + validation_error -> ValidationError."""
        err = _make_error(400, APIErrorCode.ValidationError, "Invalid input")
        result = map_api_error(err)

        assert isinstance(result, ValidationError)

    def test_400_invalid_json(self):
        """400 + invalid_json -> ValidationError."""
        err = _make_error(400, APIErrorCode.InvalidJSON, "Bad JSON")
        result = map_api_error(err)

        assert isinstance(result, ValidationError)

    def test_400_invalid_request(self):
        """400 + invalid_request -> ValidationError."""
        err = _make_error(400, APIErrorCode.InvalidRequest, "Bad request")
        result = map_api_error(err)

        assert isinstance(result, ValidationError)

    def test_409_conflict_error(self):
        """409 + conflict_error -> ConflictError."""
        err = _make_error(409, APIErrorCode.ConflictError)
        result = map_api_error(err)

        assert isinstance(result, ConflictError)

    def test_500_internal_server_error(self):
        """500 + internal_server_error -> generic NotionOpsError."""
        err = _make_error(500, APIErrorCode.InternalServerError, "Server error")
        result = map_api_error(err)

        assert isinstance(result, NotionOpsError)
        assert not isinstance(result, NotFoundError)
        assert result.code == "internal_server_error"

    def test_503_service_unavailable(self):
        """503 + service_unavailable -> generic NotionOpsError (retried by decorator)."""
        err = _make_error(503, APIErrorCode.ServiceUnavailable, "Unavailable")
        result = map_api_error(err)

        assert isinstance(result, NotionOpsError)
        assert result.code == "service_unavailable"


# ---------------------------------------------------------------------------
# Integration tests: verify that operation classes raise the right exceptions
# when the SDK raises APIResponseError.
# ---------------------------------------------------------------------------


class TestAuthenticationErrorMapping:
    """401 -> AuthenticationError across operation classes."""

    def test_pages_get_401(self, notion_ops_client, make_api_error):
        """pages.get raises AuthenticationError on 401."""
        notion_ops_client._notion.pages.retrieve.side_effect = make_api_error(
            401, APIErrorCode.Unauthorized, "Invalid token"
        )

        with pytest.raises(AuthenticationError):
            notion_ops_client.pages.get("page-001")

    def test_blocks_get_401(self, notion_ops_client, make_api_error):
        """blocks.get raises AuthenticationError on 401."""
        notion_ops_client._notion.blocks.retrieve.side_effect = make_api_error(
            401, APIErrorCode.Unauthorized, "Invalid token"
        )

        with pytest.raises(AuthenticationError):
            notion_ops_client.blocks.get("block-001")

    def test_users_get_401(self, notion_ops_client, make_api_error):
        """users.get raises AuthenticationError on 401."""
        notion_ops_client._notion.users.retrieve.side_effect = make_api_error(
            401, APIErrorCode.Unauthorized, "Invalid token"
        )

        with pytest.raises(AuthenticationError):
            notion_ops_client.users.get("user-001")


class TestRateLimitErrorMapping:
    """429 -> RateLimitError across operation classes."""

    def test_pages_get_429(self, notion_ops_client, make_api_error):
        """pages.get raises RateLimitError on 429."""
        notion_ops_client._notion.pages.retrieve.side_effect = make_api_error(
            429, APIErrorCode.RateLimited, "Rate limited"
        )

        with pytest.raises(RateLimitError):
            notion_ops_client.pages.get("page-001")

    def test_data_sources_query_429(self, notion_ops_client, make_api_error):
        """data_sources.query raises RateLimitError on 429."""
        notion_ops_client._notion.request.side_effect = make_api_error(
            429, APIErrorCode.RateLimited, "Rate limited"
        )

        with pytest.raises(RateLimitError):
            notion_ops_client.data_sources.query("ds-001")

    def test_databases_get_429(self, notion_ops_client, make_api_error):
        """databases.get raises RateLimitError on 429."""
        notion_ops_client._notion.databases.retrieve.side_effect = make_api_error(
            429, APIErrorCode.RateLimited, "Rate limited"
        )

        with pytest.raises(RateLimitError):
            notion_ops_client.databases.get("db-001")


class TestPermissionErrorMapping:
    """403 -> PermissionError across operation classes."""

    def test_pages_get_403(self, notion_ops_client, make_api_error):
        """pages.get raises PermissionError on 403."""
        notion_ops_client._notion.pages.retrieve.side_effect = make_api_error(
            403, APIErrorCode.RestrictedResource, "Restricted"
        )

        with pytest.raises(PermissionError):
            notion_ops_client.pages.get("page-001")

    def test_blocks_append_403(self, notion_ops_client, make_api_error):
        """blocks.append raises PermissionError on 403."""
        from notion_ops.models.block import Blocks

        notion_ops_client._notion.blocks.children.append.side_effect = make_api_error(
            403, APIErrorCode.RestrictedResource, "Restricted"
        )

        with pytest.raises(PermissionError):
            notion_ops_client.blocks.append(
                "page-001", [Blocks.paragraph("text")]
            )

    def test_databases_update_403(self, notion_ops_client, make_api_error):
        """databases.update raises PermissionError on 403."""
        notion_ops_client._notion.databases.update.side_effect = make_api_error(
            403, APIErrorCode.RestrictedResource, "Restricted"
        )

        with pytest.raises(PermissionError):
            notion_ops_client.databases.update("db-001", title="New Title")


class TestValidationErrorMapping:
    """400 -> ValidationError across operation classes."""

    def test_pages_create_400(self, notion_ops_client, make_api_error):
        """pages.create raises ValidationError on 400."""
        from notion_ops.models.properties import TitleProperty

        notion_ops_client._notion.pages.create.side_effect = make_api_error(
            400, APIErrorCode.ValidationError, "Invalid properties"
        )

        with pytest.raises(ValidationError):
            notion_ops_client.pages.create(
                parent_id="db-001",
                properties={"Name": TitleProperty(value="Test")},
            )


class TestGenericExceptionFallback:
    """Non-APIResponseError exceptions propagate directly (broad catch removed)."""

    def test_pages_get_generic_exception(self, notion_ops_client):
        """A plain Exception in pages.get propagates directly."""
        notion_ops_client._notion.pages.retrieve.side_effect = Exception(
            "Connection reset"
        )

        with pytest.raises(Exception, match="Connection reset"):
            notion_ops_client.pages.get("page-001")

    def test_blocks_get_generic_exception(self, notion_ops_client):
        """A plain Exception in blocks.get propagates directly."""
        notion_ops_client._notion.blocks.retrieve.side_effect = Exception(
            "Connection timeout"
        )

        with pytest.raises(Exception, match="Connection timeout"):
            notion_ops_client.blocks.get("block-001")
