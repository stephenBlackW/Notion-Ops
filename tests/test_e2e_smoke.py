"""End-to-end smoke test covering the Notion operations lifecycle.

A single passing test here implicitly validates:
- Page model creation and serialization
- Block construction and type handling
- Page CRUD operations (create, read, update)
- Data source query operations
- Block append operations
- Model deserialization from API responses
- Both sync and async code paths (via parameterized fixture)

This high-level test subsumes many lower-level unit tests.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notion_ops.client import AsyncNotionOps, NotionOps
from notion_ops.models.block import Block, Blocks
from notion_ops.models.page import Page
from notion_ops.models.properties import SelectProperty, TitleProperty

# ---------------------------------------------------------------------------
# Helpers (mirrored from test_operations/conftest.py)
# ---------------------------------------------------------------------------


async def maybe_await(result: Any) -> Any:
    """Await coroutines, return plain values as-is."""
    if inspect.isawaitable(result):
        return await result
    return result


class ClientBundle:
    """Wraps a NotionOps/AsyncNotionOps instance with convenience metadata."""

    def __init__(self, client: NotionOps | AsyncNotionOps, *, is_async: bool):
        self.client = client
        self.is_async = is_async

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def setup_mock(self, attr_chain: str, **kwargs: Any) -> None:
        parts = attr_chain.split(".")
        target = self.client._notion
        for part in parts[:-1]:
            target = getattr(target, part)
        method_name = parts[-1]
        if self.is_async:
            setattr(target, method_name, AsyncMock(**kwargs))
        else:
            mock_method = getattr(target, method_name)
            for key, value in kwargs.items():
                setattr(mock_method, key, value)

    def get_mock(self, attr_chain: str) -> Any:
        obj = self.client._notion
        for part in attr_chain.split("."):
            obj = getattr(obj, part)
        return obj


@pytest.fixture(params=["sync", "async"])
def ops(request):
    """Provide a ClientBundle for both sync and async paths."""
    if request.param == "sync":
        with patch.dict("os.environ", {"NOTION_API_KEY": "test-secret-key"}):
            with patch("notion_ops.client.Client"):
                client = NotionOps()
                mock = MagicMock()
                mock.pages = MagicMock()
                mock.pages.properties = MagicMock()
                mock.blocks = MagicMock()
                mock.blocks.children = MagicMock()
                mock.databases = MagicMock()
                mock.users = MagicMock()
                mock.search = MagicMock()
                mock.request = MagicMock()
                client._notion = mock
                return ClientBundle(client, is_async=False)
    else:
        with patch.dict("os.environ", {"NOTION_API_KEY": "test-key"}):
            with patch("notion_ops.client.AsyncClient"):
                client = AsyncNotionOps()
                mock = AsyncMock()
                mock.pages = AsyncMock()
                mock.pages.properties = AsyncMock()
                mock.blocks = AsyncMock()
                mock.blocks.children = AsyncMock()
                mock.databases = AsyncMock()
                mock.users = AsyncMock()
                mock.search = AsyncMock()
                mock.request = AsyncMock()
                client._notion = mock
                return ClientBundle(client, is_async=True)


# ---------------------------------------------------------------------------
# E2E Notion CRUD lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestNotionCRUDLifecycle:
    """Full create -> read -> update -> query -> append blocks lifecycle."""

    @pytest.mark.asyncio
    async def test_page_lifecycle(
        self, ops, mock_page_response, mock_block_response
    ):
        """Create a page, read it back, update it, query for it, append blocks."""

        # -------------------------------------------------------------------
        # 1. Create a page with properties
        # -------------------------------------------------------------------
        created_response = mock_page_response(
            page_id="page-e2e-001",
            title="E2E Smoke Page",
            parent_type="data_source_id",
            parent_id="ds-e2e-001",
            properties={
                "Name": {
                    "id": "title",
                    "type": "title",
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": "E2E Smoke Page", "link": None},
                            "plain_text": "E2E Smoke Page",
                            "href": None,
                        }
                    ],
                },
                "Status": {
                    "id": "status-prop",
                    "type": "select",
                    "select": {"id": "opt-1", "name": "Draft", "color": "gray"},
                },
            },
        )
        ops.setup_mock("pages.create", return_value=created_response)

        page = await maybe_await(
            ops.pages.create(
                parent_id="ds-e2e-001",
                properties={
                    "Name": TitleProperty(value="E2E Smoke Page"),
                    "Status": SelectProperty(value="Draft"),
                },
            )
        )

        assert isinstance(page, Page)
        assert page.id == "page-e2e-001"
        assert page.get_title() == "E2E Smoke Page"
        assert page.get_property("Status") == "Draft"

        # -------------------------------------------------------------------
        # 2. Read the page back
        # -------------------------------------------------------------------
        ops.setup_mock("pages.retrieve", return_value=created_response)

        fetched = await maybe_await(ops.pages.get("page-e2e-001"))

        assert isinstance(fetched, Page)
        assert fetched.id == page.id
        assert fetched.get_title() == "E2E Smoke Page"

        # -------------------------------------------------------------------
        # 3. Update properties
        # -------------------------------------------------------------------
        updated_response = mock_page_response(
            page_id="page-e2e-001",
            title="E2E Smoke Page",
            parent_type="data_source_id",
            parent_id="ds-e2e-001",
            properties={
                "Name": {
                    "id": "title",
                    "type": "title",
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": "E2E Smoke Page", "link": None},
                            "plain_text": "E2E Smoke Page",
                            "href": None,
                        }
                    ],
                },
                "Status": {
                    "id": "status-prop",
                    "type": "select",
                    "select": {"id": "opt-2", "name": "Published", "color": "green"},
                },
            },
        )
        ops.setup_mock("pages.update", return_value=updated_response)

        updated_page = await maybe_await(
            ops.pages.update(
                "page-e2e-001",
                properties={"Status": SelectProperty(value="Published")},
            )
        )

        assert updated_page.get_property("Status") == "Published"
        assert updated_page.id == "page-e2e-001"

        # -------------------------------------------------------------------
        # 4. Query the data source for the page
        # -------------------------------------------------------------------
        query_response = {
            "object": "list",
            "results": [updated_response],
            "has_more": False,
            "next_cursor": None,
        }
        ops.setup_mock("request", return_value=query_response)

        query_result = await maybe_await(
            ops.data_sources.query(
                "ds-e2e-001",
                filter={
                    "property": "Status",
                    "select": {"equals": "Published"},
                },
            )
        )

        assert len(query_result.pages) == 1
        assert query_result.pages[0].id == "page-e2e-001"
        assert query_result.has_more is False

        # -------------------------------------------------------------------
        # 5. Append blocks to the page
        # -------------------------------------------------------------------
        heading_block_response = mock_block_response(
            block_id="block-e2e-h1",
            block_type="heading_1",
            text="Introduction",
            parent_id="page-e2e-001",
        )
        paragraph_block_response = mock_block_response(
            block_id="block-e2e-p1",
            block_type="paragraph",
            text="This is the body of the E2E smoke test page.",
            parent_id="page-e2e-001",
        )
        append_response = {
            "object": "list",
            "results": [heading_block_response, paragraph_block_response],
            "has_more": False,
            "next_cursor": None,
        }
        ops.setup_mock("blocks.children.append", return_value=append_response)

        blocks_to_add = [
            Blocks.heading_1("Introduction"),
            Blocks.paragraph("This is the body of the E2E smoke test page."),
        ]
        appended = await maybe_await(
            ops.blocks.append("page-e2e-001", blocks_to_add)
        )

        assert len(appended) == 2
        assert all(isinstance(b, Block) for b in appended)
        assert appended[0].get_plain_text() == "Introduction"
        assert appended[1].get_plain_text() == "This is the body of the E2E smoke test page."

        # -------------------------------------------------------------------
        # 6. Read blocks back from the page
        # -------------------------------------------------------------------
        children_response = {
            "object": "list",
            "results": [heading_block_response, paragraph_block_response],
            "has_more": False,
            "next_cursor": None,
        }
        ops.setup_mock("blocks.children.list", return_value=children_response)

        children = await maybe_await(ops.blocks.get_children("page-e2e-001"))

        assert len(children) == 2
        assert children[0].type.value == "heading_1"
        assert children[1].type.value == "paragraph"
        assert children[1].get_plain_text() == "This is the body of the E2E smoke test page."


@pytest.mark.e2e
class TestNotionDatabaseLifecycle:
    """Database create -> retrieve lifecycle."""

    @pytest.mark.asyncio
    async def test_database_create_and_retrieve(self, ops, mock_database_response):
        """Create a database, then retrieve it by ID."""

        # Create
        db_response = mock_database_response(
            database_id="db-e2e-001",
            title="E2E Test Database",
            description="Created during E2E smoke test",
        )
        ops.setup_mock("databases.create", return_value=db_response)

        from notion_ops.models.database import Database

        db = await maybe_await(
            ops.databases.create(
                parent_id="page-parent-001",
                title="E2E Test Database",
                description="Created during E2E smoke test",
            )
        )

        assert isinstance(db, Database)
        assert db.id == "db-e2e-001"
        assert db.title == "E2E Test Database"

        # Retrieve
        ops.setup_mock("databases.retrieve", return_value=db_response)

        fetched_db = await maybe_await(ops.databases.get("db-e2e-001"))

        assert fetched_db.id == db.id
        assert fetched_db.title == "E2E Test Database"
