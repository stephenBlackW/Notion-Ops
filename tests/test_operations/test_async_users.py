"""Tests for AsyncUserOperations."""

from unittest.mock import AsyncMock

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.operations.users import User


class TestAsyncUserGet:
    """Tests for AsyncUserOperations.get."""

    @pytest.mark.asyncio
    async def test_get_user(self, async_notion_ops_client, mock_user_response):
        """Get user success path: calls users.retrieve and returns User."""
        client = async_notion_ops_client
        expected_response = mock_user_response(
            user_id="user-get-001",
            name="Alice",
            email="alice@example.com",
        )
        client._notion.users.retrieve = AsyncMock(
            return_value=expected_response
        )

        user = await client.users.get("user-get-001")

        assert isinstance(user, User)
        assert user.id == "user-get-001"
        assert user.name == "Alice"
        assert user.type == "person"
        assert user.email == "alice@example.com"
        client._notion.users.retrieve.assert_called_once_with(
            user_id="user-get-001"
        )

    @pytest.mark.asyncio
    async def test_get_user_bot(
        self, async_notion_ops_client, mock_user_response
    ):
        """Get bot user returns correct type."""
        client = async_notion_ops_client
        expected_response = mock_user_response(
            user_id="bot-001",
            name="My Integration",
            user_type="bot",
        )
        client._notion.users.retrieve = AsyncMock(
            return_value=expected_response
        )

        user = await client.users.get("bot-001")

        assert user.type == "bot"
        assert user.email is None  # Bots don't have email

    @pytest.mark.asyncio
    async def test_get_user_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Get user with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.users.retrieve = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find user."
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.users.get("user-missing-001")

        assert exc_info.value.resource_type == "User"
        assert exc_info.value.resource_id == "user-missing-001"

    @pytest.mark.asyncio
    async def test_get_user_generic_error(self, async_notion_ops_client):
        """Get user with generic error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.users.retrieve = AsyncMock(
            side_effect=Exception("Unexpected error")
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve user"):
            await client.users.get("user-err-001")


class TestAsyncUserList:
    """Tests for AsyncUserOperations.list."""

    @pytest.mark.asyncio
    async def test_list_users(
        self, async_notion_ops_client, mock_user_response
    ):
        """List users success path: returns all users."""
        client = async_notion_ops_client
        user1 = mock_user_response(user_id="user-001", name="Alice")
        user2 = mock_user_response(user_id="user-002", name="Bob")

        client._notion.users.list = AsyncMock(
            return_value={
                "object": "list",
                "results": [user1, user2],
                "has_more": False,
                "next_cursor": None,
            }
        )

        users = await client.users.list()

        assert len(users) == 2
        assert all(isinstance(u, User) for u in users)
        assert users[0].id == "user-001"
        assert users[0].name == "Alice"
        assert users[1].id == "user-002"
        assert users[1].name == "Bob"

    @pytest.mark.asyncio
    async def test_list_users_with_pagination(
        self, async_notion_ops_client, mock_user_response
    ):
        """List users handles pagination across multiple API calls."""
        client = async_notion_ops_client
        user1 = mock_user_response(user_id="user-p1", name="User 1")
        user2 = mock_user_response(user_id="user-p2", name="User 2")
        user3 = mock_user_response(user_id="user-p3", name="User 3")

        client._notion.users.list = AsyncMock(
            side_effect=[
                {
                    "object": "list",
                    "results": [user1, user2],
                    "has_more": True,
                    "next_cursor": "cursor-user-abc",
                },
                {
                    "object": "list",
                    "results": [user3],
                    "has_more": False,
                    "next_cursor": None,
                },
            ]
        )

        users = await client.users.list()

        assert len(users) == 3
        assert users[0].id == "user-p1"
        assert users[2].id == "user-p3"

        # Verify pagination cursor was used
        assert client._notion.users.list.call_count == 2
        second_call = client._notion.users.list.call_args_list[1]
        assert second_call.kwargs.get("start_cursor") == "cursor-user-abc"

    @pytest.mark.asyncio
    async def test_list_users_error(self, async_notion_ops_client):
        """List users with error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.users.list = AsyncMock(
            side_effect=Exception("API error")
        )

        with pytest.raises(NotionOpsError, match="Failed to list users"):
            await client.users.list()


class TestAsyncUserMe:
    """Tests for AsyncUserOperations.me."""

    @pytest.mark.asyncio
    async def test_me(self, async_notion_ops_client, mock_user_response):
        """Me success path: calls users.me and returns the bot User."""
        client = async_notion_ops_client
        expected_response = mock_user_response(
            user_id="bot-me-001",
            name="My Integration Bot",
            user_type="bot",
        )
        client._notion.users.me = AsyncMock(
            return_value=expected_response
        )

        user = await client.users.me()

        assert isinstance(user, User)
        assert user.id == "bot-me-001"
        assert user.name == "My Integration Bot"
        assert user.type == "bot"
        client._notion.users.me.assert_called_once()

    @pytest.mark.asyncio
    async def test_me_error(self, async_notion_ops_client):
        """Me with error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.users.me = AsyncMock(
            side_effect=Exception("Authentication failed")
        )

        with pytest.raises(NotionOpsError, match="Failed to get current user"):
            await client.users.me()

    @pytest.mark.asyncio
    async def test_me_api_error(self, async_notion_ops_client, make_api_error):
        """Me with API error raises mapped error."""
        client = async_notion_ops_client
        client._notion.users.me = AsyncMock(
            side_effect=make_api_error(
                401, "unauthorized", "Invalid API key"
            )
        )

        from notion_ops.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError):
            await client.users.me()
