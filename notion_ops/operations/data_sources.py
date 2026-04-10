"""DataSource CRUD operations for Notion Operations library."""

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from notion_client import APIResponseError

from notion_ops.exceptions import map_api_error
from notion_ops.models.database import DataSource, QueryResult
from notion_ops.models.page import Page
from notion_ops.models.properties import PropertyDefinition
from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.retry import retry_on_transient, retry_on_transient_async

if TYPE_CHECKING:
    from notion_ops.client import AsyncNotionOps, NotionOps


class DataSourceOperations:
    """CRUD operations for Notion data sources (collections of pages)."""

    def __init__(self, client: "NotionOps"):
        self._client = client

    @retry_on_transient
    def get(self, data_source_id: str) -> DataSource:
        """
        Retrieve a data source by ID.

        Note: In the current API, data sources and databases share the same ID.

        Args:
            data_source_id: The data source ID

        Returns:
            DataSource object
        """
        data_source_id = extract_notion_id(data_source_id)

        try:
            # Use database retrieve endpoint (data source ID == database ID in most cases)
            response = self._client.api.databases.retrieve(database_id=data_source_id)
            return DataSource.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="DataSource", resource_id=data_source_id
            ) from e

    @retry_on_transient
    def query(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, str]] | None = None,
        page_size: int = 100,
        start_cursor: str | None = None,
    ) -> QueryResult:
        """
        Query pages in a data source.

        Args:
            data_source_id: The data source ID
            filter: Filter conditions
            sorts: Sort specifications
            page_size: Results per page (max 100)
            start_cursor: Pagination cursor

        Returns:
            QueryResult with pages and pagination info

        Example:
            results = client.data_sources.query(
                "abc123",
                filter=Filter.and_(
                    Filter.select("Status").equals("Active"),
                    Filter.checkbox("Archived").equals(False)
                ),
                sorts=[Sort.descending("Created")]
            )
            for page in results.pages:
                print(page.get_title())
        """
        data_source_id = extract_notion_id(data_source_id)

        body: dict[str, Any] = {
            "page_size": min(page_size, 100),
        }

        if filter:
            body["filter"] = filter

        if sorts:
            body["sorts"] = sorts

        if start_cursor:
            body["start_cursor"] = start_cursor

        try:
            # Use raw request — the SDK's databases.query() doesn't exist.
            # As of Notion API v2025-09-03, the query endpoint moved from
            # /databases/{id}/query to /data_sources/{id}/query.
            response = self._client.api.request(
                path=f"data_sources/{data_source_id}/query",
                method="POST",
                body=body,
            )
            return QueryResult.from_api_response(response, Page)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="DataSource", resource_id=data_source_id
            ) from e

    @retry_on_transient
    def query_all(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, str]] | None = None,
    ) -> Iterator[Page]:
        """
        Query all pages in a data source (handles pagination automatically).

        Args:
            data_source_id: The data source ID
            filter: Filter conditions
            sorts: Sort specifications

        Yields:
            Page objects

        Example:
            for page in client.data_sources.query_all("abc123"):
                print(page.get_title())
        """
        start_cursor: str | None = None

        while True:
            result = self.query(
                data_source_id,
                filter=filter,
                sorts=sorts,
                page_size=100,
                start_cursor=start_cursor,
            )

            yield from result.pages

            if not result.has_more or not result.next_cursor:
                break

            start_cursor = result.next_cursor

    @retry_on_transient
    def update_schema(
        self,
        data_source_id: str,
        properties: dict[str, PropertyDefinition],
    ) -> DataSource:
        """
        Update data source schema (add/modify properties).

        Args:
            data_source_id: The data source ID
            properties: Property definitions to add or update

        Returns:
            Updated DataSource object

        Example:
            client.data_sources.update_schema(
                "abc123",
                properties={
                    "Priority": PropertyDefinition(
                        name="Priority",
                        type=PropertyType.SELECT,
                        options={"options": [
                            {"name": "High", "color": "red"},
                            {"name": "Medium", "color": "yellow"},
                            {"name": "Low", "color": "green"}
                        ]}
                    )
                }
            )
        """
        data_source_id = extract_notion_id(data_source_id)

        update_data = {
            "properties": {name: prop_def.to_api_format() for name, prop_def in properties.items()}
        }

        try:
            response = self._client.api.databases.update(
                database_id=data_source_id,
                **update_data,
            )
            return DataSource.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="DataSource", resource_id=data_source_id
            ) from e

    @retry_on_transient
    def delete_property(self, data_source_id: str, property_name: str) -> DataSource:
        """
        Delete a property from the data source schema.

        Args:
            data_source_id: The data source ID
            property_name: Name of the property to delete

        Returns:
            Updated DataSource object
        """
        data_source_id = extract_notion_id(data_source_id)

        try:
            response = self._client.api.databases.update(
                database_id=data_source_id,
                properties={property_name: None},
            )
            return DataSource.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="DataSource", resource_id=data_source_id
            ) from e

    @retry_on_transient
    def count(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,
    ) -> int:
        """
        Count pages in a data source matching a filter.

        Note: This iterates through all pages, which may be slow for large data sources.

        Args:
            data_source_id: The data source ID
            filter: Optional filter conditions

        Returns:
            Number of matching pages
        """
        count = 0
        for _ in self.query_all(data_source_id, filter=filter):
            count += 1
        return count


class AsyncDataSourceOperations:
    """Async CRUD operations for Notion data sources (collections of pages)."""

    def __init__(self, client: "AsyncNotionOps") -> None:
        self._client = client

    @retry_on_transient_async
    async def get(self, data_source_id: str) -> DataSource:
        """
        Retrieve a data source by ID (async).

        Note: In the current API, data sources and databases share the same ID.

        Args:
            data_source_id: The data source ID

        Returns:
            DataSource object
        """
        data_source_id = extract_notion_id(data_source_id)

        try:
            response = await self._client.api.databases.retrieve(
                database_id=data_source_id
            )
            return DataSource.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="DataSource", resource_id=data_source_id
            ) from e

    @retry_on_transient_async
    async def query(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, str]] | None = None,
        page_size: int = 100,
        start_cursor: str | None = None,
    ) -> QueryResult:
        """
        Query pages in a data source (async).

        Args:
            data_source_id: The data source ID
            filter: Filter conditions
            sorts: Sort specifications
            page_size: Results per page (max 100)
            start_cursor: Pagination cursor

        Returns:
            QueryResult with pages and pagination info
        """
        data_source_id = extract_notion_id(data_source_id)

        body: dict[str, Any] = {
            "page_size": min(page_size, 100),
        }

        if filter:
            body["filter"] = filter

        if sorts:
            body["sorts"] = sorts

        if start_cursor:
            body["start_cursor"] = start_cursor

        try:
            response = await self._client.api.request(
                path=f"data_sources/{data_source_id}/query",
                method="POST",
                body=body,
            )
            return QueryResult.from_api_response(response, Page)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="DataSource", resource_id=data_source_id
            ) from e

    async def query_all(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[Page]:
        """
        Query all pages in a data source (async, handles pagination automatically).

        Args:
            data_source_id: The data source ID
            filter: Filter conditions
            sorts: Sort specifications

        Yields:
            Page objects
        """
        start_cursor: str | None = None

        while True:
            result = await self.query(
                data_source_id,
                filter=filter,
                sorts=sorts,
                page_size=100,
                start_cursor=start_cursor,
            )

            for page in result.pages:
                yield page

            if not result.has_more or not result.next_cursor:
                break

            start_cursor = result.next_cursor

    @retry_on_transient_async
    async def update_schema(
        self,
        data_source_id: str,
        properties: dict[str, PropertyDefinition],
    ) -> DataSource:
        """
        Update data source schema (add/modify properties) (async).

        Args:
            data_source_id: The data source ID
            properties: Property definitions to add or update

        Returns:
            Updated DataSource object
        """
        data_source_id = extract_notion_id(data_source_id)

        update_data = {
            "properties": {
                name: prop_def.to_api_format() for name, prop_def in properties.items()
            }
        }

        try:
            response = await self._client.api.databases.update(
                database_id=data_source_id,
                **update_data,
            )
            return DataSource.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="DataSource", resource_id=data_source_id
            ) from e

    @retry_on_transient_async
    async def delete_property(self, data_source_id: str, property_name: str) -> DataSource:
        """
        Delete a property from the data source schema (async).

        Args:
            data_source_id: The data source ID
            property_name: Name of the property to delete

        Returns:
            Updated DataSource object
        """
        data_source_id = extract_notion_id(data_source_id)

        try:
            response = await self._client.api.databases.update(
                database_id=data_source_id,
                properties={property_name: None},
            )
            return DataSource.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="DataSource", resource_id=data_source_id
            ) from e

    async def count(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,
    ) -> int:
        """
        Count pages in a data source matching a filter (async).

        Note: This iterates through all pages, which may be slow for large data sources.

        Args:
            data_source_id: The data source ID
            filter: Optional filter conditions

        Returns:
            Number of matching pages
        """
        count = 0
        async for _ in self.query_all(data_source_id, filter=filter):
            count += 1
        return count
