"""Tests for PageOperations (sync) and AsyncPageOperations (async).

Both code paths are exercised via the parametrised ``ops`` fixture.
"""

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.page import Page
from notion_ops.models.properties import SelectProperty, TitleProperty

from .conftest import maybe_await

# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestPageCreate:
    """Tests for create (sync & async)."""

    @pytest.mark.asyncio
    async def test_create_page(self, ops, mock_page_response):
        expected = mock_page_response(title="New Page")
        ops.setup_mock("pages.create", return_value=expected)

        page = await maybe_await(
            ops.pages.create(
                parent_id="db-xyz789",
                properties={"Name": TitleProperty(value="New Page")},
            )
        )

        assert isinstance(page, Page)
        assert page.id == "page-abc123"
        assert page.get_title() == "New Page"
        ops.get_mock("pages.create").assert_called_once()

        call_kwargs = ops.get_mock("pages.create").call_args
        assert "parent" in call_kwargs.kwargs or "parent" in (
            call_kwargs[1] if len(call_kwargs) > 1 else {}
        )

    @pytest.mark.asyncio
    async def test_create_page_error(self, ops):
        ops.setup_mock("pages.create", side_effect=Exception("API connection failed"))

        with pytest.raises(NotionOpsError, match="Failed to create page"):
            await maybe_await(
                ops.pages.create(
                    parent_id="db-xyz789",
                    properties={"Name": TitleProperty(value="Failing Page")},
                )
            )

    @pytest.mark.asyncio
    async def test_create_page_api_error(self, ops, make_api_error):
        ops.setup_mock(
            "pages.create",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find parent."
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(
                ops.pages.create(
                    parent_id="db-missing",
                    properties={"Name": TitleProperty(value="Orphan")},
                )
            )

        assert exc_info.value.resource_type == "Page"


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestPageGet:
    """Tests for get (sync & async)."""

    @pytest.mark.asyncio
    async def test_get_page(self, ops, mock_page_response):
        expected = mock_page_response(page_id="page-get-001", title="Fetched Page")
        ops.setup_mock("pages.retrieve", return_value=expected)

        page = await maybe_await(ops.pages.get("page-get-001"))

        assert isinstance(page, Page)
        assert page.id == "page-get-001"
        assert page.get_title() == "Fetched Page"
        ops.get_mock("pages.retrieve").assert_called_once_with(page_id="pageget001")

    @pytest.mark.asyncio
    async def test_get_page_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "pages.retrieve",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find page with ID: abc"
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.pages.get("abc"))

        assert exc_info.value.resource_type == "Page"
        assert exc_info.value.resource_id == "abc"

    @pytest.mark.asyncio
    async def test_get_page_generic_error(self, ops):
        ops.setup_mock(
            "pages.retrieve", side_effect=Exception("Internal server error")
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve page"):
            await maybe_await(ops.pages.get("page-fail-001"))


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestPageUpdate:
    """Tests for update (sync & async)."""

    @pytest.mark.asyncio
    async def test_update_page(self, ops, mock_page_response):
        updated = mock_page_response(
            page_id="page-upd-001",
            title="Updated Page",
            properties={
                "Name": {"type": "title", "title": [{"plain_text": "Updated Page"}]},
                "Status": {"type": "select", "select": {"name": "Done"}},
            },
        )
        ops.setup_mock("pages.update", return_value=updated)

        page = await maybe_await(
            ops.pages.update(
                "page-upd-001",
                properties={"Status": SelectProperty(value="Done")},
            )
        )

        assert isinstance(page, Page)
        assert page.get_property("Status") == "Done"
        ops.get_mock("pages.update").assert_called_once()

    @pytest.mark.asyncio
    async def test_update_page_no_changes(self, ops, mock_page_response):
        existing = mock_page_response(page_id="page-noop-001")
        ops.setup_mock("pages.retrieve", return_value=existing)
        # Ensure update mock exists but should NOT be called
        if ops.is_async:
            ops.setup_mock("pages.update")

        page = await maybe_await(ops.pages.update("page-noop-001"))

        assert isinstance(page, Page)
        ops.get_mock("pages.update").assert_not_called()
        ops.get_mock("pages.retrieve").assert_called_once()

    @pytest.mark.asyncio
    async def test_update_page_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "pages.update",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(
                ops.pages.update(
                    "page-missing-001",
                    properties={"Status": SelectProperty(value="Done")},
                )
            )

        assert exc_info.value.resource_type == "Page"


# ---------------------------------------------------------------------------
# Archive / Restore / Delete
# ---------------------------------------------------------------------------


class TestPageArchiveRestoreDelete:
    """Tests for archive, restore, and delete (sync & async)."""

    @pytest.mark.asyncio
    async def test_archive_page(self, ops, mock_page_response):
        archived = mock_page_response(page_id="page-arch-001", archived=True)
        ops.setup_mock("pages.update", return_value=archived)

        page = await maybe_await(ops.pages.archive("page-arch-001"))

        assert isinstance(page, Page)
        assert page.archived is True

    @pytest.mark.asyncio
    async def test_restore_page(self, ops, mock_page_response):
        restored = mock_page_response(page_id="page-rest-001", archived=False)
        ops.setup_mock("pages.update", return_value=restored)

        page = await maybe_await(ops.pages.restore("page-rest-001"))

        assert isinstance(page, Page)
        assert page.archived is False

    @pytest.mark.asyncio
    async def test_delete_page(self, ops, mock_page_response):
        archived = mock_page_response(page_id="page-del-001", archived=True)
        ops.setup_mock("pages.update", return_value=archived)

        result = await maybe_await(ops.pages.delete("page-del-001"))

        assert result is None


# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------


class TestPageMove:
    """Tests for move (sync & async)."""

    @pytest.mark.asyncio
    async def test_move_page(self, ops, mock_page_response):
        moved = mock_page_response(
            page_id="page-move-001",
            title="Moved Page",
            parent_type="database_id",
            parent_id="db-new-parent",
        )
        ops.setup_mock("pages.move", return_value=moved)

        page = await maybe_await(
            ops.pages.move(
                "page-move-001",
                parent_id="db-new-parent",
                parent_type="data_source",
            )
        )

        assert isinstance(page, Page)
        assert page.id == "page-move-001"
        ops.get_mock("pages.move").assert_called_once_with(
            page_id="pagemove001",
            parent={
                "type": "data_source_id",
                "data_source_id": "dbnewparent",
            },
        )

    @pytest.mark.asyncio
    async def test_move_page_to_page_parent(self, ops, mock_page_response):
        moved = mock_page_response(
            page_id="page-move-002",
            parent_type="page_id",
            parent_id="page-new-parent",
        )
        ops.setup_mock("pages.move", return_value=moved)

        await maybe_await(
            ops.pages.move(
                "page-move-002",
                parent_id="page-new-parent",
                parent_type="page",
            )
        )

        ops.get_mock("pages.move").assert_called_once_with(
            page_id="pagemove002",
            parent={
                "type": "page_id",
                "page_id": "pagenewparent",
            },
        )

    @pytest.mark.asyncio
    async def test_move_page_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "pages.move",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(
                ops.pages.move("page-missing-001", parent_id="db-parent")
            )

        assert exc_info.value.resource_type == "Page"

    @pytest.mark.asyncio
    async def test_move_page_generic_error(self, ops):
        ops.setup_mock("pages.move", side_effect=Exception("Move failed"))

        with pytest.raises(NotionOpsError, match="Failed to move page"):
            await maybe_await(
                ops.pages.move("page-err-001", parent_id="db-parent")
            )


# ---------------------------------------------------------------------------
# get_property
# ---------------------------------------------------------------------------


class TestPageGetProperty:
    """Tests for get_property (sync & async)."""

    @pytest.mark.asyncio
    async def test_get_property(self, ops):
        ops.setup_mock(
            "pages.properties.retrieve",
            return_value={
                "object": "property_item",
                "type": "rich_text",
                "rich_text": {"type": "text", "text": {"content": "Hello"}},
            },
        )

        result = await maybe_await(
            ops.pages.get_property("page-prop-001", "description")
        )

        assert result["type"] == "rich_text"
        ops.get_mock("pages.properties.retrieve").assert_called_once_with(
            page_id="pageprop001",
            property_id="description",
        )

    @pytest.mark.asyncio
    async def test_get_property_error(self, ops):
        ops.setup_mock(
            "pages.properties.retrieve",
            side_effect=Exception("Property not found"),
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve property"):
            await maybe_await(
                ops.pages.get_property("page-prop-001", "bad_prop")
            )


# ---------------------------------------------------------------------------
# _extract_id
# ---------------------------------------------------------------------------


class TestPageExtractId:
    """Tests for _extract_id (sync & async)."""

    def test_extract_id_plain(self, ops):
        assert ops.pages._extract_id("abc-def-123") == "abcdef123"

    def test_extract_id_no_dashes(self, ops):
        assert ops.pages._extract_id("abcdef123") == "abcdef123"

    def test_extract_id_from_url(self, ops):
        url = "https://www.notion.so/workspace/Page-Title-abcdef12345678901234567890abcdef"
        assert ops.pages._extract_id(url) == "abcdef12345678901234567890abcdef"
