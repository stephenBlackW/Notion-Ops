"""Database and DataSource models for Notion Operations library."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from notion_ops.models.properties import PropertyDefinition, PropertyType


class DataSourceSchema(BaseModel):
    """Schema definition for a data source."""

    properties: dict[str, PropertyDefinition]

    @classmethod
    def from_api_response(cls, properties: dict[str, Any]) -> "DataSourceSchema":
        """Create schema from API response."""
        parsed_props = {}
        for name, prop_data in properties.items():
            prop_type = prop_data.get("type", "rich_text")
            try:
                parsed_props[name] = PropertyDefinition(
                    name=name,
                    type=PropertyType(prop_type),
                    options=prop_data.get(prop_type),
                )
            except ValueError:
                # Unknown property type, skip
                pass
        return cls(properties=parsed_props)


class Database(BaseModel):
    """Represents a Notion database (container for data sources)."""

    id: str
    title: str
    description: str | None = None
    data_sources: list[str] = Field(default_factory=list)
    created_time: datetime
    last_edited_time: datetime
    parent: dict[str, Any] = Field(default_factory=dict)
    icon: dict[str, Any] | None = None
    cover: dict[str, Any] | None = None
    archived: bool = False
    is_inline: bool = False
    url: str = ""
    # Schema properties when the API response includes them (raw
    # databases.retrieve does; the new-API database object may not). Defaults
    # empty rather than forcing callers to client._notion.databases.retrieve
    # for schema access (ISS-003).
    properties: dict[str, PropertyDefinition] = Field(default_factory=dict)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "Database":
        """Create a Database from Notion API response."""
        # Extract title
        title_arr = data.get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_arr)

        # Extract description
        desc_arr = data.get("description", [])
        description = "".join(d.get("plain_text", "") for d in desc_arr) if desc_arr else None

        # Extract data source IDs (if present in new API)
        data_sources = data.get("data_sources", [])
        if not data_sources and data.get("id"):
            # In older API, database ID is also the data source ID
            data_sources = [data["id"]]

        # Parse schema properties when present (reuse the data-source parser).
        properties = DataSourceSchema.from_api_response(
            data.get("properties", {})
        ).properties

        return cls(
            id=data["id"],
            title=title,
            description=description,
            data_sources=data_sources,
            created_time=data.get("created_time", datetime.now()),
            last_edited_time=data.get("last_edited_time", datetime.now()),
            parent=data.get("parent", {}),
            icon=data.get("icon"),
            cover=data.get("cover"),
            archived=data.get("archived", False),
            is_inline=data.get("is_inline", False),
            url=data.get("url", ""),
            properties=properties,
        )

    def get_property_names(self) -> list[str]:
        """Get all schema property names (empty if the response had no schema)."""
        return list(self.properties.keys())

    def get_property_type(self, name: str) -> PropertyType | None:
        """Get the type of a property by name, or None if absent."""
        prop = self.properties.get(name)
        return prop.type if prop else None


class DataSource(BaseModel):
    """Represents a Notion data source (collection of pages with schema)."""

    id: str
    database_id: str | None = None
    title: str
    description: str | None = None
    schema_: DataSourceSchema = Field(alias="schema")
    created_time: datetime
    last_edited_time: datetime
    parent: dict[str, Any] = Field(default_factory=dict)
    icon: dict[str, Any] | None = None
    cover: dict[str, Any] | None = None
    archived: bool = False
    url: str = ""

    model_config = {"populate_by_name": True}

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "DataSource":
        """Create a DataSource from Notion API response."""
        # Extract title
        title_arr = data.get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_arr)

        # Extract description
        desc_arr = data.get("description", [])
        description = "".join(d.get("plain_text", "") for d in desc_arr) if desc_arr else None

        # Parse schema from properties
        schema = DataSourceSchema.from_api_response(data.get("properties", {}))

        return cls(
            id=data["id"],
            database_id=data.get("database_id"),
            title=title,
            description=description,
            schema=schema,
            created_time=data.get("created_time", datetime.now()),
            last_edited_time=data.get("last_edited_time", datetime.now()),
            parent=data.get("parent", {}),
            icon=data.get("icon"),
            cover=data.get("cover"),
            archived=data.get("archived", False),
            url=data.get("url", ""),
        )

    def get_property_names(self) -> list[str]:
        """Get all property names in the schema."""
        return list(self.schema_.properties.keys())

    def get_property_type(self, name: str) -> PropertyType | None:
        """Get the type of a property by name."""
        prop = self.schema_.properties.get(name)
        return prop.type if prop else None


class QueryResult(BaseModel):
    """Result of a data source query."""

    pages: list[Any]  # list[Page]
    has_more: bool = False
    next_cursor: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any], page_cls: type) -> "QueryResult":
        """Create QueryResult from API response."""
        from notion_ops.models.page import Page

        pages = [Page.from_api_response(p) for p in data.get("results", [])]
        return cls(
            pages=pages,
            has_more=data.get("has_more", False),
            next_cursor=data.get("next_cursor"),
        )
