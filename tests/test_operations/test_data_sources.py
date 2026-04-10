"""Tests for DataSourceOperations (sync) and AsyncDataSourceOperations (async).

Both code paths are exercised via the parametrised ``ops`` fixture.
"""

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.database import DataSource, QueryResult
from notion_ops.models.page import Page
from notion_ops.models.properties import PropertyDefinition, PropertyType

from .conftest import collect_iter, maybe_await

# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestDataSourceGet:
    """Tests for get (sync & async)."""

    @pytest.mark.asyncio
    async def test_get_data_source(self, ops, mock_database_response):
        expected = mock_database_response(
            database_id="ds-get-001", title="My Data Source"
        )
        ops.setup_mock("databases.retrieve", return_value=expected)

        ds = await maybe_await(ops.data_sources.get("ds-get-001"))

        assert isinstance(ds, DataSource)
        assert ds.id == "ds-get-001"
        assert ds.title == "My Data Source"
        ops.get_mock("databases.retrieve").assert_called_once_with(
            database_id="dsget001"
        )

    @pytest.mark.asyncio
    async def test_get_data_source_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "databases.retrieve",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find data source."
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.data_sources.get("ds-missing-001"))

        assert exc_info.value.resource_type == "DataSource"
        assert exc_info.value.resource_id == "dsmissing001"

    @pytest.mark.asyncio
    async def test_get_data_source_generic_error(self, ops):
        ops.setup_mock(
            "databases.retrieve", side_effect=Exception("Unexpected error")
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve data source"):
            await maybe_await(ops.data_sources.get("ds-err-001"))


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class TestDataSourceQuery:
    """Tests for query (sync & async)."""

    @pytest.mark.asyncio
    async def test_query_data_source(self, ops, mock_page_response):
        page1 = mock_page_response(page_id="page-q1", title="Result 1")
        page2 = mock_page_response(page_id="page-q2", title="Result 2")

        ops.setup_mock(
            "request",
            return_value={
                "object": "list",
                "results": [page1, page2],
                "has_more": False,
                "next_cursor": None,
                "type": "page_or_database",
            },
        )

        result = await maybe_await(
            ops.data_sources.query(
                "ds-query-001",
                filter={"property": "Status", "select": {"equals": "Active"}},
            )
        )

        assert isinstance(result, QueryResult)
        assert len(result.pages) == 2
        assert isinstance(result.pages[0], Page)
        assert result.pages[0].id == "page-q1"
        assert result.has_more is False

        ops.get_mock("request").assert_called_once()
        call_kwargs = ops.get_mock("request").call_args.kwargs
        assert call_kwargs["path"] == "data_sources/dsquery001/query"
        assert call_kwargs["method"] == "POST"
        assert "filter" in call_kwargs["body"]

    @pytest.mark.asyncio
    async def test_query_data_source_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "request",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.data_sources.query("ds-missing-001"))

        assert exc_info.value.resource_type == "DataSource"

    @pytest.mark.asyncio
    async def test_query_data_source_generic_error(self, ops):
        ops.setup_mock(
            "request", side_effect=Exception("Internal server error")
        )

        with pytest.raises(NotionOpsError, match="Failed to query data source"):
            await maybe_await(ops.data_sources.query("ds-err-001"))

    @pytest.mark.asyncio
    async def test_query_with_sorts(self, ops, mock_page_response):
        page1 = mock_page_response(page_id="page-sorted")
        ops.setup_mock(
            "request",
            return_value={
                "object": "list",
                "results": [page1],
                "has_more": False,
                "next_cursor": None,
            },
        )

        test_sorts = [{"property": "Name", "direction": "ascending"}]
        await maybe_await(
            ops.data_sources.query("ds-sort-001", sorts=test_sorts)
        )

        call_kwargs = ops.get_mock("request").call_args.kwargs
        assert call_kwargs["body"]["sorts"] == test_sorts


# ---------------------------------------------------------------------------
# Query all (pagination)
# ---------------------------------------------------------------------------


class TestDataSourceQueryAll:
    """Tests for query_all (sync & async)."""

    @pytest.mark.asyncio
    async def test_query_all(self, ops, mock_page_response):
        page1 = mock_page_response(page_id="page-p1", title="Page 1")
        page2 = mock_page_response(page_id="page-p2", title="Page 2")
        page3 = mock_page_response(page_id="page-p3", title="Page 3")

        ops.setup_mock(
            "request",
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
            ],
        )

        pages = await collect_iter(ops.data_sources.query_all("ds-paginated-001"))

        assert len(pages) == 3
        assert pages[0].id == "page-p1"
        assert pages[1].id == "page-p2"
        assert pages[2].id == "page-p3"

        mock = ops.get_mock("request")
        assert mock.call_count == 2
        second_call = mock.call_args_list[1]
        assert second_call.kwargs["body"]["start_cursor"] == "cursor-abc"

    @pytest.mark.asyncio
    async def test_query_all_empty(self, ops):
        ops.setup_mock(
            "request",
            return_value={
                "object": "list",
                "results": [],
                "has_more": False,
                "next_cursor": None,
            },
        )

        pages = await collect_iter(ops.data_sources.query_all("ds-empty-001"))

        assert len(pages) == 0


# ---------------------------------------------------------------------------
# Update schema
# ---------------------------------------------------------------------------


class TestDataSourceUpdateSchema:
    """Tests for update_schema (sync & async)."""

    @pytest.mark.asyncio
    async def test_update_schema(self, ops, mock_database_response):
        updated = mock_database_response(database_id="ds-schema-001")
        ops.setup_mock("databases.update", return_value=updated)

        prop_def = PropertyDefinition(
            name="Priority",
            type=PropertyType.SELECT,
            options={
                "options": [
                    {"name": "High", "color": "red"},
                    {"name": "Low", "color": "green"},
                ]
            },
        )

        ds = await maybe_await(
            ops.data_sources.update_schema(
                "ds-schema-001", properties={"Priority": prop_def}
            )
        )

        assert isinstance(ds, DataSource)
        ops.get_mock("databases.update").assert_called_once()
        call_kwargs = ops.get_mock("databases.update").call_args.kwargs
        assert "Priority" in call_kwargs["properties"]

    @pytest.mark.asyncio
    async def test_update_schema_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "databases.update",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        prop_def = PropertyDefinition(name="X", type=PropertyType.CHECKBOX)

        with pytest.raises(NotFoundError):
            await maybe_await(
                ops.data_sources.update_schema(
                    "ds-missing-001", properties={"X": prop_def}
                )
            )


# ---------------------------------------------------------------------------
# Count
# ---------------------------------------------------------------------------


class TestDataSourceCount:
    """Tests for count (sync & async)."""

    @pytest.mark.asyncio
    async def test_count(self, ops, mock_page_response):
        page1 = mock_page_response(page_id="page-c1")
        page2 = mock_page_response(page_id="page-c2")

        ops.setup_mock(
            "request",
            return_value={
                "object": "list",
                "results": [page1, page2],
                "has_more": False,
                "next_cursor": None,
            },
        )

        count = await maybe_await(ops.data_sources.count("ds-count-001"))

        assert count == 2

    @pytest.mark.asyncio
    async def test_count_with_filter(self, ops, mock_page_response):
        page1 = mock_page_response(page_id="page-cf1")

        ops.setup_mock(
            "request",
            return_value={
                "object": "list",
                "results": [page1],
                "has_more": False,
                "next_cursor": None,
            },
        )

        test_filter = {"property": "Status", "select": {"equals": "Active"}}
        count = await maybe_await(
            ops.data_sources.count("ds-count-002", filter=test_filter)
        )

        assert count == 1
        call_kwargs = ops.get_mock("request").call_args.kwargs
        assert call_kwargs["body"]["filter"] == test_filter

    @pytest.mark.asyncio
    async def test_count_empty(self, ops):
        ops.setup_mock(
            "request",
            return_value={
                "object": "list",
                "results": [],
                "has_more": False,
                "next_cursor": None,
            },
        )

        count = await maybe_await(ops.data_sources.count("ds-empty-001"))

        assert count == 0


# ---------------------------------------------------------------------------
# Delete property
# ---------------------------------------------------------------------------


class TestDataSourceDeleteProperty:
    """Tests for delete_property (sync & async)."""

    @pytest.mark.asyncio
    async def test_delete_property(self, ops, mock_database_response):
        updated = mock_database_response(database_id="ds-delprop-001")
        ops.setup_mock("databases.update", return_value=updated)

        ds = await maybe_await(
            ops.data_sources.delete_property("ds-delprop-001", "ObsoleteColumn")
        )

        assert isinstance(ds, DataSource)
        ops.get_mock("databases.update").assert_called_once_with(
            database_id="dsdelprop001",
            properties={"ObsoleteColumn": None},
        )


# ---------------------------------------------------------------------------
# _extract_id
# ---------------------------------------------------------------------------


class TestDataSourceExtractId:
    """Tests for _extract_id (sync & async)."""

    def test_extract_id_plain(self, ops):
        assert ops.data_sources._extract_id("2d8d-371a-79f4") == "2d8d371a79f4"

    def test_extract_id_from_url(self, ops):
        url = "https://www.notion.so/workspace/DB-Title-abcdef1234567890abcdef1234567890"
        assert (
            ops.data_sources._extract_id(url)
            == "abcdef1234567890abcdef1234567890"
        )
