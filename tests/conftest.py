"""Shared pytest fixtures for Notion Operations tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from notion_client import APIResponseError
from notion_client.errors import APIErrorCode

from notion_ops.client import AsyncNotionOps, NotionOps


@pytest.fixture
def mock_notion_client():
    """A MagicMock of notion_client.Client with all sub-endpoints."""
    mock = MagicMock()
    # Sub-endpoints
    mock.pages = MagicMock()
    mock.pages.properties = MagicMock()
    mock.blocks = MagicMock()
    mock.blocks.children = MagicMock()
    mock.databases = MagicMock()
    mock.users = MagicMock()
    mock.search = MagicMock()
    mock.request = MagicMock()
    return mock


@pytest.fixture
def notion_ops_client(mock_notion_client):
    """A NotionOps instance with the mock notion_client injected."""
    with patch.dict("os.environ", {"NOTION_API_KEY": "test-secret-key"}):
        with patch("notion_ops.client.Client"):
            client = NotionOps()
            client._notion = mock_notion_client
            return client


@pytest.fixture
def mock_page_response():
    """Factory fixture returning realistic Notion API page response dicts."""

    def _factory(
        page_id="page-abc123",
        title="Test Page",
        parent_type="database_id",
        parent_id="db-xyz789",
        archived=False,
        properties=None,
        icon=None,
        cover=None,
    ):
        if properties is None:
            properties = {
                "Name": {
                    "id": "title",
                    "type": "title",
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": title, "link": None},
                            "annotations": {
                                "bold": False,
                                "italic": False,
                                "strikethrough": False,
                                "underline": False,
                                "code": False,
                                "color": "default",
                            },
                            "plain_text": title,
                            "href": None,
                        }
                    ],
                },
            }

        return {
            "object": "page",
            "id": page_id,
            "created_time": "2025-01-15T12:00:00.000Z",
            "last_edited_time": "2025-01-16T08:30:00.000Z",
            "created_by": {"object": "user", "id": "user-001"},
            "last_edited_by": {"object": "user", "id": "user-001"},
            "cover": cover,
            "icon": icon,
            "parent": {parent_type: parent_id},
            "archived": archived,
            "in_trash": False,
            "properties": properties,
            "url": f"https://www.notion.so/Test-Page-{page_id.replace('-', '')}",
            "public_url": None,
        }

    return _factory


@pytest.fixture
def mock_block_response():
    """Factory fixture returning realistic Notion API block response dicts."""

    def _factory(
        block_id="block-abc123",
        block_type="paragraph",
        text="Hello world",
        has_children=False,
        archived=False,
        parent_type="page_id",
        parent_id="page-xyz789",
    ):
        content = {}
        if block_type == "paragraph":
            content = {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": text, "link": None},
                        "annotations": {
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "default",
                        },
                        "plain_text": text,
                        "href": None,
                    }
                ],
                "color": "default",
            }
        elif block_type == "heading_1":
            content = {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": text, "link": None},
                        "plain_text": text,
                    }
                ],
                "is_toggleable": False,
                "color": "default",
            }
        elif block_type == "divider":
            content = {}

        return {
            "object": "block",
            "id": block_id,
            "parent": {"type": parent_type, parent_type: parent_id},
            "created_time": "2025-01-15T12:00:00.000Z",
            "last_edited_time": "2025-01-15T12:00:00.000Z",
            "created_by": {"object": "user", "id": "user-001"},
            "last_edited_by": {"object": "user", "id": "user-001"},
            "has_children": has_children,
            "archived": archived,
            "in_trash": False,
            "type": block_type,
            block_type: content,
        }

    return _factory


@pytest.fixture
def mock_database_response():
    """Factory fixture returning realistic Notion API database response dicts."""

    def _factory(
        database_id="db-abc123",
        title="Test Database",
        description=None,
        archived=False,
        is_inline=False,
        parent_type="page_id",
        parent_id="page-parent-001",
        properties=None,
        icon=None,
        cover=None,
    ):
        if properties is None:
            properties = {
                "Name": {
                    "id": "title",
                    "name": "Name",
                    "type": "title",
                    "title": {},
                },
                "Status": {
                    "id": "status-prop",
                    "name": "Status",
                    "type": "select",
                    "select": {
                        "options": [
                            {"id": "opt-1", "name": "Active", "color": "green"},
                            {"id": "opt-2", "name": "Archived", "color": "gray"},
                        ]
                    },
                },
            }

        title_arr = [
            {
                "type": "text",
                "text": {"content": title, "link": None},
                "plain_text": title,
            }
        ]

        desc_arr = []
        if description:
            desc_arr = [
                {
                    "type": "text",
                    "text": {"content": description, "link": None},
                    "plain_text": description,
                }
            ]

        return {
            "object": "database",
            "id": database_id,
            "cover": cover,
            "icon": icon,
            "created_time": "2025-01-10T10:00:00.000Z",
            "last_edited_time": "2025-01-15T14:30:00.000Z",
            "created_by": {"object": "user", "id": "user-001"},
            "last_edited_by": {"object": "user", "id": "user-001"},
            "title": title_arr,
            "description": desc_arr,
            "is_inline": is_inline,
            "properties": properties,
            "parent": {"type": parent_type, parent_type: parent_id},
            "url": f"https://www.notion.so/{database_id.replace('-', '')}",
            "archived": archived,
            "in_trash": False,
            "public_url": None,
        }

    return _factory


@pytest.fixture
def mock_user_response():
    """Factory fixture returning realistic Notion API user response dicts."""

    def _factory(
        user_id="user-abc123",
        name="Test User",
        user_type="person",
        email="test@example.com",
        avatar_url="https://example.com/avatar.png",
    ):
        response = {
            "object": "user",
            "id": user_id,
            "type": user_type,
            "name": name,
            "avatar_url": avatar_url,
        }

        if user_type == "person":
            response["person"] = {"email": email}
        elif user_type == "bot":
            response["bot"] = {
                "owner": {"type": "workspace", "workspace": True},
                "workspace_name": "Test Workspace",
            }

        return response

    return _factory


@pytest.fixture
def make_api_error():
    """Factory fixture that creates ``APIResponseError`` instances for testing.

    Usage::

        err = make_api_error(404, APIErrorCode.ObjectNotFound, "Not found")
        mock.side_effect = err
    """

    def _factory(
        status: int = 500,
        code: str | APIErrorCode = "internal_server_error",
        message: str = "An error occurred",
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> APIResponseError:
        hdrs = httpx.Headers(headers or {"content-type": "application/json"})
        if body is None:
            code_str = code.value if isinstance(code, APIErrorCode) else code
            body = (
                f'{{"object":"error","code":"{code_str}",'
                f'"message":"{message}"}}'
            )
        return APIResponseError(
            code=code,
            status=status,
            message=message,
            headers=hdrs,
            raw_body_text=body,
        )

    return _factory


@pytest.fixture
def async_notion_ops_client():
    """AsyncNotionOps with mocked async Notion client."""
    with patch.dict("os.environ", {"NOTION_API_KEY": "test-key"}):
        with patch("notion_ops.client.AsyncClient"):
            client = AsyncNotionOps()
            client._notion = AsyncMock()
            # Set up sub-endpoint mocks
            client._notion.pages = AsyncMock()
            client._notion.pages.properties = AsyncMock()
            client._notion.blocks = AsyncMock()
            client._notion.blocks.children = AsyncMock()
            client._notion.databases = AsyncMock()
            client._notion.users = AsyncMock()
            client._notion.search = AsyncMock()
            client._notion.request = AsyncMock()
            return client
