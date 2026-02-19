"""Tests for DataSourceOperations."""

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.database import DataSource, QueryResult
from notion_ops.models.page import Page


class TestDataSourceGet:
    """Tests for DataSourceOperations.get."""

    def test_get_data_source(self, notion_ops_client, mock_database_response):
        """Get data source success path: calls databases.retrieve, returns DataSource."""
        expected_response = mock_database_response(
            database_id="ds-get-001", title="My Data Source"
        )
        notion_ops_client._notion.databases.retrieve.return_value = expected_response

        ds = notion_ops_client.data_sources.get("ds-get-001")

        assert isinstance(ds, DataSource)
        assert ds.id == "ds-get-001"
        assert ds.title == "My Data Source"
        notion_ops_client._notion.databases.retrieve.assert_called_once_with(
            database_id="dsget001"
        )

    def test_get_data_source_not_found(self, notion_ops_client, make_api_error):
        """Get data source with invalid ID raises NotFoundError."""
        notion_ops_client._notion.databases.retrieve.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "Could not find data source."
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.data_sources.get("ds-missing-001")

        assert exc_info.value.resource_type == "DataSource"
        assert exc_info.value.resource_id == "dsmissing001"


class TestDataSourceQuery:
    """Tests for DataSourceOperations.query."""

    def test_query_data_source(self, notion_ops_client, mock_page_response):
        """Query data source success path: uses _notion.request() and returns QueryResult."""
        page1 = mock_page_response(page_id="page-q1", title="Result 1")
        page2 = mock_page_response(page_id="page-q2", title="Result 2")

        notion_ops_client._notion.request.return_value = {
            "object": "list",
            "results": [page1, page2],
            "has_more": False,
            "next_cursor": None,
            "type": "page_or_database",
        }

        result = notion_ops_client.data_sources.query(
            "ds-query-001",
            filter={"property": "Status", "select": {"equals": "Active"}},
        )

        assert isinstance(result, QueryResult)
        assert len(result.pages) == 2
        assert isinstance(result.pages[0], Page)
        assert result.pages[0].id == "page-q1"
        assert result.has_more is False

        # Verify it uses _notion.request, NOT _notion.databases.query
        notion_ops_client._notion.request.assert_called_once()
        call_kwargs = notion_ops_client._notion.request.call_args.kwargs
        assert call_kwargs["path"] == "databases/dsquery001/query"
        assert call_kwargs["method"] == "POST"
        assert "filter" in call_kwargs["body"]

    def test_query_data_source_not_found(self, notion_ops_client, make_api_error):
        """Query against missing data source raises NotFoundError."""
        notion_ops_client._notion.request.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "object_not_found"
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.data_sources.query("ds-missing-001")

        assert exc_info.value.resource_type == "DataSource"

    def test_query_data_source_generic_error(self, notion_ops_client):
        """Query with generic error raises NotionOpsError."""
        notion_ops_client._notion.request.side_effect = Exception(
            "Internal server error"
        )

        with pytest.raises(NotionOpsError, match="Failed to query data source"):
            notion_ops_client.data_sources.query("ds-err-001")


class TestDataSourceQueryAll:
    """Tests for DataSourceOperations.query_all (pagination)."""

    def test_query_all(self, notion_ops_client, mock_page_response):
        """query_all handles pagination across multiple pages of results."""
        page1 = mock_page_response(page_id="page-p1", title="Page 1")
        page2 = mock_page_response(page_id="page-p2", title="Page 2")
        page3 = mock_page_response(page_id="page-p3", title="Page 3")

        # First call returns 2 results with has_more=True
        # Second call returns 1 result with has_more=False
        notion_ops_client._notion.request.side_effect = [
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

        pages = list(notion_ops_client.data_sources.query_all("ds-paginated-001"))

        assert len(pages) == 3
        assert pages[0].id == "page-p1"
        assert pages[1].id == "page-p2"
        assert pages[2].id == "page-p3"

        # Verify two calls were made
        assert notion_ops_client._notion.request.call_count == 2

        # The second call should include the start_cursor
        second_call = notion_ops_client._notion.request.call_args_list[1]
        assert second_call.kwargs["body"]["start_cursor"] == "cursor-abc"


class TestDataSourceUpdateSchema:
    """Tests for DataSourceOperations.update_schema."""

    def test_update_schema(self, notion_ops_client, mock_database_response):
        """Update schema success path: calls databases.update with properties."""
        from notion_ops.models.properties import PropertyDefinition, PropertyType

        updated_response = mock_database_response(database_id="ds-schema-001")
        notion_ops_client._notion.databases.update.return_value = updated_response

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

        ds = notion_ops_client.data_sources.update_schema(
            "ds-schema-001",
            properties={"Priority": prop_def},
        )

        assert isinstance(ds, DataSource)
        notion_ops_client._notion.databases.update.assert_called_once()

        call_kwargs = notion_ops_client._notion.databases.update.call_args.kwargs
        assert "Priority" in call_kwargs["properties"]

    def test_update_schema_not_found(self, notion_ops_client, make_api_error):
        """Update schema on missing data source raises NotFoundError."""
        from notion_ops.models.properties import PropertyDefinition, PropertyType

        notion_ops_client._notion.databases.update.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "object_not_found"
        )

        prop_def = PropertyDefinition(name="X", type=PropertyType.CHECKBOX)

        with pytest.raises(NotFoundError):
            notion_ops_client.data_sources.update_schema(
                "ds-missing-001",
                properties={"X": prop_def},
            )


class TestDataSourceCount:
    """Tests for DataSourceOperations.count."""

    def test_count(self, notion_ops_client, mock_page_response):
        """Count iterates through all pages via query_all and returns count."""
        page1 = mock_page_response(page_id="page-c1")
        page2 = mock_page_response(page_id="page-c2")

        notion_ops_client._notion.request.return_value = {
            "object": "list",
            "results": [page1, page2],
            "has_more": False,
            "next_cursor": None,
        }

        count = notion_ops_client.data_sources.count("ds-count-001")

        assert count == 2

    def test_count_with_filter(self, notion_ops_client, mock_page_response):
        """Count with filter passes filter through to query."""
        page1 = mock_page_response(page_id="page-cf1")

        notion_ops_client._notion.request.return_value = {
            "object": "list",
            "results": [page1],
            "has_more": False,
            "next_cursor": None,
        }

        test_filter = {"property": "Status", "select": {"equals": "Active"}}
        count = notion_ops_client.data_sources.count("ds-count-002", filter=test_filter)

        assert count == 1
        # Verify filter was passed in the request body
        call_kwargs = notion_ops_client._notion.request.call_args.kwargs
        assert call_kwargs["body"]["filter"] == test_filter


class TestDataSourceDeleteProperty:
    """Tests for DataSourceOperations.delete_property."""

    def test_delete_property(
        self, notion_ops_client, mock_database_response
    ):
        """delete_property passes property_name=None to databases.update."""
        updated_response = mock_database_response(
            database_id="ds-delprop-001"
        )
        notion_ops_client._notion.databases.update.return_value = (
            updated_response
        )

        ds = notion_ops_client.data_sources.delete_property(
            "ds-delprop-001", "ObsoleteColumn"
        )

        assert isinstance(ds, DataSource)
        notion_ops_client._notion.databases.update.assert_called_once_with(
            database_id="dsdelprop001",
            properties={"ObsoleteColumn": None},
        )


class TestDataSourceExtractId:
    """Tests for DataSourceOperations._extract_id."""

    def test_extract_id_plain(self, notion_ops_client):
        """Plain ID with dashes gets dashes removed."""
        ops = notion_ops_client.data_sources
        assert ops._extract_id("2d8d-371a-79f4") == "2d8d371a79f4"

    def test_extract_id_from_url(self, notion_ops_client):
        """Notion URL extracts ID from path."""
        ops = notion_ops_client.data_sources
        url = "https://www.notion.so/workspace/DB-Title-abcdef1234567890abcdef1234567890"
        result = ops._extract_id(url)
        assert result == "abcdef1234567890abcdef1234567890"
