"""Page models for Notion Operations library."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from notion_ops.models.properties import PropertyValue, parse_property_value

if TYPE_CHECKING:
    pass


class Page(BaseModel):
    """Represents a Notion page."""

    id: str
    created_time: datetime
    last_edited_time: datetime
    created_by: str
    last_edited_by: str
    parent: dict[str, str]
    archived: bool = False
    in_trash: bool = False
    properties: dict[str, Any]
    url: str
    icon: dict[str, Any] | None = None
    cover: dict[str, Any] | None = None

    def get_property(self, name: str) -> Any:
        """
        Get a property value by name.

        Args:
            name: Property name

        Returns:
            Parsed property value
        """
        prop_data = self.properties.get(name)
        if prop_data is None:
            return None
        return parse_property_value(prop_data)

    def get_title(self) -> str:
        """
        Get the page title.

        Returns:
            Page title string
        """
        # Find the title property
        for prop_data in self.properties.values():
            if prop_data.get("type") == "title":
                return parse_property_value(prop_data) or ""
        return ""

    def get_parent_id(self) -> str | None:
        """Get the parent ID (database, data source, or page)."""
        return (
            self.parent.get("database_id")
            or self.parent.get("data_source_id")
            or self.parent.get("page_id")
            or self.parent.get("workspace")
        )

    def get_parent_type(self) -> str | None:
        """Get the parent type."""
        if "database_id" in self.parent:
            return "database"
        elif "data_source_id" in self.parent:
            return "data_source"
        elif "page_id" in self.parent:
            return "page"
        elif "workspace" in self.parent:
            return "workspace"
        return None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "Page":
        """Create a Page from Notion API response."""
        return cls(
            id=data["id"],
            created_time=data["created_time"],
            last_edited_time=data["last_edited_time"],
            created_by=data.get("created_by", {}).get("id", ""),
            last_edited_by=data.get("last_edited_by", {}).get("id", ""),
            parent=data.get("parent", {}),
            archived=data.get("archived", False),
            in_trash=data.get("in_trash", False),
            properties=data.get("properties", {}),
            url=data.get("url", ""),
            icon=data.get("icon"),
            cover=data.get("cover"),
        )


class PageCreate(BaseModel):
    """Schema for creating a page."""

    parent_id: str
    parent_type: str = "data_source"  # "database", "data_source", or "page"
    properties: dict[str, PropertyValue]
    children: list[Any] | None = None  # list[Block]
    icon: str | dict[str, Any] | None = None
    cover: str | None = None

    def to_api_format(self) -> dict[str, Any]:
        """Convert to Notion API request format."""
        # Build parent
        parent_key = f"{self.parent_type}_id"
        if self.parent_type == "workspace":
            parent = {"type": "workspace", "workspace": True}
        else:
            parent = {parent_key: self.parent_id}

        # Build properties
        properties = {}
        for name, prop_value in self.properties.items():
            properties[name] = prop_value.to_api_format()

        result: dict[str, Any] = {
            "parent": parent,
            "properties": properties,
        }

        # Add children if provided
        if self.children:
            result["children"] = [
                child.to_api_format() if hasattr(child, "to_api_format") else child
                for child in self.children
            ]

        # Add icon
        if self.icon:
            if isinstance(self.icon, str):
                result["icon"] = {"type": "emoji", "emoji": self.icon}
            else:
                result["icon"] = self.icon

        # Add cover
        if self.cover:
            result["cover"] = {"type": "external", "external": {"url": self.cover}}

        return result


class PageUpdate(BaseModel):
    """Schema for updating a page."""

    properties: dict[str, PropertyValue] | None = None
    archived: bool | None = None
    icon: str | dict[str, Any] | None = None
    cover: str | None = None

    def to_api_format(self) -> dict[str, Any]:
        """Convert to Notion API request format."""
        result: dict[str, Any] = {}

        if self.properties:
            result["properties"] = {
                name: prop_value.to_api_format()
                for name, prop_value in self.properties.items()
            }

        if self.archived is not None:
            result["archived"] = self.archived

        if self.icon is not None:
            if isinstance(self.icon, str):
                result["icon"] = {"type": "emoji", "emoji": self.icon}
            else:
                result["icon"] = self.icon

        if self.cover is not None:
            result["cover"] = {"type": "external", "external": {"url": self.cover}}

        return result
