"""Main client for Notion Operations library."""

import os
from typing import Any

from notion_client import AsyncClient, Client

from notion_ops.exceptions import AuthenticationError
from notion_ops.models.page import Page
from notion_ops.operations.blocks import AsyncBlockOperations, BlockOperations
from notion_ops.operations.data_sources import AsyncDataSourceOperations, DataSourceOperations
from notion_ops.operations.databases import AsyncDatabaseOperations, DatabaseOperations
from notion_ops.operations.file_uploads import FileUploads
from notion_ops.operations.pages import AsyncPageOperations, PageOperations
from notion_ops.operations.users import AsyncUserOperations, UserOperations


class NotionOps:
    """
    High-level Notion operations client.

    Provides a clean, Pythonic interface for CRUD operations on Notion
    resources including pages, databases, data sources, and blocks.

    Example:
        # Initialize from environment variable
        client = NotionOps()

        # Or with explicit token
        client = NotionOps(auth="secret_xxx")

        # Create a page
        page = client.pages.create(
            parent_id="database_id",
            properties={
                "Name": TitleProperty(value="New Page"),
            }
        )

        # Query a database
        results = client.data_sources.query(
            "database_id",
            filter=Filter.select("Status").equals("Active")
        )
    """

    def __init__(
        self,
        auth: str | None = None,
        timeout: int = 60000,
        notion_version: str = "2025-09-03",
    ):
        """
        Initialize the Notion Operations client.

        Args:
            auth: Notion API key. If not provided, reads from
                  NOTION_API_KEY or NOTION_TOKEN environment variables.
            timeout: Request timeout in milliseconds (default: 60000)
            notion_version: Notion API version to use

        Raises:
            AuthenticationError: If no API key is provided or found
        """
        # Get auth token
        self._auth = auth or os.environ.get("NOTION_API_KEY") or os.environ.get("NOTION_TOKEN")

        if not self._auth:
            raise AuthenticationError(
                "No Notion API key provided. Set NOTION_API_KEY or NOTION_TOKEN "
                "environment variable, or pass auth parameter."
            )

        # Initialize the underlying notion-client
        self._notion = Client(
            auth=self._auth,
            timeout_ms=timeout,
            notion_version=notion_version,
        )

        # Track Notion-Version so file_uploads can build its own headers
        # for direct multipart POSTs against pre-signed upload URLs.
        self._notion_version = notion_version

        # Initialize operation handlers
        self.pages = PageOperations(self)
        self.databases = DatabaseOperations(self)
        self.data_sources = DataSourceOperations(self)
        self.blocks = BlockOperations(self)
        self.users = UserOperations(self)
        self.file_uploads = FileUploads(self)

    @property
    def api(self) -> Client:
        """Public accessor for the underlying notion-client SDK client."""
        return self._notion

    def search(
        self,
        query: str = "",
        *,
        filter_type: str | None = None,
        sort_direction: str = "descending",
        sort_timestamp: str = "last_edited_time",
        page_size: int = 100,
        start_cursor: str | None = None,
    ) -> list[Page]:
        """
        Search across all pages and databases.

        Args:
            query: Search query string
            filter_type: Filter by object type ("page" or "database")
            sort_direction: Sort direction ("ascending" or "descending")
            sort_timestamp: Sort by timestamp ("last_edited_time")
            page_size: Results per page (max 100)
            start_cursor: Pagination cursor

        Returns:
            List of Page objects from search results
        """
        params: dict[str, Any] = {
            "page_size": min(page_size, 100),
            "sort": {
                "direction": sort_direction,
                "timestamp": sort_timestamp,
            },
        }

        if query:
            params["query"] = query

        if filter_type:
            params["filter"] = {"property": "object", "value": filter_type}

        if start_cursor:
            params["start_cursor"] = start_cursor

        response = self.api.search(**params)

        # Convert results to Page objects (filter out non-page results like databases)
        pages = []
        for result in response.get("results", []):
            if result.get("object") == "page":
                pages.append(Page.from_api_response(result))

        return pages


class AsyncNotionOps:
    """
    Async version of the Notion Operations client.

    Provides the same interface as NotionOps but with async/await support.

    Example:
        async with AsyncNotionOps() as client:
            page = await client.pages.get("page_id")
    """

    def __init__(
        self,
        auth: str | None = None,
        timeout: int = 60000,
        notion_version: str = "2025-09-03",
    ):
        """
        Initialize the async Notion Operations client.

        Args:
            auth: Notion API key. If not provided, reads from
                  NOTION_API_KEY or NOTION_TOKEN environment variables.
            timeout: Request timeout in milliseconds (default: 60000)
            notion_version: Notion API version to use

        Raises:
            AuthenticationError: If no API key is provided or found
        """
        self._auth = auth or os.environ.get("NOTION_API_KEY") or os.environ.get("NOTION_TOKEN")

        if not self._auth:
            raise AuthenticationError(
                "No Notion API key provided. Set NOTION_API_KEY or NOTION_TOKEN "
                "environment variable, or pass auth parameter."
            )

        self._notion = AsyncClient(
            auth=self._auth,
            timeout_ms=timeout,
            notion_version=notion_version,
        )

        # Track Notion-Version for file upload header construction.
        self._notion_version = notion_version

        # Initialize async operation handlers
        self.pages = AsyncPageOperations(self)
        self.databases = AsyncDatabaseOperations(self)
        self.data_sources = AsyncDataSourceOperations(self)
        self.blocks = AsyncBlockOperations(self)
        self.users = AsyncUserOperations(self)
        # File uploads are exposed even on the async client; the upload flow
        # itself is synchronous (direct multipart POST to a pre-signed URL).
        self.file_uploads = FileUploads(self)

    @property
    def api(self) -> AsyncClient:
        """Public accessor for the underlying notion-client SDK client."""
        return self._notion

    async def __aenter__(self) -> "AsyncNotionOps":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.api.aclose()

    async def search(
        self,
        query: str = "",
        *,
        filter_type: str | None = None,
        sort_direction: str = "descending",
        sort_timestamp: str = "last_edited_time",
        page_size: int = 100,
        start_cursor: str | None = None,
    ) -> list[Page]:
        """Search across all pages and databases (async version)."""
        params: dict[str, Any] = {
            "page_size": min(page_size, 100),
            "sort": {
                "direction": sort_direction,
                "timestamp": sort_timestamp,
            },
        }

        if query:
            params["query"] = query

        if filter_type:
            params["filter"] = {"property": "object", "value": filter_type}

        if start_cursor:
            params["start_cursor"] = start_cursor

        response = await self.api.search(**params)

        # Convert results to Page objects (filter out non-page results like databases)
        pages = []
        for result in response.get("results", []):
            if result.get("object") == "page":
                pages.append(Page.from_api_response(result))

        return pages
