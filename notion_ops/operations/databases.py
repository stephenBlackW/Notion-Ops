"""Database CRUD operations for Notion Operations library."""

from typing import TYPE_CHECKING, Any, Literal

from notion_client import APIResponseError

from notion_ops.exceptions import NotionOpsError, map_api_error
from notion_ops.models.database import Database, DataSource
from notion_ops.models.properties import PropertyDefinition
from notion_ops.utils.retry import retry_on_transient

if TYPE_CHECKING:
    from notion_ops.client import NotionOps


class DatabaseOperations:
    """CRUD operations for Notion databases (containers)."""

    def __init__(self, client: "NotionOps"):
        self._client = client

    @retry_on_transient
    def create(
        self,
        parent_id: str,
        title: str,
        *,
        parent_type: Literal["page", "workspace"] = "page",
        description: str | None = None,
        schema: dict[str, PropertyDefinition] | None = None,
        icon: str | dict[str, Any] | None = None,
        cover: str | None = None,
        is_inline: bool = False,
    ) -> Database:
        """
        Create a new database.

        Args:
            parent_id: Parent page ID
            title: Database title
            parent_type: Type of parent ("page" or "workspace")
            description: Optional description
            schema: Property definitions for the database
            icon: Emoji string or icon dict
            cover: URL for cover image
            is_inline: Whether this is an inline database

        Returns:
            Created Database object

        Example:
            db = client.databases.create(
                parent_id="page_abc123",
                title="Project Tasks",
                schema={
                    "Name": PropertyDefinition(name="Name", type=PropertyType.TITLE),
                    "Status": PropertyDefinition(
                        name="Status",
                        type=PropertyType.STATUS,
                        options={"options": [{"name": "Done", "color": "green"}]}
                    ),
                }
            )
        """
        # Build parent
        if parent_type == "workspace":
            parent = {"type": "workspace", "workspace": True}
        else:
            parent = {"type": "page_id", "page_id": parent_id}

        # Build properties schema
        properties: dict[str, Any] = {}
        if schema:
            for name, prop_def in schema.items():
                properties[name] = prop_def.to_api_format()
        else:
            # At minimum, need a title property
            properties["Name"] = {"title": {}}

        # Build request
        create_data: dict[str, Any] = {
            "parent": parent,
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
            "is_inline": is_inline,
        }

        if description:
            create_data["description"] = [{"type": "text", "text": {"content": description}}]

        if icon:
            if isinstance(icon, str):
                create_data["icon"] = {"type": "emoji", "emoji": icon}
            else:
                create_data["icon"] = icon

        if cover:
            create_data["cover"] = {"type": "external", "external": {"url": cover}}

        try:
            response = self._client._notion.databases.create(**create_data)
            return Database.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="Database", resource_id=parent_id
            ) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to create database: {e}") from e

    @retry_on_transient
    def get(self, database_id: str) -> Database:
        """
        Retrieve a database by ID.

        Args:
            database_id: The database ID

        Returns:
            Database object
        """
        database_id = self._extract_id(database_id)

        try:
            response = self._client._notion.databases.retrieve(database_id=database_id)
            return Database.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="Database", resource_id=database_id
            ) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to retrieve database: {e}") from e

    @retry_on_transient
    def update(
        self,
        database_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        properties: dict[str, PropertyDefinition] | None = None,
        icon: str | dict[str, Any] | None = None,
        cover: str | None = None,
    ) -> Database:
        """
        Update database metadata and schema.

        Args:
            database_id: The database ID
            title: New title
            description: New description
            properties: Properties to add or modify
            icon: Update icon
            cover: Update cover image

        Returns:
            Updated Database object
        """
        database_id = self._extract_id(database_id)

        update_data: dict[str, Any] = {}

        if title is not None:
            update_data["title"] = [{"type": "text", "text": {"content": title}}]

        if description is not None:
            update_data["description"] = [{"type": "text", "text": {"content": description}}]

        if properties:
            update_data["properties"] = {
                name: prop_def.to_api_format() for name, prop_def in properties.items()
            }

        if icon is not None:
            if isinstance(icon, str):
                update_data["icon"] = {"type": "emoji", "emoji": icon}
            else:
                update_data["icon"] = icon

        if cover is not None:
            update_data["cover"] = {"type": "external", "external": {"url": cover}}

        if not update_data:
            return self.get(database_id)

        try:
            response = self._client._notion.databases.update(
                database_id=database_id,
                **update_data,
            )
            return Database.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="Database", resource_id=database_id
            ) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to update database: {e}") from e

    @retry_on_transient
    def list_data_sources(self, database_id: str) -> list[DataSource]:
        """
        List all data sources in a database.

        Note: In the current API version, a database typically has one data source.

        Args:
            database_id: The database ID

        Returns:
            List of DataSource objects
        """
        db = self.get(database_id)

        # In the 2025-09-03 API, databases can have multiple data sources
        # For now, we return the database itself as a data source
        return [
            DataSource(
                id=db.id,
                database_id=db.id,
                title=db.title,
                description=db.description,
                schema={"properties": {}},
                created_time=db.created_time,
                last_edited_time=db.last_edited_time,
                parent=db.parent,
                icon=db.icon,
                cover=db.cover,
                archived=db.archived,
                url=db.url,
            )
        ]

    @retry_on_transient
    def archive(self, database_id: str) -> Database:
        """
        Archive a database.

        Args:
            database_id: The database ID

        Returns:
            Updated Database object
        """
        database_id = self._extract_id(database_id)

        try:
            response = self._client._notion.databases.update(
                database_id=database_id,
                archived=True,
            )
            return Database.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(
                e, resource_type="Database", resource_id=database_id
            ) from e
        except Exception as e:
            raise NotionOpsError(f"Failed to archive database: {e}") from e

    def _extract_id(self, id_or_url: str) -> str:
        """Extract database ID from URL or return as-is."""
        if id_or_url.startswith("http"):
            # Extract from Notion URL
            path = id_or_url.split("notion.so/")[-1].split("?")[0]
            if "/" in path:
                path = path.split("/")[-1]
            if "-" in path:
                path = path.split("-")[-1]
            return path
        return id_or_url.replace("-", "")
