"""Tests for BlockOperations (sync) and AsyncBlockOperations (async).

Both code paths are exercised via the parametrised ``ops`` fixture.
"""

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.block import Block, Blocks, BlockType
from notion_ops.operations.blocks import ChildPageInfo

from .conftest import maybe_await

# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestBlockGet:
    """Tests for get (sync & async)."""

    @pytest.mark.asyncio
    async def test_get_block(self, ops, mock_block_response):
        expected = mock_block_response(
            block_id="block-get-001", block_type="paragraph", text="Test content"
        )
        ops.setup_mock("blocks.retrieve", return_value=expected)

        block = await maybe_await(ops.blocks.get("block-get-001"))

        assert isinstance(block, Block)
        assert block.id == "block-get-001"
        assert block.type == BlockType.PARAGRAPH
        assert block.get_plain_text() == "Test content"
        ops.get_mock("blocks.retrieve").assert_called_once_with(
            block_id="blockget001"
        )

    @pytest.mark.asyncio
    async def test_get_block_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "blocks.retrieve",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find block."
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.blocks.get("block-missing-001"))

        assert exc_info.value.resource_type == "Block"
        assert exc_info.value.resource_id == "blockmissing001"

    @pytest.mark.asyncio
    async def test_get_block_generic_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock(
            "blocks.retrieve", side_effect=Exception("Unexpected error")
        )

        with pytest.raises(Exception, match="Unexpected error"):
            await maybe_await(ops.blocks.get("block-err-001"))


# ---------------------------------------------------------------------------
# Get children
# ---------------------------------------------------------------------------


class TestBlockGetChildren:
    """Tests for get_children (sync & async)."""

    @pytest.mark.asyncio
    async def test_get_children(self, ops, mock_block_response):
        block1 = mock_block_response(block_id="child-001", text="First paragraph")
        block2 = mock_block_response(
            block_id="child-002", block_type="heading_1", text="Title"
        )

        ops.setup_mock(
            "blocks.children.list",
            return_value={
                "object": "list",
                "results": [block1, block2],
                "has_more": False,
                "next_cursor": None,
            },
        )

        blocks = await maybe_await(ops.blocks.get_children("page-parent-001"))

        assert len(blocks) == 2
        assert all(isinstance(b, Block) for b in blocks)
        assert blocks[0].id == "child-001"
        assert blocks[0].type == BlockType.PARAGRAPH
        assert blocks[1].id == "child-002"
        assert blocks[1].type == BlockType.HEADING_1

    @pytest.mark.asyncio
    async def test_get_children_with_pagination(self, ops, mock_block_response):
        block1 = mock_block_response(block_id="child-p1", text="Page 1 block")
        block2 = mock_block_response(block_id="child-p2", text="Page 2 block")

        ops.setup_mock(
            "blocks.children.list",
            side_effect=[
                {
                    "object": "list",
                    "results": [block1],
                    "has_more": True,
                    "next_cursor": "cursor-block-abc",
                },
                {
                    "object": "list",
                    "results": [block2],
                    "has_more": False,
                    "next_cursor": None,
                },
            ],
        )

        blocks = await maybe_await(ops.blocks.get_children("page-parent-002"))

        assert len(blocks) == 2
        assert blocks[0].id == "child-p1"
        assert blocks[1].id == "child-p2"

        mock = ops.get_mock("blocks.children.list")
        assert mock.call_count == 2
        second_call = mock.call_args_list[1]
        assert second_call.kwargs.get("start_cursor") == "cursor-block-abc"

    @pytest.mark.asyncio
    async def test_get_children_recursive(self, ops, mock_block_response):
        parent_block = mock_block_response(
            block_id="parent-block", text="Parent", has_children=True
        )
        child_block = mock_block_response(
            block_id="nested-child", text="Nested content", has_children=False
        )

        ops.setup_mock(
            "blocks.children.list",
            side_effect=[
                {
                    "object": "list",
                    "results": [parent_block],
                    "has_more": False,
                    "next_cursor": None,
                },
                {
                    "object": "list",
                    "results": [child_block],
                    "has_more": False,
                    "next_cursor": None,
                },
            ],
        )

        blocks = await maybe_await(
            ops.blocks.get_children("page-recursive-001", recursive=True)
        )

        assert len(blocks) == 1
        assert blocks[0].id == "parent-block"
        assert blocks[0].children is not None
        assert len(blocks[0].children) == 1
        assert blocks[0].children[0].id == "nested-child"

    @pytest.mark.asyncio
    async def test_get_children_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "blocks.children.list",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.blocks.get_children("block-missing-001"))

        assert exc_info.value.resource_type == "Block"


# ---------------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------------


class TestBlockAppend:
    """Tests for append (sync & async)."""

    @pytest.mark.asyncio
    async def test_append_blocks(self, ops, mock_block_response):
        created = mock_block_response(block_id="new-block-001", text="Appended text")

        ops.setup_mock(
            "blocks.children.append",
            return_value={"object": "list", "results": [created]},
        )

        result = await maybe_await(
            ops.blocks.append("page-append-001", [Blocks.paragraph("Appended text")])
        )

        assert len(result) == 1
        assert isinstance(result[0], Block)
        assert result[0].id == "new-block-001"

        mock = ops.get_mock("blocks.children.append")
        mock.assert_called_once()
        call_kwargs = mock.call_args.kwargs
        assert call_kwargs["block_id"] == "pageappend001"
        assert "children" in call_kwargs
        assert len(call_kwargs["children"]) == 1

    @pytest.mark.asyncio
    async def test_append_blocks_with_after(self, ops, mock_block_response):
        created = mock_block_response(block_id="inserted-001", text="Inserted")

        ops.setup_mock(
            "blocks.children.append",
            return_value={"object": "list", "results": [created]},
        )

        await maybe_await(
            ops.blocks.append(
                "page-insert-001",
                [Blocks.paragraph("Inserted")],
                after="block-before-001",
            )
        )

        call_kwargs = ops.get_mock("blocks.children.append").call_args.kwargs
        assert call_kwargs["after"] == "blockbefore001"

    @pytest.mark.asyncio
    async def test_append_blocks_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "blocks.children.append",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        with pytest.raises(NotFoundError):
            await maybe_await(
                ops.blocks.append("page-missing-001", [Blocks.paragraph("text")])
            )

    @pytest.mark.asyncio
    async def test_append_blocks_generic_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock(
            "blocks.children.append",
            side_effect=Exception("Server error"),
        )

        with pytest.raises(Exception, match="Server error"):
            await maybe_await(
                ops.blocks.append("page-err-001", [Blocks.paragraph("text")])
            )


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestBlockUpdate:
    """Tests for update (sync & async)."""

    @pytest.mark.asyncio
    async def test_update_block(self, ops, mock_block_response):
        existing = mock_block_response(
            block_id="block-upd-001", block_type="paragraph", text="Old text"
        )
        updated = mock_block_response(
            block_id="block-upd-001", block_type="paragraph", text="New text"
        )

        ops.setup_mock("blocks.retrieve", return_value=existing)
        ops.setup_mock("blocks.update", return_value=updated)

        new_content = {
            "rich_text": [{"type": "text", "text": {"content": "New text"}}]
        }
        block = await maybe_await(
            ops.blocks.update("block-upd-001", content=new_content)
        )

        assert isinstance(block, Block)
        ops.get_mock("blocks.retrieve").assert_called_once()
        ops.get_mock("blocks.update").assert_called_once()
        call_kwargs = ops.get_mock("blocks.update").call_args.kwargs
        assert call_kwargs["block_id"] == "blockupd001"
        assert "paragraph" in call_kwargs

    @pytest.mark.asyncio
    async def test_update_block_not_found(self, ops, mock_block_response, make_api_error):
        existing = mock_block_response(
            block_id="block-upd-nf", block_type="paragraph", text="Exists"
        )
        ops.setup_mock("blocks.retrieve", return_value=existing)
        ops.setup_mock(
            "blocks.update",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        with pytest.raises(NotFoundError):
            await maybe_await(
                ops.blocks.update(
                    "block-upd-nf",
                    content={"rich_text": [{"type": "text", "text": {"content": "x"}}]},
                )
            )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestBlockDelete:
    """Tests for delete (sync & async)."""

    @pytest.mark.asyncio
    async def test_delete_block(self, ops):
        ops.setup_mock("blocks.delete", return_value=None)

        await maybe_await(ops.blocks.delete("block-del-001"))

        ops.get_mock("blocks.delete").assert_called_once_with(
            block_id="blockdel001"
        )

    @pytest.mark.asyncio
    async def test_delete_block_not_found(self, ops, make_api_error):
        ops.setup_mock(
            "blocks.delete",
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            ),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await maybe_await(ops.blocks.delete("block-missing-001"))

        assert exc_info.value.resource_type == "Block"

    @pytest.mark.asyncio
    async def test_delete_block_generic_error(self, ops):
        """Generic exceptions propagate directly (not wrapped in NotionOpsError)."""
        ops.setup_mock(
            "blocks.delete", side_effect=Exception("Unexpected server error")
        )

        with pytest.raises(Exception, match="Unexpected server error"):
            await maybe_await(ops.blocks.delete("block-err-001"))


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


class TestBlockArchive:
    """Tests for archive (sync & async)."""

    @pytest.mark.asyncio
    async def test_archive_block(self, ops, mock_block_response):
        archived = mock_block_response(
            block_id="block-arch-001",
            block_type="paragraph",
            text="Archived block",
            archived=True,
        )
        ops.setup_mock("blocks.update", return_value=archived)

        block = await maybe_await(ops.blocks.archive("block-arch-001"))

        assert isinstance(block, Block)
        assert block.archived is True
        ops.get_mock("blocks.update").assert_called_once_with(
            block_id="blockarch001", archived=True
        )


# ---------------------------------------------------------------------------
# Get child pages
# ---------------------------------------------------------------------------


class TestBlockGetChildPages:
    """Tests for get_child_pages (sync & async)."""

    @pytest.mark.asyncio
    async def test_get_child_pages(self, ops):
        ops.setup_mock(
            "blocks.children.list",
            return_value={
                "object": "list",
                "results": [
                    {
                        "object": "block",
                        "id": "child-page-001",
                        "type": "child_page",
                        "child_page": {"title": "Sub Page A"},
                        "has_children": True,
                    },
                    {
                        "object": "block",
                        "id": "block-paragraph-001",
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"plain_text": "text"}]},
                        "has_children": False,
                    },
                    {
                        "object": "block",
                        "id": "child-page-002",
                        "type": "child_page",
                        "child_page": {"title": "Sub Page B"},
                        "has_children": False,
                    },
                ],
                "has_more": False,
                "next_cursor": None,
            },
        )

        result = await maybe_await(ops.blocks.get_child_pages("parent-page-001"))

        assert len(result) == 2
        assert all(isinstance(cp, ChildPageInfo) for cp in result)
        assert result[0].id == "child-page-001"
        assert result[0].title == "Sub Page A"
        assert result[0].has_children is True
        assert result[1].id == "child-page-002"
        assert result[1].title == "Sub Page B"
        assert result[1].has_children is False


# ---------------------------------------------------------------------------
# extract_notion_id (shared utility, was _extract_id)
# ---------------------------------------------------------------------------


class TestBlockExtractId:
    """Tests for the shared extract_notion_id utility."""

    def test_extract_id_plain(self, ops):
        from notion_ops.utils.ids import extract_notion_id
        assert extract_notion_id("abc-def-123") == "abcdef123"

    def test_extract_id_from_url_with_fragment(self, ops):
        from notion_ops.utils.ids import extract_notion_id
        url = "https://www.notion.so/Page-Title-abc123#blockid456"
        assert extract_notion_id(url) == "blockid456"
