"""User operations for Notion Operations library."""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from notion_ops.exceptions import NotFoundError, NotionOpsError

if TYPE_CHECKING:
    from notion_ops.client import NotionOps


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

    def get(self, user_id: str) -> User:
        """
        Retrieve a user by ID.

        Args:
            user_id: The user ID

        Returns:
            User object
        """
        try:
            response = self._client._notion.users.retrieve(user_id=user_id)
            return User.from_api_response(response)
        except Exception as e:
            if "object_not_found" in str(e).lower():
                raise NotFoundError("User", user_id) from e
            raise NotionOpsError(f"Failed to retrieve user: {e}") from e

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

                response = self._client._notion.users.list(**params)

                for user_data in response.get("results", []):
                    users.append(User.from_api_response(user_data))

                if not response.get("has_more"):
                    break

                start_cursor = response.get("next_cursor")

            except Exception as e:
                raise NotionOpsError(f"Failed to list users: {e}") from e

        return users

    def me(self) -> User:
        """
        Get the bot user associated with the current integration.

        Returns:
            User object for the bot
        """
        try:
            response = self._client._notion.users.me()
            return User.from_api_response(response)
        except Exception as e:
            raise NotionOpsError(f"Failed to get current user: {e}") from e
