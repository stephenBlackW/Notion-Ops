"""Tests for AsyncDataSourceOperations."""

from unittest.mock import AsyncMock

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.database import DataSource, QueryResult
from notion_ops.models.page import Page


class TestAsyncDataSourceGet:
    """Tests for AsyncDataSourceOperations.get."""

    @pytest.mark.asyncio
    async def test_get_data_source(
        self, async_notion_ops_client, mock_database_response
    ):
        """Get data source success path: calls databases.retrieve, returns DataSource."""
        client = async_notion_ops_client
        expected_response = mock_database_response(
            database_id="ds-get-001", title="My Data Source"
        )
        client._notion.databases.retrieve = AsyncMock(
            return_value=expected_response
        )

        ds = await client.data_sources.get("ds-get-001")

        assert isinstance(ds, DataSource)
        assert ds.id == "ds-get-001"
        assert ds.title == "My Data Source"
        client._notion.databases.retrieve.assert_called_once_with(
            database_id="dsget001"
        )

    @pytest.mark.asyncio
    async def test_get_data_source_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Get data source with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.databases.retrieve = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find data source."
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.data_sources.get("ds-missing-001")

        assert exc_info.value.resource_type == "DataSource"
        assert exc_info.value.resource_id == "dsmissing001"

    @pytest.mark.asyncio
    async def test_get_data_source_generic_error(self, async_notion_ops_client):
        """Get data source with generic error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.databases.retrieve = AsyncMock(
            side_effect=Exception("Unexpected error")
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve data source"):
            await client.data_sources.get("ds-err-001")


class TestAsyncDataSourceQuery:
    """Tests for AsyncDataSourceOperations.query."""

    @pytest.mark.asyncio
    async def test_query_data_source(
        self, async_notion_ops_client, mock_page_response
    ):
        """Query data source success path: uses _notion.request() and returns QueryResult."""
        client = async_notion_ops_client
        page1 = mock_page_response(page_id="page-q1", title="Result 1")
        page2 = mock_page_response(page_id="page-q2", title="Result 2")

        client._notion.request = AsyncMock(
            return_value={
                "object": "list",
                "results": [page1, page2],
                "has_more": False,
                "next_cursor": None,
                "type": "page_or_database",
            }
        )

        result = await client.data_sources.query(
            "ds-query-001",
            filter={"property": "Status", "select": {"equals": "Active"}},
        )

        assert isinstance(result, QueryResult)
        assert len(result.pages) == 2
        assert isinstance(result.pages[0], Page)
        assert result.pages[0].id == "page-q1"
        assert result.has_more is False

        # Verify it uses _notion.request, NOT _notion.databases.query
        client._notion.request.assert_called_once()
        call_kwargs = client._notion.request.call_args.kwargs
        assert call_kwargs["path"] == "data_sources/dsquery001/query"
        assert call_kwargs["method"] == "POST"
        assert "filter" in call_kwargs["body"]

    @pytest.mark.asyncio
    async def test_query_data_source_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Query against missing data source raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.request = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.data_sources.query("ds-missing-001")

        assert exc_info.value.resource_type == "DataSource"

    @pytest.mark.asyncio
    async def test_query_data_source_generic_error(self, async_notion_ops_client):
        """Query with generic error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.request = AsyncMock(
            side_effect=Exception("Internal server error")
        )

        with pytest.raises(NotionOpsError, match="Failed to query data source"):
            await client.data_sources.query("ds-err-001")

    @pytest.mark.asyncio
    async def test_query_with_sorts(self, async_notion_ops_client, mock_page_response):
        """Query with sorts passes them through to the request body."""
        client = async_notion_ops_client
        page1 = mock_page_response(page_id="page-sorted")
        client._notion.request = AsyncMock(
            return_value={
                "object": "list",
                "results": [page1],
                "has_more": False,
                "next_cursor": None,
            }
        )

        test_sorts = [{"property": "Name", "direction": "ascending"}]
        await client.data_sources.query("ds-sort-001", sorts=test_sorts)

        call_kwargs = client._notion.request.call_args.kwargs
        assert call_kwargs["body"]["sorts"] == test_sorts


class TestAsyncDataSourceQueryAll:
    """Tests for AsyncDataSourceOperations.query_all (pagination)."""

    @pytest.mark.asyncio
    async def test_query_all(self, async_notion_ops_client, mock_page_response):
        """query_all handles pagination across multiple pages of results."""
        client = async_notion_ops_client
        page1 = mock_page_response(page_id="page-p1", title="Page 1")
        page2 = mock_page_response(page_id="page-p2", title="Page 2")
        page3 = mock_page_response(page_id="page-p3", title="Page 3")

        # First call returns 2 results with has_more=True
        # Second call returns 1 result with has_more=False
        client._notion.request = AsyncMock(
            side_effect=[
                {
                    "object": "list",
                    "results": [page1, page2],
                    "has_more": True,
                    "next_cursor": "cursor-abc",
                },
                {
                    "object": "list",
                    "results": [page3],
                    "has_more": False,
                    "next_cursor": None,
                },
            ]
        )

        pages = []
        async for page in client.data_sources.query_all("ds-paginated-001"):
            pages.append(page)

        assert len(pages) == 3
        assert pages[0].id == "page-p1"
        assert pages[1].id == "page-p2"
        assert pages[2].id == "page-p3"

        # Verify two calls were made
        assert client._notion.request.call_count == 2

        # The second call should include the start_cursor
        second_call = client._notion.request.call_args_list[1]
        assert second_call.kwargs["body"]["start_cursor"] == "cursor-abc"

    @pytest.mark.asyncio
    async def test_query_all_empty(self, async_notion_ops_client):
        """query_all with empty results yields nothing."""
        client = async_notion_ops_client
        client._notion.request = AsyncMock(
            return_value={
                "object": "list",
                "results": [],
                "has_more": False,
                "next_cursor": None,
            }
        )

        pages = []
        async for page in client.data_sources.query_all("ds-empty-001"):
            pages.append(page)

        assert len(pages) == 0


class TestAsyncDataSourceCount:
    """Tests for AsyncDataSourceOperations.count."""

    @pytest.mark.asyncio
    async def test_count(self, async_notion_ops_client, mock_page_response):
        """Count iterates through all pages via query_all and returns count."""
        client = async_notion_ops_client
        page1 = mock_page_response(page_id="page-c1")
        page2 = mock_page_response(page_id="page-c2")

        client._notion.request = AsyncMock(
            return_value={
                "object": "list",
                "results": [page1, page2],
                "has_more": False,
                "next_cursor": None,
            }
        )

        count = await client.data_sources.count("ds-count-001")

        assert count == 2

    @pytest.mark.asyncio
    async def test_count_with_filter(
        self, async_notion_ops_client, mock_page_response
    ):
        """Count with filter passes filter through to query."""
        client = async_notion_ops_client
        page1 = mock_page_response(page_id="page-cf1")

        client._notion.request = AsyncMock(
            return_value={
                "object": "list",
                "results": [page1],
                "has_more": False,
                "next_cursor": None,
            }
        )

        test_filter = {"property": "Status", "select": {"equals": "Active"}}
        count = await client.data_sources.count(
            "ds-count-002", filter=test_filter
        )

        assert count == 1
        # Verify filter was passed in the request body
        call_kwargs = client._notion.request.call_args.kwargs
        assert call_kwargs["body"]["filter"] == test_filter

    @pytest.mark.asyncio
    async def test_count_empty(self, async_notion_ops_client):
        """Count returns 0 for empty data source."""
        client = async_notion_ops_client
        client._notion.request = AsyncMock(
            return_value={
                "object": "list",
                "results": [],
                "has_more": False,
                "next_cursor": None,
            }
        )

        count = await client.data_sources.count("ds-empty-001")

        assert count == 0
