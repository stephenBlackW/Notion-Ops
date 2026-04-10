"""Tests for DatabaseOperations (sync) and AsyncDatabaseOperations (async).

Both code paths are exercised via the parametrised ``ops`` fixture.
"""

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.database import Database, DataSource

from .conftest import maybe_await

# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestDatabaseCreate:
    """Tests for create (sync & async)."""

    @pytest.mark.asyncio
    async def test_create_database(self, ops, mock_database_response):
        expected = mock_database_response(database_id="db-new-001", title="Projects")
        ops.setup_mock("databases.create", return_value=expected)

        db = await maybe_await(
            ops.databases.create(parent_id="page-parent-001", title="Projects")
        )

        assert isinstance(db, Database)
        assert db.id == "db-new-001"
        assert db.title == "Projects"
        ops.get_mock("databases.create").assert_called_once()

        call_kwargs = ops.get_mock("databases.create").call_args.kwargs
        assert call_kwargs["parent"] == {
            "type": "page_id",
            "page_id": "page-parent-001",
        }
        assert call_kwargs["title"][0]["text"]["content"] == "Projects"
        assert "Name" in call_kwargs["properties"]

    @pytest.mark.asyncio
    async def test_create_database_with_icon(self, ops, mock_database_response):
        expected = mock_database_response(
            database_id="db-icon-001",
            title="Tasks",
            icon={"type": "emoji", "emoji": "x"},
        )
        ops.setup_mock("databases.create", return_value=expected)

        await maybe_await(
            ops.databases.create(
                parent_id="page-parent-001", title="Tasks", icon="x"
            )
        )

        call_kwargs = ops.get_mock("databases.create").call_args.kwargs
        assert call_kwargs["icon"] == {"type": "emoji", "emoji": "x"}

    @pytest.mark.asyncio
    async def test_create_database_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock("databases.create", side_effect=Exception("Forbidden"))

        with pytest.raises(Exception, match="Forbidden"):
            await maybe_await(
                ops.databases.create(
                    parent_id="page-parent-001", title="Failing DB"
                )
            )

    @pytest.mark.asyncio
    async def test_create_database_api_error(self, ops, make_api_error):
        ops.setup_mock(
            "databases.create",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Parent not found"
            ),
        )

        with pytest.raises(NotFoundError):
            await maybe_await(
                ops.databases.create(
                    parent_id="page-missing", title="Orphan DB"
                )
            )


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestDatabaseGet:
    """Tests for get (sync & async)."""

    @pytest.mark.asyncio
    async def test_get_database(self, ops, mock_database_response):
        expected = mock_database_response(
            database_id="db-get-001", title="My Database"
        )
        ops.setup_mock("databases.retrieve", return_value=expected)

        db = await maybe_await(ops.databases.get("db-get-001"))

        assert isinstance(db, Database)
        assert db.id == "db-get-001"
        assert db.title == "My Database"
        ops.get_mock("databases.retrieve").assert_called_once_with(
            database_id="dbget001"
        )

    @pytest.mark.asyncio
    async def test_get_database_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "databases.retrieve",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find database."
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.databases.get("db-missing-001"))

        assert exc_info.value.resource_type == "Database"
        assert exc_info.value.resource_id == "dbmissing001"

    @pytest.mark.asyncio
    async def test_get_database_generic_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock(
            "databases.retrieve", side_effect=Exception("Unexpected error")
        )

        with pytest.raises(Exception, match="Unexpected error"):
            await maybe_await(ops.databases.get("db-err-001"))


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestDatabaseUpdate:
    """Tests for update (sync & async)."""

    @pytest.mark.asyncio
    async def test_update_database(self, ops, mock_database_response):
        updated = mock_database_response(
            database_id="db-upd-001", title="Renamed DB"
        )
        ops.setup_mock("databases.update", return_value=updated)

        db = await maybe_await(ops.databases.update("db-upd-001", title="Renamed DB"))

        assert isinstance(db, Database)
        assert db.title == "Renamed DB"
        ops.get_mock("databases.update").assert_called_once()
        call_kwargs = ops.get_mock("databases.update").call_args.kwargs
        assert call_kwargs["title"][0]["text"]["content"] == "Renamed DB"

    @pytest.mark.asyncio
    async def test_update_database_no_changes(self, ops, mock_database_response):
        existing = mock_database_response(database_id="db-noop-001")
        ops.setup_mock("databases.retrieve", return_value=existing)
        if ops.is_async:
            ops.setup_mock("databases.update")

        db = await maybe_await(ops.databases.update("db-noop-001"))

        assert isinstance(db, Database)
        ops.get_mock("databases.update").assert_not_called()
        ops.get_mock("databases.retrieve").assert_called_once()

    @pytest.mark.asyncio
    async def test_update_database_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "databases.update",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.databases.update("db-missing-001", title="New"))

        assert exc_info.value.resource_type == "Database"


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


class TestDatabaseArchive:
    """Tests for archive (sync & async)."""

    @pytest.mark.asyncio
    async def test_archive_database(self, ops, mock_database_response):
        archived = mock_database_response(database_id="db-arch-001", archived=True)
        ops.setup_mock("databases.update", return_value=archived)

        db = await maybe_await(ops.databases.archive("db-arch-001"))

        assert isinstance(db, Database)
        assert db.archived is True
        ops.get_mock("databases.update").assert_called_once_with(
            database_id="dbarch001", archived=True
        )

    @pytest.mark.asyncio
    async def test_archive_database_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock(
            "databases.update", side_effect=Exception("Cannot archive")
        )

        with pytest.raises(Exception, match="Cannot archive"):
            await maybe_await(ops.databases.archive("db-fail-001"))


# ---------------------------------------------------------------------------
# list_data_sources
# ---------------------------------------------------------------------------


class TestDatabaseListDataSources:
    """Tests for list_data_sources (sync & async)."""

    @pytest.mark.asyncio
    async def test_list_data_sources(self, ops, mock_database_response):
        expected = mock_database_response(
            database_id="db-ds-001", title="DS Database"
        )
        ops.setup_mock("databases.retrieve", return_value=expected)

        result = await maybe_await(ops.databases.list_data_sources("db-ds-001"))

        assert len(result) == 1
        assert isinstance(result[0], DataSource)
        assert result[0].id == "db-ds-001"
        assert result[0].database_id == "db-ds-001"
        assert result[0].title == "DS Database"


# ---------------------------------------------------------------------------
# _extract_id
# ---------------------------------------------------------------------------


class TestDatabaseExtractId:
    """Tests for the shared extract_notion_id utility."""

    def test_extract_id_plain(self, ops):
        from notion_ops.utils.ids import extract_notion_id
        assert extract_notion_id("2d8d-371a-79f4") == "2d8d371a79f4"

    def test_extract_id_from_url(self, ops):
        from notion_ops.utils.ids import extract_notion_id
        url = "https://www.notion.so/workspace/My-Database-abcdef1234567890abcdef1234567890"
        assert extract_notion_id(url) == "abcdef1234567890abcdef1234567890"
