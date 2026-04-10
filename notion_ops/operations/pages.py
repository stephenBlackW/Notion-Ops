"""Page CRUD operations for Notion Operations library."""

from typing import TYPE_CHECKING, Any, Literal

from notion_client import APIResponseError

from notion_ops.exceptions import NotionOpsError, map_api_error
from notion_ops.models.block import Block
from notion_ops.models.page import Page, PageCreate, PageUpdate
from notion_ops.models.properties import PropertyValue
from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.retry import retry_on_transient, retry_on_transient_async

if TYPE_CHECKING:
    from notion_ops.client import AsyncNotionOps, NotionOps


class PageOperations:
    """CRUD operations for Notion pages."""

    def __init__(self, client: "NotionOps"):
        self._client = client

    @retry_on_transient
    def create(
        self,
        parent_id: str,
        properties: dict[str, PropertyValue],
        *,
        parent_type: Literal["database", "data_source", "page"] = "data_source",
        children: list[Block] | None = None,
        icon: str | dict[str, Any] | None = None,
        cover: str | None = None,
    ) -> Page:
        """
        Create a new page.

        Args:
            parent_id: ID of the parent database, data source, or page
            properties: Dictionary of property names to values
            parent_type: Type of parent ("database", "data_source", or "page")
            children: Initial content blocks
            icon: Emoji string or icon dict
            cover: URL for cover image

        Returns:
            Created Page object

        Example:
            page = client.pages.create(
                parent_id="abc123",
                properties={
                    "Name": TitleProperty(value="New Page"),
                    "Status": SelectProperty(value="In Progress"),
                },
                children=[
                    Blocks.heading_1("Introduction"),
                    Blocks.paragraph("This is my new page.")
                ]
            )
        """
        page_create = PageCreate(
            parent_id=parent_id,
            parent_type=parent_type,
            properties=properties,
            children=children,
            icon=icon,
            cover=cover,
        )

        try:
            response = self._client._notion.pages.create(**page_create.to_api_format())
            return Page.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=parent_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to create page: {e}") from e

    @retry_on_transient
    def get(self, page_id: str) -> Page:
        """
        Retrieve a page by ID.

        Args:
            page_id: The page ID or URL

        Returns:
            Page object

        Example:
            page = client.pages.get("abc123")
            print(page.get_title())
        """
        # Extract ID from URL if needed
        page_id = extract_notion_id(page_id)

        try:
            response = self._client._notion.pages.retrieve(page_id=page_id)
            return Page.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=page_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to retrieve page: {e}") from e

    @retry_on_transient
    def update(
        self,
        page_id: str,
        properties: dict[str, PropertyValue] | None = None,
        *,
        archived: bool | None = None,
        icon: str | dict[str, Any] | None = None,
        cover: str | None = None,
    ) -> Page:
        """
        Update page properties.

        Args:
            page_id: The page ID
            properties: Properties to update
            archived: Set archive status
            icon: Update icon
            cover: Update cover image

        Returns:
            Updated Page object

        Example:
            page = client.pages.update(
                "abc123",
                properties={
                    "Status": SelectProperty(value="Done"),
                    "Completed": CheckboxProperty(value=True)
                }
            )
        """
        page_id = extract_notion_id(page_id)

        page_update = PageUpdate(
            properties=properties,
            archived=archived,
            icon=icon,
            cover=cover,
        )

        update_data = page_update.to_api_format()
        if not update_data:
            # Nothing to update
            return self.get(page_id)

        try:
            response = self._client._notion.pages.update(
                page_id=page_id,
                **update_data,
            )
            return Page.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=page_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to update page: {e}") from e

    @retry_on_transient
    def archive(self, page_id: str) -> Page:
        """
        Archive (soft delete) a page.

        Args:
            page_id: The page ID

        Returns:
            Updated Page object
        """
        return self.update(page_id, archived=True)

    @retry_on_transient
    def restore(self, page_id: str) -> Page:
        """
        Restore an archived page.

        Args:
            page_id: The page ID

        Returns:
            Updated Page object
        """
        return self.update(page_id, archived=False)

    @retry_on_transient
    def delete(self, page_id: str) -> None:
        """
        Delete a page (move to trash).

        Note: Pages in trash are permanently deleted after 30 days.

        Args:
            page_id: The page ID
        """
        # In Notion API, archiving is the delete operation
        self.archive(page_id)

    @retry_on_transient
    def move(
        self,
        page_id: str,
        *,
        parent_id: str,
        parent_type: Literal["data_source", "page"] = "data_source",
    ) -> Page:
        """
        Move a page to a new parent (page or data source).

        Uses the SDK's native pages.move() endpoint
        (POST /v1/pages/{page_id}/move).

        Args:
            page_id: The page to move.
            parent_id: The new parent ID.
            parent_type: Either "data_source" (for databases) or "page".

        Returns:
            The moved Page object.
        """
        page_id = extract_notion_id(page_id)
        parent_id_clean = extract_notion_id(parent_id)

        parent_key = "data_source_id" if parent_type == "data_source" else "page_id"

        try:
            response = self._client._notion.pages.move(
                page_id=page_id,
                parent={
                    "type": parent_key,
                    parent_key: parent_id_clean,
                },
            )
            return Page.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=page_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to move page: {e}") from e

    @retry_on_transient
    def get_property(self, page_id: str, property_id: str) -> Any:
        """
        Retrieve a specific property value from a page.

        Args:
            page_id: The page ID
            property_id: The property ID or name

        Returns:
            Property value
        """
        page_id = extract_notion_id(page_id)

        try:
            response = self._client._notion.pages.properties.retrieve(
                page_id=page_id,
                property_id=property_id,
            )
            return response
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=page_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to retrieve property: {e}") from e



class AsyncPageOperations:
    """Async CRUD operations for Notion pages."""

    def __init__(self, client: "AsyncNotionOps") -> None:
        self._client = client

    @retry_on_transient_async
    async def create(
        self,
        parent_id: str,
        properties: dict[str, PropertyValue],
        *,
        parent_type: Literal["database", "data_source", "page"] = "data_source",
        children: list[Block] | None = None,
        icon: str | dict[str, Any] | None = None,
        cover: str | None = None,
    ) -> Page:
        """
        Create a new page (async).

        Args:
            parent_id: ID of the parent database, data source, or page
            properties: Dictionary of property names to values
            parent_type: Type of parent ("database", "data_source", or "page")
            children: Initial content blocks
            icon: Emoji string or icon dict
            cover: URL for cover image

        Returns:
            Created Page object
        """
        page_create = PageCreate(
            parent_id=parent_id,
            parent_type=parent_type,
            properties=properties,
            children=children,
            icon=icon,
            cover=cover,
        )

        try:
            response = await self._client._notion.pages.create(**page_create.to_api_format())
            return Page.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=parent_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to create page: {e}") from e

    @retry_on_transient_async
    async def get(self, page_id: str) -> Page:
        """
        Retrieve a page by ID (async).

        Args:
            page_id: The page ID or URL

        Returns:
            Page object
        """
        page_id = extract_notion_id(page_id)

        try:
            response = await self._client._notion.pages.retrieve(page_id=page_id)
            return Page.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=page_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to retrieve page: {e}") from e

    @retry_on_transient_async
    async def update(
        self,
        page_id: str,
        properties: dict[str, PropertyValue] | None = None,
        *,
        archived: bool | None = None,
        icon: str | dict[str, Any] | None = None,
        cover: str | None = None,
    ) -> Page:
        """
        Update page properties (async).

        Args:
            page_id: The page ID
            properties: Properties to update
            archived: Set archive status
            icon: Update icon
            cover: Update cover image

        Returns:
            Updated Page object
        """
        page_id = extract_notion_id(page_id)

        page_update = PageUpdate(
            properties=properties,
            archived=archived,
            icon=icon,
            cover=cover,
        )

        update_data = page_update.to_api_format()
        if not update_data:
            return await self.get(page_id)

        try:
            response = await self._client._notion.pages.update(
                page_id=page_id,
                **update_data,
            )
            return Page.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=page_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to update page: {e}") from e

    @retry_on_transient_async
    async def archive(self, page_id: str) -> Page:
        """
        Archive (soft delete) a page (async).

        Args:
            page_id: The page ID

        Returns:
            Updated Page object
        """
        return await self.update(page_id, archived=True)

    @retry_on_transient_async
    async def restore(self, page_id: str) -> Page:
        """
        Restore an archived page (async).

        Args:
            page_id: The page ID

        Returns:
            Updated Page object
        """
        return await self.update(page_id, archived=False)

    @retry_on_transient_async
    async def delete(self, page_id: str) -> None:
        """
        Delete a page (move to trash) (async).

        Note: Pages in trash are permanently deleted after 30 days.

        Args:
            page_id: The page ID
        """
        await self.archive(page_id)

    @retry_on_transient_async
    async def move(
        self,
        page_id: str,
        *,
        parent_id: str,
        parent_type: Literal["data_source", "page"] = "data_source",
    ) -> Page:
        """
        Move a page to a new parent (page or data source) (async).

        Args:
            page_id: The page to move.
            parent_id: The new parent ID.
            parent_type: Either "data_source" (for databases) or "page".

        Returns:
            The moved Page object.
        """
        page_id = extract_notion_id(page_id)
        parent_id_clean = extract_notion_id(parent_id)

        parent_key = "data_source_id" if parent_type == "data_source" else "page_id"

        try:
            response = await self._client._notion.pages.move(
                page_id=page_id,
                parent={
                    "type": parent_key,
                    parent_key: parent_id_clean,
                },
            )
            return Page.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=page_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to move page: {e}") from e

    @retry_on_transient_async
    async def get_property(self, page_id: str, property_id: str) -> Any:
        """
        Retrieve a specific property value from a page (async).

        Args:
            page_id: The page ID
            property_id: The property ID or name

        Returns:
            Property value
        """
        page_id = extract_notion_id(page_id)

        try:
            response = await self._client._notion.pages.properties.retrieve(
                page_id=page_id,
                property_id=property_id,
            )
            return response
        except APIResponseError as e:
            raise map_api_error(e, resource_type="Page", resource_id=page_id) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to retrieve property: {e}") from e
