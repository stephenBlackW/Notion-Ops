"""Tests for DatabaseOperations."""

import pytest

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.database import Database, DataSource


class TestDatabaseCreate:
    """Tests for DatabaseOperations.create."""

    def test_create_database(self, notion_ops_client, mock_database_response):
        """Create database success path: calls databases.create and returns Database."""
        expected_response = mock_database_response(
            database_id="db-new-001", title="Projects"
        )
        notion_ops_client._notion.databases.create.return_value = expected_response

        db = notion_ops_client.databases.create(
            parent_id="page-parent-001",
            title="Projects",
        )

        assert isinstance(db, Database)
        assert db.id == "db-new-001"
        assert db.title == "Projects"
        notion_ops_client._notion.databases.create.assert_called_once()

        # Verify the create call includes parent, title, and properties
        call_kwargs = notion_ops_client._notion.databases.create.call_args.kwargs
        assert call_kwargs["parent"] == {"type": "page_id", "page_id": "page-parent-001"}
        assert call_kwargs["title"][0]["text"]["content"] == "Projects"
        assert "Name" in call_kwargs["properties"]

    def test_create_database_with_icon(self, notion_ops_client, mock_database_response):
        """Create database with emoji icon passes icon in request."""
        expected_response = mock_database_response(
            database_id="db-icon-001",
            title="Tasks",
            icon={"type": "emoji", "emoji": "📋"},
        )
        notion_ops_client._notion.databases.create.return_value = expected_response

        db = notion_ops_client.databases.create(
            parent_id="page-parent-001",
            title="Tasks",
            icon="📋",
        )

        assert isinstance(db, Database)
        call_kwargs = notion_ops_client._notion.databases.create.call_args.kwargs
        assert call_kwargs["icon"] == {"type": "emoji", "emoji": "📋"}

    def test_create_database_error(self, notion_ops_client):
        """Generic error during create raises NotionOpsError."""
        notion_ops_client._notion.databases.create.side_effect = Exception("Forbidden")

        with pytest.raises(NotionOpsError, match="Failed to create database"):
            notion_ops_client.databases.create(
                parent_id="page-parent-001",
                title="Failing DB",
            )


class TestDatabaseGet:
    """Tests for DatabaseOperations.get."""

    def test_get_database(self, notion_ops_client, mock_database_response):
        """Get database success path: calls databases.retrieve and returns Database."""
        expected_response = mock_database_response(
            database_id="db-get-001", title="My Database"
        )
        notion_ops_client._notion.databases.retrieve.return_value = expected_response

        db = notion_ops_client.databases.get("db-get-001")

        assert isinstance(db, Database)
        assert db.id == "db-get-001"
        assert db.title == "My Database"
        notion_ops_client._notion.databases.retrieve.assert_called_once_with(
            database_id="dbget001"
        )

    def test_get_database_not_found(self, notion_ops_client):
        """Get database with invalid ID raises NotFoundError."""
        notion_ops_client._notion.databases.retrieve.side_effect = Exception(
            "Could not find database. object_not_found"
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.databases.get("db-missing-001")

        assert exc_info.value.resource_type == "Database"
        assert exc_info.value.resource_id == "dbmissing001"

    def test_get_database_generic_error(self, notion_ops_client):
        """Get database with generic error raises NotionOpsError."""
        notion_ops_client._notion.databases.retrieve.side_effect = Exception(
            "Unexpected error"
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve database"):
            notion_ops_client.databases.get("db-err-001")


class TestDatabaseUpdate:
    """Tests for DatabaseOperations.update."""

    def test_update_database(self, notion_ops_client, mock_database_response):
        """Update database success path: updates title."""
        updated_response = mock_database_response(
            database_id="db-upd-001", title="Renamed DB"
        )
        notion_ops_client._notion.databases.update.return_value = updated_response

        db = notion_ops_client.databases.update(
            "db-upd-001",
            title="Renamed DB",
        )

        assert isinstance(db, Database)
        assert db.title == "Renamed DB"
        notion_ops_client._notion.databases.update.assert_called_once()
        call_kwargs = notion_ops_client._notion.databases.update.call_args.kwargs
        assert call_kwargs["title"][0]["text"]["content"] == "Renamed DB"

    def test_update_database_no_changes(self, notion_ops_client, mock_database_response):
        """Update with no changes returns existing database via get."""
        existing_response = mock_database_response(database_id="db-noop-001")
        notion_ops_client._notion.databases.retrieve.return_value = existing_response

        db = notion_ops_client.databases.update("db-noop-001")

        assert isinstance(db, Database)
        notion_ops_client._notion.databases.update.assert_not_called()
        notion_ops_client._notion.databases.retrieve.assert_called_once()

    def test_update_database_not_found(self, notion_ops_client):
        """Update database with invalid ID raises NotFoundError."""
        notion_ops_client._notion.databases.update.side_effect = Exception(
            "object_not_found"
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.databases.update("db-missing-001", title="New")

        assert exc_info.value.resource_type == "Database"


class TestDatabaseArchive:
    """Tests for DatabaseOperations.archive."""

    def test_archive_database(self, notion_ops_client, mock_database_response):
        """Archive database calls update with archived=True."""
        archived_response = mock_database_response(
            database_id="db-arch-001", archived=True
        )
        notion_ops_client._notion.databases.update.return_value = archived_response

        db = notion_ops_client.databases.archive("db-arch-001")

        assert isinstance(db, Database)
        assert db.archived is True
        notion_ops_client._notion.databases.update.assert_called_once_with(
            database_id="dbarch001",
            archived=True,
        )

    def test_archive_database_error(self, notion_ops_client):
        """Archive database with error raises NotionOpsError."""
        notion_ops_client._notion.databases.update.side_effect = Exception(
            "Cannot archive"
        )

        with pytest.raises(NotionOpsError, match="Failed to archive database"):
            notion_ops_client.databases.archive("db-fail-001")


class TestDatabaseListDataSources:
    """Tests for DatabaseOperations.list_data_sources."""

    def test_list_data_sources(
        self, notion_ops_client, mock_database_response
    ):
        """list_data_sources returns a list with one DataSource."""
        expected_response = mock_database_response(
            database_id="db-ds-001", title="DS Database"
        )
        notion_ops_client._notion.databases.retrieve.return_value = (
            expected_response
        )

        result = notion_ops_client.databases.list_data_sources("db-ds-001")

        assert len(result) == 1
        assert isinstance(result[0], DataSource)
        assert result[0].id == "db-ds-001"
        assert result[0].database_id == "db-ds-001"
        assert result[0].title == "DS Database"


class TestDatabaseExtractId:
    """Tests for DatabaseOperations._extract_id."""

    def test_extract_id_plain(self, notion_ops_client):
        """Plain ID with dashes gets dashes removed."""
        ops = notion_ops_client.databases
        assert ops._extract_id("2d8d-371a-79f4") == "2d8d371a79f4"

    def test_extract_id_from_url(self, notion_ops_client):
        """Notion URL extracts ID from path."""
        ops = notion_ops_client.databases
        url = "https://www.notion.so/workspace/My-Database-abcdef1234567890abcdef1234567890"
        result = ops._extract_id(url)
        assert result == "abcdef1234567890abcdef1234567890"
