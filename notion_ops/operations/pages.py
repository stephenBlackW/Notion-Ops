"""Page CRUD operations for Notion Operations library."""

from typing import TYPE_CHECKING, Any, Literal

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.block import Block
from notion_ops.models.page import Page, PageCreate, PageUpdate
from notion_ops.models.properties import PropertyValue

if TYPE_CHECKING:
    from notion_ops.client import NotionOps


class PageOperations:
    """CRUD operations for Notion pages."""

    def __init__(self, client: "NotionOps"):
        self._client = client

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
        except Exception as e:
            raise NotionOpsError(f"Failed to create page: {e}") from e

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
        page_id = self._extract_id(page_id)

        try:
            response = self._client._notion.pages.retrieve(page_id=page_id)
            return Page.from_api_response(response)
        except Exception as e:
            if "object_not_found" in str(e).lower():
                raise NotFoundError("Page", page_id) from e
            raise NotionOpsError(f"Failed to retrieve page: {e}") from e

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
        page_id = self._extract_id(page_id)

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
        except Exception as e:
            if "object_not_found" in str(e).lower():
                raise NotFoundError("Page", page_id) from e
            raise NotionOpsError(f"Failed to update page: {e}") from e

    def archive(self, page_id: str) -> Page:
        """
        Archive (soft delete) a page.

        Args:
            page_id: The page ID

        Returns:
            Updated Page object
        """
        return self.update(page_id, archived=True)

    def restore(self, page_id: str) -> Page:
        """
        Restore an archived page.

        Args:
            page_id: The page ID

        Returns:
            Updated Page object
        """
        return self.update(page_id, archived=False)

    def delete(self, page_id: str) -> None:
        """
        Delete a page (move to trash).

        Note: Pages in trash are permanently deleted after 30 days.

        Args:
            page_id: The page ID
        """
        # In Notion API, archiving is the delete operation
        self.archive(page_id)

    def get_property(self, page_id: str, property_id: str) -> Any:
        """
        Retrieve a specific property value from a page.

        Args:
            page_id: The page ID
            property_id: The property ID or name

        Returns:
            Property value
        """
        page_id = self._extract_id(page_id)

        try:
            response = self._client._notion.pages.properties.retrieve(
                page_id=page_id,
                property_id=property_id,
            )
            return response
        except Exception as e:
            raise NotionOpsError(f"Failed to retrieve property: {e}") from e

    def _extract_id(self, id_or_url: str) -> str:
        """Extract page ID from URL or return as-is."""
        if id_or_url.startswith("http"):
            # Extract ID from Notion URL
            # URL format: https://www.notion.so/workspace/Page-Title-<id>
            # or https://www.notion.so/<id>
            parts = id_or_url.rstrip("/").split("-")
            if parts:
                potential_id = parts[-1]
                # Notion IDs are 32 hex characters (without dashes)
                if len(potential_id) == 32:
                    return potential_id
            # Try extracting from path
            path = id_or_url.split("notion.so/")[-1].split("?")[0]
            if "/" in path:
                path = path.split("/")[-1]
            # Remove any title prefix
            if "-" in path:
                path = path.split("-")[-1]
            return path

        # Remove dashes if present (normalize ID format)
        return id_or_url.replace("-", "")
