"""Tests for AsyncDatabaseOperations."""

from unittest.mock import AsyncMock

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.database import Database, DataSource


class TestAsyncDatabaseGet:
    """Tests for AsyncDatabaseOperations.get."""

    @pytest.mark.asyncio
    async def test_get_database(
        self, async_notion_ops_client, mock_database_response
    ):
        """Get database success path: calls databases.retrieve and returns Database."""
        client = async_notion_ops_client
        expected_response = mock_database_response(
            database_id="db-get-001", title="My Database"
        )
        client._notion.databases.retrieve = AsyncMock(
            return_value=expected_response
        )

        db = await client.databases.get("db-get-001")

        assert isinstance(db, Database)
        assert db.id == "db-get-001"
        assert db.title == "My Database"
        client._notion.databases.retrieve.assert_called_once_with(
            database_id="dbget001"
        )

    @pytest.mark.asyncio
    async def test_get_database_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Get database with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.databases.retrieve = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find database."
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.databases.get("db-missing-001")

        assert exc_info.value.resource_type == "Database"
        assert exc_info.value.resource_id == "dbmissing001"

    @pytest.mark.asyncio
    async def test_get_database_generic_error(self, async_notion_ops_client):
        """Get database with generic error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.databases.retrieve = AsyncMock(
            side_effect=Exception("Unexpected error")
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve database"):
            await client.databases.get("db-err-001")


class TestAsyncDatabaseCreate:
    """Tests for AsyncDatabaseOperations.create."""

    @pytest.mark.asyncio
    async def test_create_database(
        self, async_notion_ops_client, mock_database_response
    ):
        """Create database success path: calls databases.create and returns Database."""
        client = async_notion_ops_client
        expected_response = mock_database_response(
            database_id="db-new-001", title="Projects"
        )
        client._notion.databases.create = AsyncMock(
            return_value=expected_response
        )

        db = await client.databases.create(
            parent_id="page-parent-001",
            title="Projects",
        )

        assert isinstance(db, Database)
        assert db.id == "db-new-001"
        assert db.title == "Projects"
        client._notion.databases.create.assert_called_once()

        # Verify the create call includes parent, title, and properties
        call_kwargs = client._notion.databases.create.call_args.kwargs
        assert call_kwargs["parent"] == {
            "type": "page_id",
            "page_id": "page-parent-001",
        }
        assert call_kwargs["title"][0]["text"]["content"] == "Projects"
        assert "Name" in call_kwargs["properties"]

    @pytest.mark.asyncio
    async def test_create_database_with_icon(
        self, async_notion_ops_client, mock_database_response
    ):
        """Create database with emoji icon passes icon in request."""
        client = async_notion_ops_client
        expected_response = mock_database_response(
            database_id="db-icon-001",
            title="Tasks",
            icon={"type": "emoji", "emoji": "x"},
        )
        client._notion.databases.create = AsyncMock(
            return_value=expected_response
        )

        await client.databases.create(
            parent_id="page-parent-001",
            title="Tasks",
            icon="x",
        )

        call_kwargs = client._notion.databases.create.call_args.kwargs
        assert call_kwargs["icon"] == {"type": "emoji", "emoji": "x"}

    @pytest.mark.asyncio
    async def test_create_database_error(self, async_notion_ops_client):
        """Generic error during create raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.databases.create = AsyncMock(
            side_effect=Exception("Forbidden")
        )

        with pytest.raises(NotionOpsError, match="Failed to create database"):
            await client.databases.create(
                parent_id="page-parent-001",
                title="Failing DB",
            )

    @pytest.mark.asyncio
    async def test_create_database_api_error(
        self, async_notion_ops_client, make_api_error
    ):
        """APIResponseError during create raises mapped error."""
        client = async_notion_ops_client
        client._notion.databases.create = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Parent not found"
            )
        )

        with pytest.raises(NotFoundError):
            await client.databases.create(
                parent_id="page-missing",
                title="Orphan DB",
            )


class TestAsyncDatabaseUpdate:
    """Tests for AsyncDatabaseOperations.update."""

    @pytest.mark.asyncio
    async def test_update_database(
        self, async_notion_ops_client, mock_database_response
    ):
        """Update database success path: updates title."""
        client = async_notion_ops_client
        updated_response = mock_database_response(
            database_id="db-upd-001", title="Renamed DB"
        )
        client._notion.databases.update = AsyncMock(
            return_value=updated_response
        )

        db = await client.databases.update(
            "db-upd-001",
            title="Renamed DB",
        )

        assert isinstance(db, Database)
        assert db.title == "Renamed DB"
        client._notion.databases.update.assert_called_once()
        call_kwargs = client._notion.databases.update.call_args.kwargs
        assert call_kwargs["title"][0]["text"]["content"] == "Renamed DB"

    @pytest.mark.asyncio
    async def test_update_database_no_changes(
        self, async_notion_ops_client, mock_database_response
    ):
        """Update with no changes returns existing database via get."""
        client = async_notion_ops_client
        existing_response = mock_database_response(database_id="db-noop-001")
        client._notion.databases.retrieve = AsyncMock(
            return_value=existing_response
        )
        client._notion.databases.update = AsyncMock()

        db = await client.databases.update("db-noop-001")

        assert isinstance(db, Database)
        client._notion.databases.update.assert_not_called()
        client._notion.databases.retrieve.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_database_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Update database with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.databases.update = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.databases.update("db-missing-001", title="New")

        assert exc_info.value.resource_type == "Database"


class TestAsyncDatabaseArchive:
    """Tests for AsyncDatabaseOperations.archive."""

    @pytest.mark.asyncio
    async def test_archive_database(
        self, async_notion_ops_client, mock_database_response
    ):
        """Archive database calls update with archived=True."""
        client = async_notion_ops_client
        archived_response = mock_database_response(
            database_id="db-arch-001", archived=True
        )
        client._notion.databases.update = AsyncMock(
            return_value=archived_response
        )

        db = await client.databases.archive("db-arch-001")

        assert isinstance(db, Database)
        assert db.archived is True
        client._notion.databases.update.assert_called_once_with(
            database_id="dbarch001",
            archived=True,
        )

    @pytest.mark.asyncio
    async def test_archive_database_error(self, async_notion_ops_client):
        """Archive database with error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.databases.update = AsyncMock(
            side_effect=Exception("Cannot archive")
        )

        with pytest.raises(NotionOpsError, match="Failed to archive database"):
            await client.databases.archive("db-fail-001")


class TestAsyncDatabaseListDataSources:
    """Tests for AsyncDatabaseOperations.list_data_sources."""

    @pytest.mark.asyncio
    async def test_list_data_sources(
        self, async_notion_ops_client, mock_database_response
    ):
        """list_data_sources returns a list with one DataSource."""
        client = async_notion_ops_client
        expected_response = mock_database_response(
            database_id="db-ds-001", title="DS Database"
        )
        client._notion.databases.retrieve = AsyncMock(
            return_value=expected_response
        )

        result = await client.databases.list_data_sources("db-ds-001")

        assert len(result) == 1
        assert isinstance(result[0], DataSource)
        assert result[0].id == "db-ds-001"
        assert result[0].database_id == "db-ds-001"
        assert result[0].title == "DS Database"
