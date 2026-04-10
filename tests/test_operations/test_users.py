"""Tests for UserOperations (sync) and AsyncUserOperations (async).

Both code paths are exercised via the parametrised ``ops`` fixture.
"""

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import AuthenticationError, NotFoundError, NotionOpsError
from notion_ops.operations.users import User

from .conftest import maybe_await

# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestUserGet:
    """Tests for get (sync & async)."""

    @pytest.mark.asyncio
    async def test_get_user(self, ops, mock_user_response):
        expected = mock_user_response(
            user_id="user-get-001", name="Alice", email="alice@example.com"
        )
        ops.setup_mock("users.retrieve", return_value=expected)

        user = await maybe_await(ops.users.get("user-get-001"))

        assert isinstance(user, User)
        assert user.id == "user-get-001"
        assert user.name == "Alice"
        assert user.type == "person"
        assert user.email == "alice@example.com"
        ops.get_mock("users.retrieve").assert_called_once_with(
            user_id="user-get-001"
        )

    @pytest.mark.asyncio
    async def test_get_user_bot(self, ops, mock_user_response):
        expected = mock_user_response(
            user_id="bot-001", name="My Integration", user_type="bot"
        )
        ops.setup_mock("users.retrieve", return_value=expected)

        user = await maybe_await(ops.users.get("bot-001"))

        assert user.type == "bot"
        assert user.email is None

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "users.retrieve",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find user."
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.users.get("user-missing-001"))

        assert exc_info.value.resource_type == "User"
        assert exc_info.value.resource_id == "user-missing-001"

    @pytest.mark.asyncio
    async def test_get_user_generic_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock(
            "users.retrieve", side_effect=Exception("Unexpected error")
        )

        with pytest.raises(Exception, match="Unexpected error"):
            await maybe_await(ops.users.get("user-err-001"))


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestUserList:
    """Tests for list (sync & async)."""

    @pytest.mark.asyncio
    async def test_list_users(self, ops, mock_user_response):
        user1 = mock_user_response(user_id="user-001", name="Alice")
        user2 = mock_user_response(user_id="user-002", name="Bob")

        ops.setup_mock(
            "users.list",
            return_value={
                "object": "list",
                "results": [user1, user2],
                "has_more": False,
                "next_cursor": None,
            },
        )

        users = await maybe_await(ops.users.list())

        assert len(users) == 2
        assert all(isinstance(u, User) for u in users)
        assert users[0].id == "user-001"
        assert users[0].name == "Alice"
        assert users[1].id == "user-002"
        assert users[1].name == "Bob"

    @pytest.mark.asyncio
    async def test_list_users_with_pagination(self, ops, mock_user_response):
        user1 = mock_user_response(user_id="user-p1", name="User 1")
        user2 = mock_user_response(user_id="user-p2", name="User 2")
        user3 = mock_user_response(user_id="user-p3", name="User 3")

        ops.setup_mock(
            "users.list",
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
            ],
        )

        users = await maybe_await(ops.users.list())

        assert len(users) == 3
        assert users[0].id == "user-p1"
        assert users[2].id == "user-p3"

        mock = ops.get_mock("users.list")
        assert mock.call_count == 2
        second_call = mock.call_args_list[1]
        assert second_call.kwargs.get("start_cursor") == "cursor-user-abc"

    @pytest.mark.asyncio
    async def test_list_users_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock("users.list", side_effect=Exception("API error"))

        with pytest.raises(Exception, match="API error"):
            await maybe_await(ops.users.list())


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


class TestUserMe:
    """Tests for me (sync & async)."""

    @pytest.mark.asyncio
    async def test_me(self, ops, mock_user_response):
        expected = mock_user_response(
            user_id="bot-me-001", name="My Integration Bot", user_type="bot"
        )
        ops.setup_mock("users.me", return_value=expected)

        user = await maybe_await(ops.users.me())

        assert isinstance(user, User)
        assert user.id == "bot-me-001"
        assert user.name == "My Integration Bot"
        assert user.type == "bot"
        ops.get_mock("users.me").assert_called_once()

    @pytest.mark.asyncio
    async def test_me_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock(
            "users.me", side_effect=Exception("Authentication failed")
        )

        with pytest.raises(Exception, match="Authentication failed"):
            await maybe_await(ops.users.me())

    @pytest.mark.asyncio
    async def test_me_api_error(self, ops, make_api_error):
        ops.setup_mock(
            "users.me",
            side_effect=make_api_error(401, "unauthorized", "Invalid API key"),
        )

        with pytest.raises(AuthenticationError):
            await maybe_await(ops.users.me())
