"""User operations for Notion Operations library."""

from typing import TYPE_CHECKING, Any

from notion_client import APIResponseError
from pydantic import BaseModel

from notion_ops.exceptions import map_api_error
from notion_ops.utils.retry import retry_on_transient, retry_on_transient_async

if TYPE_CHECKING:
    from notion_ops.client import AsyncNotionOps, NotionOps


class User(BaseModel):
    """Represents a Notion user."""

    id: str
    object: str = "user"
    type: str  # "person" or "bot"
    name: str | None = None
    avatar_url: str | None = None
    email: str | None = None  # Only for person type with email capability

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "User":
        """Create a User from Notion API response."""
        person_data = data.get("person", {})
        return cls(
            id=data["id"],
            object=data.get("object", "user"),
            type=data.get("type", "person"),
            name=data.get("name"),
            avatar_url=data.get("avatar_url"),
            email=person_data.get("email"),
        )


class UserOperations:
    """Operations for Notion users."""

    def __init__(self, client: "NotionOps"):
        self._client = client

    @retry_on_transient
    def get(self, user_id: str) -> User:
        """
        Retrieve a user by ID.

        Args:
            user_id: The user ID

        Returns:
            User object
        """
        try:
            response = self._client.api.users.retrieve(user_id=user_id)
            return User.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="User", resource_id=user_id) from e

    @retry_on_transient
    def list(self, page_size: int = 100) -> list[User]:
        """
        List all users in the workspace.

        Args:
            page_size: Results per page

        Returns:
            List of User objects
        """
        users: list[User] = []
        start_cursor: str | None = None

        while True:
            try:
                params: dict[str, Any] = {"page_size": min(page_size, 100)}
                if start_cursor:
                    params["start_cursor"] = start_cursor

                response = self._client.api.users.list(**params)

                for user_data in response.get("results", []):
                    users.append(User.from_api_response(user_data))

                if not response.get("has_more"):
                    break

                start_cursor = response.get("next_cursor")

            except APIResponseError as e:
                raise map_api_error(e, resource_type="User") from e

        return users

    @retry_on_transient
    def me(self) -> User:
        """
        Get the bot user associated with the current integration.

        Returns:
            User object for the bot
        """
        try:
            response = self._client.api.users.me()
            return User.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="User") from e


class AsyncUserOperations:
    """Async operations for Notion users."""

    def __init__(self, client: "AsyncNotionOps") -> None:
        self._client = client

    @retry_on_transient_async
    async def get(self, user_id: str) -> User:
        """
        Retrieve a user by ID (async).

        Args:
            user_id: The user ID

        Returns:
            User object
        """
        try:
            response = await self._client.api.users.retrieve(user_id=user_id)
            return User.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="User", resource_id=user_id) from e

    @retry_on_transient_async
    async def list(self, page_size: int = 100) -> list[User]:
        """
        List all users in the workspace (async).

        Args:
            page_size: Results per page

        Returns:
            List of User objects
        """
        users: list[User] = []
        start_cursor: str | None = None

        while True:
            try:
                params: dict[str, Any] = {"page_size": min(page_size, 100)}
                if start_cursor:
                    params["start_cursor"] = start_cursor

                response = await self._client.api.users.list(**params)

                for user_data in response.get("results", []):
                    users.append(User.from_api_response(user_data))

                if not response.get("has_more"):
                    break

                start_cursor = response.get("next_cursor")

            except APIResponseError as e:
                raise map_api_error(e, resource_type="User") from e

        return users

    @retry_on_transient_async
    async def me(self) -> User:
        """
        Get the bot user associated with the current integration (async).

        Returns:
            User object for the bot
        """
        try:
            response = await self._client.api.users.me()
            return User.from_api_response(response)
        except APIResponseError as e:
            raise map_api_error(e, resource_type="User") from e
