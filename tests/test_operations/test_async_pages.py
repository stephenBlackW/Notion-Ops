"""Tests for AsyncPageOperations."""

from unittest.mock import AsyncMock

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.page import Page
from notion_ops.models.properties import SelectProperty, TitleProperty


class TestAsyncPageGet:
    """Tests for AsyncPageOperations.get."""

    @pytest.mark.asyncio
    async def test_get_page(self, async_notion_ops_client, mock_page_response):
        """Get page success path: calls pages.retrieve and returns Page."""
        client = async_notion_ops_client
        expected_response = mock_page_response(
            page_id="page-get-001", title="Fetched Page"
        )
        client._notion.pages.retrieve = AsyncMock(return_value=expected_response)

        page = await client.pages.get("page-get-001")

        assert isinstance(page, Page)
        assert page.id == "page-get-001"
        assert page.get_title() == "Fetched Page"
        client._notion.pages.retrieve.assert_called_once_with(
            page_id="pageget001"
        )

    @pytest.mark.asyncio
    async def test_get_page_not_found(self, async_notion_ops_client, make_api_error):
        """Get page with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.pages.retrieve = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find page with ID: abc"
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.pages.get("abc")

        assert exc_info.value.resource_type == "Page"
        assert exc_info.value.resource_id == "abc"

    @pytest.mark.asyncio
    async def test_get_page_generic_error(self, async_notion_ops_client):
        """Get page with generic API error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.pages.retrieve = AsyncMock(
            side_effect=Exception("Internal server error")
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve page"):
            await client.pages.get("page-fail-001")


class TestAsyncPageCreate:
    """Tests for AsyncPageOperations.create."""

    @pytest.mark.asyncio
    async def test_create_page(self, async_notion_ops_client, mock_page_response):
        """Create page success path: calls pages.create and returns Page."""
        client = async_notion_ops_client
        expected_response = mock_page_response(title="New Page")
        client._notion.pages.create = AsyncMock(return_value=expected_response)

        page = await client.pages.create(
            parent_id="db-xyz789",
            properties={
                "Name": TitleProperty(value="New Page"),
            },
        )

        assert isinstance(page, Page)
        assert page.id == "page-abc123"
        assert page.get_title() == "New Page"
        client._notion.pages.create.assert_called_once()

        # Verify the call kwargs contain parent and properties
        call_kwargs = client._notion.pages.create.call_args
        assert "parent" in call_kwargs.kwargs or "parent" in (
            call_kwargs[1] if len(call_kwargs) > 1 else {}
        )

    @pytest.mark.asyncio
    async def test_create_page_error(self, async_notion_ops_client):
        """Generic error during create raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.pages.create = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        with pytest.raises(NotionOpsError, match="Failed to create page"):
            await client.pages.create(
                parent_id="db-xyz789",
                properties={"Name": TitleProperty(value="Failing Page")},
            )

    @pytest.mark.asyncio
    async def test_create_page_api_error(self, async_notion_ops_client, make_api_error):
        """APIResponseError during create raises mapped NotionOpsError."""
        client = async_notion_ops_client
        client._notion.pages.create = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find parent."
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.pages.create(
                parent_id="db-missing",
                properties={"Name": TitleProperty(value="Orphan")},
            )

        assert exc_info.value.resource_type == "Page"


class TestAsyncPageUpdate:
    """Tests for AsyncPageOperations.update."""

    @pytest.mark.asyncio
    async def test_update_page(self, async_notion_ops_client, mock_page_response):
        """Update page success path: calls pages.update with properties."""
        client = async_notion_ops_client
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
        client._notion.pages.update = AsyncMock(return_value=updated_response)

        page = await client.pages.update(
            "page-upd-001",
            properties={"Status": SelectProperty(value="Done")},
        )

        assert isinstance(page, Page)
        assert page.get_property("Status") == "Done"
        client._notion.pages.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_page_no_changes(
        self, async_notion_ops_client, mock_page_response
    ):
        """Update with no changes delegates to get (returns existing page)."""
        client = async_notion_ops_client
        existing_response = mock_page_response(page_id="page-noop-001")
        client._notion.pages.retrieve = AsyncMock(return_value=existing_response)
        client._notion.pages.update = AsyncMock()

        page = await client.pages.update("page-noop-001")

        assert isinstance(page, Page)
        # pages.update should NOT be called; instead pages.retrieve is used
        client._notion.pages.update.assert_not_called()
        client._notion.pages.retrieve.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_page_not_found(self, async_notion_ops_client, make_api_error):
        """Update page with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.pages.update = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.pages.update(
                "page-missing-001",
                properties={"Status": SelectProperty(value="Done")},
            )

        assert exc_info.value.resource_type == "Page"


class TestAsyncPageMove:
    """Tests for AsyncPageOperations.move."""

    @pytest.mark.asyncio
    async def test_move_page(self, async_notion_ops_client, mock_page_response):
        """Move page success path: calls SDK pages.move with parent payload."""
        client = async_notion_ops_client
        moved_response = mock_page_response(
            page_id="page-move-001",
            title="Moved Page",
            parent_type="database_id",
            parent_id="db-new-parent",
        )
        client._notion.pages.move = AsyncMock(return_value=moved_response)

        page = await client.pages.move(
            "page-move-001",
            parent_id="db-new-parent",
            parent_type="data_source",
        )

        assert isinstance(page, Page)
        assert page.id == "page-move-001"

        # Verify SDK pages.move was called with correct arguments
        client._notion.pages.move.assert_called_once_with(
            page_id="pagemove001",
            parent={
                "type": "data_source_id",
                "data_source_id": "dbnewparent",
            },
        )

    @pytest.mark.asyncio
    async def test_move_page_to_page_parent(
        self, async_notion_ops_client, mock_page_response
    ):
        """Move page to a page parent uses page_id key."""
        client = async_notion_ops_client
        moved_response = mock_page_response(
            page_id="page-move-002",
            parent_type="page_id",
            parent_id="page-new-parent",
        )
        client._notion.pages.move = AsyncMock(return_value=moved_response)

        await client.pages.move(
            "page-move-002",
            parent_id="page-new-parent",
            parent_type="page",
        )

        client._notion.pages.move.assert_called_once_with(
            page_id="pagemove002",
            parent={
                "type": "page_id",
                "page_id": "pagenewparent",
            },
        )

    @pytest.mark.asyncio
    async def test_move_page_not_found(self, async_notion_ops_client, make_api_error):
        """Move page with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.pages.move = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.pages.move(
                "page-missing-001",
                parent_id="db-parent",
            )

        assert exc_info.value.resource_type == "Page"

    @pytest.mark.asyncio
    async def test_move_page_generic_error(self, async_notion_ops_client):
        """Move page with generic error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.pages.move = AsyncMock(
            side_effect=Exception("Move failed")
        )

        with pytest.raises(NotionOpsError, match="Failed to move page"):
            await client.pages.move(
                "page-err-001",
                parent_id="db-parent",
            )


class TestAsyncPageArchiveRestore:
    """Tests for AsyncPageOperations.archive and restore."""

    @pytest.mark.asyncio
    async def test_archive_page(self, async_notion_ops_client, mock_page_response):
        """Archive delegates to update with archived=True."""
        client = async_notion_ops_client
        archived_response = mock_page_response(
            page_id="page-arch-001", archived=True
        )
        client._notion.pages.update = AsyncMock(return_value=archived_response)

        page = await client.pages.archive("page-arch-001")

        assert isinstance(page, Page)
        assert page.archived is True

    @pytest.mark.asyncio
    async def test_restore_page(self, async_notion_ops_client, mock_page_response):
        """Restore delegates to update with archived=False."""
        client = async_notion_ops_client
        restored_response = mock_page_response(
            page_id="page-rest-001", archived=False
        )
        client._notion.pages.update = AsyncMock(return_value=restored_response)

        page = await client.pages.restore("page-rest-001")

        assert isinstance(page, Page)
        assert page.archived is False


class TestAsyncPageDelete:
    """Tests for AsyncPageOperations.delete."""

    @pytest.mark.asyncio
    async def test_delete_page(self, async_notion_ops_client, mock_page_response):
        """Delete delegates to archive (update with archived=True)."""
        client = async_notion_ops_client
        archived_response = mock_page_response(
            page_id="page-del-001", archived=True
        )
        client._notion.pages.update = AsyncMock(return_value=archived_response)

        result = await client.pages.delete("page-del-001")

        assert result is None
