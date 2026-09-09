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
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notion_ops.client import AsyncNotionOps, NotionOps
from notion_ops.models.block import Block, Blocks
from notion_ops.models.page import Page
from notion_ops.models.properties import SelectProperty, TitleProperty
from notion_ops.utils.revise import RevisionSchema

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


# ---------------------------------------------------------------------------
# AC-7-live (nops-cycle-3) — the dual-relation inverse, against a live workspace
# ---------------------------------------------------------------------------
#
# Everything above this line is a MOCKED lifecycle that happens to carry the
# `e2e` marker. This one is genuinely live: `revise_page` writes only
# `old.Next = [new]` and leaves `new.Previous` to Notion's dual-relation inverse,
# and Step 2 could establish that the pair IS dual (a read) but not that writing
# one side back-fills the other (a write). AC-7 binds the library's half — one
# relation write, on the old page. This binds the server's half, which is the
# only assumption in the cycle that in-repo evidence cannot settle.
#
# It needs a live workspace and a data source carrying a dual Next/Previous pair
# plus a `Status` select offering "Archived". Without a key, or without an id to
# aim at, it SKIPS (ENV-CONSTRAINED, not a miss). Its fixture pages are titled
# with a `nops-cycle-3 e2e ` prefix and trashed in a `finally`.


def _live_data_source_id() -> str | None:
    """The data source AC-7-live writes its fixtures into, or ``None`` to skip.

    ``NOTION_E2E_DATA_SOURCE_ID`` and nothing else. This used to fall back to the
    hub's configured Atoms data source, which meant a published library's own
    suite created, related, status-stamped and trashed fixture pages in a private
    **production** workspace whenever a key happened to be in the environment —
    and AC-7-live's own text asks for "a scratch data source". A missing variable
    is ENV-CONSTRAINED, which is a skip, not a licence to aim somewhere else
    (contract-11).
    """
    return os.environ.get("NOTION_E2E_DATA_SOURCE_ID") or None


LIVE_SCHEMA = RevisionSchema(
    title_property="Name",
    supersede_property="Next",
    predecessor_property="Previous",
    status_property="Status",
    archived_status_value="Archived",
    carry_properties=("Type",),
)

FIXTURE_PREFIX = "nops-cycle-3 e2e "


def _trash(client: Any, page_id: str) -> None:
    """Trash a fixture page. Only ever called on pages this test created."""
    try:
        client.api.pages.update(page_id=page_id, in_trash=True)
    except Exception:  # noqa: BLE001 - cleanup must not mask the real assertion
        try:
            client.api.pages.update(page_id=page_id, archived=True)
        except Exception:
            pass


@pytest.mark.e2e
class TestDualRelationInverseLive:
    """AC-7-live: writing one side of a dual relation populates the other."""

    def test_inverse_is_auto_set_by_the_server(self):
        if not os.environ.get("NOTION_API_KEY"):
            pytest.skip("ENV-CONSTRAINED: no NOTION_API_KEY in this environment")
        data_source_id = _live_data_source_id()
        if not data_source_id:
            pytest.skip(
                "ENV-CONSTRAINED: no data source id "
                "(set NOTION_E2E_DATA_SOURCE_ID or run from a workspace config)"
            )

        from notion_ops.client import NotionOps
        from notion_ops.utils.ids import extract_notion_id
        from notion_ops.utils.revise import revise_page

        client = NotionOps()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        source = client.api.pages.create(
            parent={"type": "data_source_id", "data_source_id": data_source_id},
            properties={
                "Name": {
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": f"{FIXTURE_PREFIX}source {stamp}"},
                        }
                    ]
                },
                "Status": {"select": {"name": "Draft"}},
            },
        )
        source_id = extract_notion_id(source["id"])
        successor_id = None
        try:
            result = revise_page(
                client,
                source_id,
                new_markdown="Revised body written by the nops-cycle-3 e2e test.",
                schema=LIVE_SCHEMA,
            )
            successor_id = result.canonical_page_id

            successor = client.api.pages.retrieve(page_id=successor_id)
            inverse = successor["properties"]["Previous"]["relation"]
            linked = {extract_notion_id(r["id"]) for r in inverse}
            assert source_id in linked, (
                "the dual-relation inverse was NOT auto-set: revise_page wrote only "
                "old.Next, so new.Previous must come from the server. If this fails, "
                "revise_page has to write both sides (spec §9 fallback)."
            )

            superseded = client.api.pages.retrieve(page_id=source_id)
            title = "".join(
                span.get("plain_text", "")
                for span in superseded["properties"]["Name"]["title"]
            )
            assert title.endswith("(v1)")
            assert superseded["properties"]["Status"]["select"]["name"] == "Archived"
            # D-6: archived is a STATUS, and the page is still live.
            assert superseded.get("in_trash") is not True
            assert superseded.get("archived") is not True

            forward = client.api.pages.retrieve(page_id=source_id)["properties"]["Next"]
            assert {extract_notion_id(r["id"]) for r in forward["relation"]} == {
                extract_notion_id(successor_id)
            }
        finally:
            for page_id in (successor_id, source_id):
                if page_id:
                    _trash(client, page_id)
