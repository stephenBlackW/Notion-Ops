"""Tests for UserOperations."""

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.operations.users import User


class TestUserGet:
    """Tests for UserOperations.get."""

    def test_get_user(self, notion_ops_client, mock_user_response):
        """Get user success path: calls users.retrieve and returns User."""
        expected_response = mock_user_response(
            user_id="user-get-001",
            name="Alice",
            email="alice@example.com",
        )
        notion_ops_client._notion.users.retrieve.return_value = expected_response

        user = notion_ops_client.users.get("user-get-001")

        assert isinstance(user, User)
        assert user.id == "user-get-001"
        assert user.name == "Alice"
        assert user.type == "person"
        assert user.email == "alice@example.com"
        notion_ops_client._notion.users.retrieve.assert_called_once_with(
            user_id="user-get-001"
        )

    def test_get_user_bot(self, notion_ops_client, mock_user_response):
        """Get bot user returns correct type."""
        expected_response = mock_user_response(
            user_id="bot-001",
            name="My Integration",
            user_type="bot",
        )
        notion_ops_client._notion.users.retrieve.return_value = expected_response

        user = notion_ops_client.users.get("bot-001")

        assert user.type == "bot"
        assert user.email is None  # Bots don't have email

    def test_get_user_not_found(self, notion_ops_client, make_api_error):
        """Get user with invalid ID raises NotFoundError."""
        notion_ops_client._notion.users.retrieve.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "Could not find user."
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.users.get("user-missing-001")

        assert exc_info.value.resource_type == "User"
        assert exc_info.value.resource_id == "user-missing-001"

    def test_get_user_generic_error(self, notion_ops_client):
        """Get user with generic error raises NotionOpsError."""
        notion_ops_client._notion.users.retrieve.side_effect = Exception(
            "Unexpected error"
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve user"):
            notion_ops_client.users.get("user-err-001")


class TestUserList:
    """Tests for UserOperations.list."""

    def test_list_users(self, notion_ops_client, mock_user_response):
        """List users success path: returns all users."""
        user1 = mock_user_response(user_id="user-001", name="Alice")
        user2 = mock_user_response(user_id="user-002", name="Bob")

        notion_ops_client._notion.users.list.return_value = {
            "object": "list",
            "results": [user1, user2],
            "has_more": False,
            "next_cursor": None,
        }

        users = notion_ops_client.users.list()

        assert len(users) == 2
        assert all(isinstance(u, User) for u in users)
        assert users[0].id == "user-001"
        assert users[0].name == "Alice"
        assert users[1].id == "user-002"
        assert users[1].name == "Bob"

    def test_list_users_with_pagination(self, notion_ops_client, mock_user_response):
        """List users handles pagination across multiple API calls."""
        user1 = mock_user_response(user_id="user-p1", name="User 1")
        user2 = mock_user_response(user_id="user-p2", name="User 2")
        user3 = mock_user_response(user_id="user-p3", name="User 3")

        # First call returns 2 users with has_more=True
        # Second call returns 1 user with has_more=False
        notion_ops_client._notion.users.list.side_effect = [
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

        users = notion_ops_client.users.list()

        assert len(users) == 3
        assert users[0].id == "user-p1"
        assert users[2].id == "user-p3"

        # Verify pagination cursor was used
        assert notion_ops_client._notion.users.list.call_count == 2
        second_call = notion_ops_client._notion.users.list.call_args_list[1]
        assert second_call.kwargs.get("start_cursor") == "cursor-user-abc"

    def test_list_users_error(self, notion_ops_client):
        """List users with error raises NotionOpsError."""
        notion_ops_client._notion.users.list.side_effect = Exception("API error")

        with pytest.raises(NotionOpsError, match="Failed to list users"):
            notion_ops_client.users.list()


class TestUserMe:
    """Tests for UserOperations.me."""

    def test_me(self, notion_ops_client, mock_user_response):
        """Me success path: calls users.me and returns the bot User."""
        expected_response = mock_user_response(
            user_id="bot-me-001",
            name="My Integration Bot",
            user_type="bot",
        )
        notion_ops_client._notion.users.me.return_value = expected_response

        user = notion_ops_client.users.me()

        assert isinstance(user, User)
        assert user.id == "bot-me-001"
        assert user.name == "My Integration Bot"
        assert user.type == "bot"
        notion_ops_client._notion.users.me.assert_called_once()

    def test_me_error(self, notion_ops_client):
        """Me with error raises NotionOpsError."""
        notion_ops_client._notion.users.me.side_effect = Exception(
            "Authentication failed"
        )

        with pytest.raises(NotionOpsError, match="Failed to get current user"):
            notion_ops_client.users.me()
