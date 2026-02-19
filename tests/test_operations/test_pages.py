"""Tests for PageOperations."""

from unittest.mock import MagicMock, patch

import pytest

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.page import Page
from notion_ops.models.properties import SelectProperty, TitleProperty


class TestPageCreate:
    """Tests for PageOperations.create."""

    def test_create_page(self, notion_ops_client, mock_page_response):
        """Create page success path: calls pages.create and returns Page."""
        expected_response = mock_page_response(title="New Page")
        notion_ops_client._notion.pages.create.return_value = expected_response

        page = notion_ops_client.pages.create(
            parent_id="db-xyz789",
            properties={
                "Name": TitleProperty(value="New Page"),
            },
        )

        assert isinstance(page, Page)
        assert page.id == "page-abc123"
        assert page.get_title() == "New Page"
        notion_ops_client._notion.pages.create.assert_called_once()

        # Verify the call kwargs contain parent and properties
        call_kwargs = notion_ops_client._notion.pages.create.call_args
        assert "parent" in call_kwargs.kwargs or "parent" in (
            call_kwargs[1] if len(call_kwargs) > 1 else {}
        )

    def test_create_page_error(self, notion_ops_client):
        """Generic error during create raises NotionOpsError."""
        notion_ops_client._notion.pages.create.side_effect = Exception("API connection failed")

        with pytest.raises(NotionOpsError, match="Failed to create page"):
            notion_ops_client.pages.create(
                parent_id="db-xyz789",
                properties={"Name": TitleProperty(value="Failing Page")},
            )


class TestPageGet:
    """Tests for PageOperations.get."""

    def test_get_page(self, notion_ops_client, mock_page_response):
        """Get page success path: calls pages.retrieve and returns Page."""
        expected_response = mock_page_response(page_id="page-get-001", title="Fetched Page")
        notion_ops_client._notion.pages.retrieve.return_value = expected_response

        page = notion_ops_client.pages.get("page-get-001")

        assert isinstance(page, Page)
        assert page.id == "page-get-001"
        assert page.get_title() == "Fetched Page"
        notion_ops_client._notion.pages.retrieve.assert_called_once_with(
            page_id="pageget001"
        )

    def test_get_page_not_found(self, notion_ops_client):
        """Get page with invalid ID raises NotFoundError."""
        notion_ops_client._notion.pages.retrieve.side_effect = Exception(
            "Could not find page with ID: abc. object_not_found"
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.pages.get("abc")

        assert exc_info.value.resource_type == "Page"
        assert exc_info.value.resource_id == "abc"

    def test_get_page_generic_error(self, notion_ops_client):
        """Get page with generic API error raises NotionOpsError."""
        notion_ops_client._notion.pages.retrieve.side_effect = Exception(
            "Internal server error"
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve page"):
            notion_ops_client.pages.get("page-fail-001")


class TestPageUpdate:
    """Tests for PageOperations.update."""

    def test_update_page(self, notion_ops_client, mock_page_response):
        """Update page success path: calls pages.update with properties."""
        updated_response = mock_page_response(
            page_id="page-upd-001",
            title="Updated Page",
            properties={
                "Name": {
                    "type": "title",
                    "title": [{"plain_text": "Updated Page"}],
                },
                "Status": {
                    "type": "select",
                    "select": {"name": "Done"},
                },
            },
        )
        notion_ops_client._notion.pages.update.return_value = updated_response

        page = notion_ops_client.pages.update(
            "page-upd-001",
            properties={"Status": SelectProperty(value="Done")},
        )

        assert isinstance(page, Page)
        assert page.get_property("Status") == "Done"
        notion_ops_client._notion.pages.update.assert_called_once()

    def test_update_page_no_changes(self, notion_ops_client, mock_page_response):
        """Update with no changes delegates to get (returns existing page)."""
        existing_response = mock_page_response(page_id="page-noop-001")
        notion_ops_client._notion.pages.retrieve.return_value = existing_response

        page = notion_ops_client.pages.update("page-noop-001")

        assert isinstance(page, Page)
        # pages.update should NOT be called; instead pages.retrieve is used
        notion_ops_client._notion.pages.update.assert_not_called()
        notion_ops_client._notion.pages.retrieve.assert_called_once()


class TestPageArchive:
    """Tests for PageOperations.archive."""

    def test_archive_page(self, notion_ops_client, mock_page_response):
        """Archive delegates to update with archived=True."""
        archived_response = mock_page_response(page_id="page-arch-001", archived=True)
        notion_ops_client._notion.pages.update.return_value = archived_response

        page = notion_ops_client.pages.archive("page-arch-001")

        assert isinstance(page, Page)
        assert page.archived is True
        # Verify archived=True was passed in the update call
        call_kwargs = notion_ops_client._notion.pages.update.call_args
        assert call_kwargs.kwargs.get("archived") is True or (
            "archived" in call_kwargs[1] and call_kwargs[1]["archived"] is True
        )


class TestPageRestore:
    """Tests for PageOperations.restore."""

    def test_restore_page(self, notion_ops_client, mock_page_response):
        """Restore delegates to update with archived=False."""
        restored_response = mock_page_response(page_id="page-rest-001", archived=False)
        notion_ops_client._notion.pages.update.return_value = restored_response

        page = notion_ops_client.pages.restore("page-rest-001")

        assert isinstance(page, Page)
        assert page.archived is False
        # Verify archived=False was passed in the update call
        call_kwargs = notion_ops_client._notion.pages.update.call_args
        assert call_kwargs.kwargs.get("archived") is False or (
            "archived" in call_kwargs[1] and call_kwargs[1]["archived"] is False
        )


class TestPageDelete:
    """Tests for PageOperations.delete."""

    def test_delete_page(self, notion_ops_client, mock_page_response):
        """Delete delegates to archive (which delegates to update with archived=True)."""
        archived_response = mock_page_response(page_id="page-del-001", archived=True)
        notion_ops_client._notion.pages.update.return_value = archived_response

        # delete returns None
        result = notion_ops_client.pages.delete("page-del-001")

        assert result is None
        notion_ops_client._notion.pages.update.assert_called_once()


class TestPageGetProperty:
    """Tests for PageOperations.get_property."""

    def test_get_property(self, notion_ops_client):
        """Get property success path: calls pages.properties.retrieve."""
        notion_ops_client._notion.pages.properties.retrieve.return_value = {
            "object": "property_item",
            "type": "rich_text",
            "rich_text": {"type": "text", "text": {"content": "Hello"}},
        }

        result = notion_ops_client.pages.get_property("page-prop-001", "description")

        assert result["type"] == "rich_text"
        notion_ops_client._notion.pages.properties.retrieve.assert_called_once_with(
            page_id="pageprop001",
            property_id="description",
        )

    def test_get_property_error(self, notion_ops_client):
        """Get property with error raises NotionOpsError."""
        notion_ops_client._notion.pages.properties.retrieve.side_effect = Exception(
            "Property not found"
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve property"):
            notion_ops_client.pages.get_property("page-prop-001", "bad_prop")


class TestPageExtractId:
    """Tests for PageOperations._extract_id."""

    def test_extract_id_plain(self, notion_ops_client):
        """Plain ID with dashes gets dashes removed."""
        ops = notion_ops_client.pages
        assert ops._extract_id("abc-def-123") == "abcdef123"

    def test_extract_id_no_dashes(self, notion_ops_client):
        """Plain ID without dashes passes through."""
        ops = notion_ops_client.pages
        assert ops._extract_id("abcdef123") == "abcdef123"

    def test_extract_id_from_url(self, notion_ops_client):
        """Notion URL extracts the 32-char hex ID from end."""
        ops = notion_ops_client.pages
        url = "https://www.notion.so/workspace/Page-Title-abcdef12345678901234567890abcdef"
        result = ops._extract_id(url)
        assert result == "abcdef12345678901234567890abcdef"


class TestPageMove:
    """Tests for PageOperations.move."""

    @patch("httpx.post")
    def test_move_page(
        self, mock_post, notion_ops_client, mock_page_response
    ):
        """Move page success path: posts to /pages/{id}/move."""
        moved_response = mock_page_response(
            page_id="page-move-001",
            title="Moved Page",
            parent_type="database_id",
            parent_id="db-new-parent",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = moved_response
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        # Set up the auth token on the mock client
        notion_ops_client._notion.options = MagicMock()
        notion_ops_client._notion.options.auth = "test-secret-key"

        page = notion_ops_client.pages.move(
            "page-move-001",
            parent_id="db-new-parent",
            parent_type="data_source",
        )

        assert isinstance(page, Page)
        assert page.id == "page-move-001"

        # Verify httpx.post was called with correct URL and payload
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "pagemove001/move" in call_args[0][0]
        assert call_args[1]["json"]["parent"]["type"] == "data_source_id"
        assert (
            call_args[1]["json"]["parent"]["data_source_id"]
            == "dbnewparent"
        )
