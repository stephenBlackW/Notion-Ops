"""AC-1 (nops-cycle-3) — the read-only comments surface.

``notion_ops`` had no comments operation at all before this cycle, so the check
ISS-029's workaround prescribes ("call get_comments and refuse if it returns any
discussion") was not expressible against the library. These tests bind the
surface that makes it expressible:

- pagination across ``has_more`` pages, in API order,
- page-level threads (``parent.type == "page_id"``) versus block-anchored ones
  (``parent.type == "block_id"``),
- ``has_discussion`` as the boolean the guard consumes,
- the retry wrap and the error mapping the rest of ``operations/`` uses,
- a URL accepted wherever an id is.

The comment payloads mirror the shape in Notion's API reference. The live shape
was NOT re-verified against a populated thread during Step 2 (no sampled page
carried an open discussion), which is why the mapper reads every field with
``.get`` and why one test feeds it a deliberately impoverished payload.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from notion_client import APIResponseError
from notion_client.errors import APIErrorCode

from notion_ops.client import NotionOps
from notion_ops.exceptions import PermissionError as NotionPermissionError
from notion_ops.operations.comments import (
    AsyncCommentOperations,
    CommentOperations,
    Discussion,
    list_discussions,
    list_discussions_async,
)
from notion_ops.utils.ids import extract_notion_id

PAGE = "11111111-1111-1111-1111-111111111111"
BLOCK = "22222222-2222-2222-2222-222222222222"
# Every operation runs its argument through extract_notion_id, which strips the
# dashes; the id that reaches the SDK is therefore the compact form.
PAGE_ID_SENT = extract_notion_id(PAGE)


def _comment(
    cid: str,
    text: str,
    *,
    discussion: str = "d-1",
    block_id: str | None = None,
    user: str | None = "user-1",
) -> dict[str, Any]:
    """One API comment object. ``block_id=None`` makes it a page-level thread."""
    parent = (
        {"type": "block_id", "block_id": block_id}
        if block_id
        else {"type": "page_id", "page_id": PAGE}
    )
    return {
        "object": "comment",
        "id": cid,
        "parent": parent,
        "discussion_id": discussion,
        "created_time": "2026-09-01T00:00:00.000Z",
        "last_edited_time": "2026-09-01T00:00:00.000Z",
        "created_by": {"object": "user", "id": user} if user else None,
        "rich_text": [
            {
                "type": "text",
                "text": {"content": text, "link": None},
                "plain_text": text,
                "href": None,
            }
        ],
    }


class CommentFakeClient:
    """A fake SDK exposing only ``api.comments.list``, with a recorded call log.

    ``pages`` is a list of API list-envelopes returned in order, so a multi-page
    listing is expressed by handing the fake two envelopes.
    """

    def __init__(self, pages: list[dict[str, Any]]):
        self._pages = pages
        self.calls: list[dict[str, Any]] = []
        client = self

        class _Comments:
            def list(self, **params: Any) -> dict[str, Any]:
                client.calls.append(dict(params))
                index = len(client.calls) - 1
                if index >= len(client._pages):
                    raise AssertionError(
                        f"comments.list called {index + 1} times but only "
                        f"{len(client._pages)} response page(s) were configured"
                    )
                return client._pages[index]

        class _API:
            comments = _Comments()

        self.api = _API()


def _envelope(
    results: list[dict[str, Any]],
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
) -> dict[str, Any]:
    return {
        "object": "list",
        "type": "comment",
        "results": results,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@pytest.fixture
def ops_client() -> Any:
    """A ``NotionOps`` whose SDK is a MagicMock, for the operations-layer tests."""
    with patch.dict("os.environ", {"NOTION_API_KEY": "test-secret-key"}):
        with patch("notion_ops.client.Client"):
            client = NotionOps()
            client._notion = MagicMock()
            return client


# ---------------------------------------------------------------------------
# AC-1 — pagination, ordering, and the page-level/block-anchored distinction
# ---------------------------------------------------------------------------


class TestListDiscussions:
    def test_paginates_and_preserves_order(self):
        """Two pages of results become three Discussions in API order."""
        client = CommentFakeClient(
            [
                _envelope(
                    [
                        _comment("c-1", "first, page level"),
                        _comment("c-2", "second, anchored", block_id=BLOCK),
                    ],
                    has_more=True,
                    next_cursor="cursor-1",
                ),
                _envelope([_comment("c-3", "third, page level", discussion="d-2")]),
            ]
        )

        found = list_discussions(client, PAGE)

        assert [d.id for d in found] == ["c-1", "c-2", "c-3"]
        assert [d.plain_text for d in found] == [
            "first, page level",
            "second, anchored",
            "third, page level",
        ]
        # The block-anchored one carries its anchor; the page-level ones do not.
        assert found[0].parent_block_id is None
        assert found[1].parent_block_id == BLOCK
        assert found[2].parent_block_id is None
        assert [d.discussion_id for d in found] == ["d-1", "d-1", "d-2"]
        assert all(d.created_by == "user-1" for d in found)
        assert all(isinstance(d, Discussion) for d in found)

    def test_second_request_carries_the_cursor(self):
        client = CommentFakeClient(
            [
                _envelope([_comment("c-1", "a")], has_more=True, next_cursor="cur"),
                _envelope([_comment("c-2", "b")]),
            ]
        )

        list_discussions(client, PAGE)

        assert len(client.calls) == 2
        assert client.calls[0]["block_id"] == PAGE_ID_SENT
        assert "start_cursor" not in client.calls[0]
        assert client.calls[1]["start_cursor"] == "cur"

    def test_empty_results_is_empty_list(self):
        client = CommentFakeClient([_envelope([])])
        assert list_discussions(client, PAGE) == []

    def test_has_more_without_cursor_stops_rather_than_looping(self):
        """A malformed envelope must terminate, not spin on the same cursor."""
        client = CommentFakeClient([_envelope([_comment("c-1", "a")], has_more=True)])

        found = list_discussions(client, PAGE)

        assert [d.id for d in found] == ["c-1"]
        assert len(client.calls) == 1

    def test_accepts_a_url_and_sends_the_bare_id(self):
        client = CommentFakeClient([_envelope([])])

        list_discussions(client, f"https://www.notion.so/Some-Page-{PAGE_ID_SENT}")

        assert client.calls[0]["block_id"] == PAGE_ID_SENT

    def test_impoverished_payload_degrades_field_by_field(self):
        """A comment missing every optional key maps without raising."""
        client = CommentFakeClient([_envelope([{"id": "c-x"}])])

        (only,) = list_discussions(client, PAGE)

        assert only.id == "c-x"
        assert only.discussion_id == ""
        assert only.parent_block_id is None
        assert only.created_by is None
        assert only.plain_text == ""


class TestCommentOperations:
    def test_list_delegates_and_has_discussion_is_true(self, ops_client):
        ops_client.api.comments.list.return_value = _envelope(
            [_comment("c-1", "please fix the second paragraph", block_id=BLOCK)]
        )

        found = ops_client.comments.list(PAGE)

        assert isinstance(ops_client.comments, CommentOperations)
        assert [d.id for d in found] == ["c-1"]
        assert found[0].parent_block_id == BLOCK
        assert ops_client.comments.has_discussion(PAGE) is True

    def test_has_discussion_is_false_on_empty(self, ops_client):
        ops_client.api.comments.list.return_value = _envelope([])

        assert ops_client.comments.list(PAGE) == []
        assert ops_client.comments.has_discussion(PAGE) is False

    def test_403_maps_to_permission_error(self, ops_client):
        """Without the read-comments capability the API returns 403; the guard
        must see a typed PermissionError, not a raw SDK error."""
        ops_client.api.comments.list.side_effect = APIResponseError(
            code=APIErrorCode.RestrictedResource,
            status=403,
            message="Insufficient permissions",
            headers=None,
            raw_body_text='{"object":"error","code":"restricted_resource"}',
        )

        with pytest.raises(NotionPermissionError):
            ops_client.comments.list(PAGE)

    def test_transient_503_is_retried(self, ops_client):
        """The listing is retry-wrapped like every other read in operations/."""
        ops_client.api.comments.list.side_effect = [
            APIResponseError(
                code=APIErrorCode.ServiceUnavailable,
                status=503,
                message="503 Service Unavailable",
                headers=None,
                raw_body_text='{"object":"error","code":"service_unavailable"}',
            ),
            _envelope([_comment("c-1", "survived")]),
        ]

        with patch("notion_ops.utils.retry.time.sleep"):
            found = ops_client.comments.list(PAGE)

        assert [d.plain_text for d in found] == ["survived"]
        assert ops_client.api.comments.list.call_count == 2


class TestAsyncCommentOperations:
    """The async twin must exist and behave identically (client parity)."""

    async def test_async_list_and_has_discussion(self):
        class _AsyncComments:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def list(self, **params: Any) -> dict[str, Any]:
                self.calls.append(dict(params))
                if len(self.calls) == 1:
                    return _envelope(
                        [_comment("c-1", "a")], has_more=True, next_cursor="cur"
                    )
                return _envelope([_comment("c-2", "b", block_id=BLOCK)])

        class _API:
            comments = _AsyncComments()

        class _Client:
            api = _API()

        client = _Client()

        found = await list_discussions_async(client, PAGE)

        assert [d.id for d in found] == ["c-1", "c-2"]
        assert found[1].parent_block_id == BLOCK
        assert _API.comments.calls[1]["start_cursor"] == "cur"

    async def test_async_operations_wrapper(self):
        class _AsyncComments:
            async def list(self, **params: Any) -> dict[str, Any]:
                return _envelope([_comment("c-1", "a")])

        class _API:
            comments = _AsyncComments()

        class _Client:
            api = _API()

        ops = AsyncCommentOperations(_Client())  # type: ignore[arg-type]

        assert [d.id for d in await ops.list(PAGE)] == ["c-1"]
        assert await ops.has_discussion(PAGE) is True
