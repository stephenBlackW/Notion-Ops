"""Tests for AsyncBlockOperations."""

from unittest.mock import AsyncMock

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.block import Block, Blocks, BlockType


class TestAsyncBlockGet:
    """Tests for AsyncBlockOperations.get."""

    @pytest.mark.asyncio
    async def test_get_block(self, async_notion_ops_client, mock_block_response):
        """Get block success path: calls blocks.retrieve and returns Block."""
        client = async_notion_ops_client
        expected_response = mock_block_response(
            block_id="block-get-001", block_type="paragraph", text="Test content"
        )
        client._notion.blocks.retrieve = AsyncMock(
            return_value=expected_response
        )

        block = await client.blocks.get("block-get-001")

        assert isinstance(block, Block)
        assert block.id == "block-get-001"
        assert block.type == BlockType.PARAGRAPH
        assert block.get_plain_text() == "Test content"
        client._notion.blocks.retrieve.assert_called_once_with(
            block_id="blockget001"
        )

    @pytest.mark.asyncio
    async def test_get_block_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Get block with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.blocks.retrieve = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "Could not find block."
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.blocks.get("block-missing-001")

        assert exc_info.value.resource_type == "Block"
        assert exc_info.value.resource_id == "blockmissing001"

    @pytest.mark.asyncio
    async def test_get_block_generic_error(self, async_notion_ops_client):
        """Get block with generic error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.blocks.retrieve = AsyncMock(
            side_effect=Exception("Unexpected error")
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve block"):
            await client.blocks.get("block-err-001")


class TestAsyncBlockGetChildren:
    """Tests for AsyncBlockOperations.get_children."""

    @pytest.mark.asyncio
    async def test_get_children(
        self, async_notion_ops_client, mock_block_response
    ):
        """Get children success path: calls blocks.children.list and returns Blocks."""
        client = async_notion_ops_client
        block1 = mock_block_response(
            block_id="child-001", text="First paragraph"
        )
        block2 = mock_block_response(
            block_id="child-002", block_type="heading_1", text="Title"
        )

        client._notion.blocks.children.list = AsyncMock(
            return_value={
                "object": "list",
                "results": [block1, block2],
                "has_more": False,
                "next_cursor": None,
            }
        )

        blocks = await client.blocks.get_children("page-parent-001")

        assert len(blocks) == 2
        assert all(isinstance(b, Block) for b in blocks)
        assert blocks[0].id == "child-001"
        assert blocks[0].type == BlockType.PARAGRAPH
        assert blocks[1].id == "child-002"
        assert blocks[1].type == BlockType.HEADING_1

    @pytest.mark.asyncio
    async def test_get_children_with_pagination(
        self, async_notion_ops_client, mock_block_response
    ):
        """Get children handles pagination across multiple API calls."""
        client = async_notion_ops_client
        block1 = mock_block_response(block_id="child-p1", text="Page 1 block")
        block2 = mock_block_response(block_id="child-p2", text="Page 2 block")

        client._notion.blocks.children.list = AsyncMock(
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
            ]
        )

        blocks = await client.blocks.get_children("page-parent-002")

        assert len(blocks) == 2
        assert blocks[0].id == "child-p1"
        assert blocks[1].id == "child-p2"

        # Verify pagination cursor was used
        assert client._notion.blocks.children.list.call_count == 2
        second_call = client._notion.blocks.children.list.call_args_list[1]
        assert second_call.kwargs.get("start_cursor") == "cursor-block-abc"

    @pytest.mark.asyncio
    async def test_get_children_recursive(
        self, async_notion_ops_client, mock_block_response
    ):
        """Get children with recursive=True fetches nested children."""
        client = async_notion_ops_client
        parent_block = mock_block_response(
            block_id="parent-block",
            text="Parent",
            has_children=True,
        )
        child_block = mock_block_response(
            block_id="nested-child",
            text="Nested content",
            has_children=False,
        )

        # First call: parent's children list
        # Second call: nested children of parent_block
        client._notion.blocks.children.list = AsyncMock(
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
            ]
        )

        blocks = await client.blocks.get_children(
            "page-recursive-001", recursive=True
        )

        assert len(blocks) == 1
        assert blocks[0].id == "parent-block"
        assert blocks[0].children is not None
        assert len(blocks[0].children) == 1
        assert blocks[0].children[0].id == "nested-child"

    @pytest.mark.asyncio
    async def test_get_children_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Get children for missing block raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.blocks.children.list = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.blocks.get_children("block-missing-001")

        assert exc_info.value.resource_type == "Block"


class TestAsyncBlockAppend:
    """Tests for AsyncBlockOperations.append."""

    @pytest.mark.asyncio
    async def test_append_blocks(
        self, async_notion_ops_client, mock_block_response
    ):
        """Append blocks success path: calls blocks.children.append and returns Blocks."""
        client = async_notion_ops_client
        created_block = mock_block_response(
            block_id="new-block-001", text="Appended text"
        )

        client._notion.blocks.children.append = AsyncMock(
            return_value={
                "object": "list",
                "results": [created_block],
            }
        )

        blocks_to_append = [Blocks.paragraph("Appended text")]
        result = await client.blocks.append("page-append-001", blocks_to_append)

        assert len(result) == 1
        assert isinstance(result[0], Block)
        assert result[0].id == "new-block-001"

        client._notion.blocks.children.append.assert_called_once()
        call_kwargs = (
            client._notion.blocks.children.append.call_args.kwargs
        )
        assert call_kwargs["block_id"] == "pageappend001"
        assert "children" in call_kwargs
        assert len(call_kwargs["children"]) == 1

    @pytest.mark.asyncio
    async def test_append_blocks_with_after(
        self, async_notion_ops_client, mock_block_response
    ):
        """Append blocks with after parameter inserts after specified block."""
        client = async_notion_ops_client
        created_block = mock_block_response(
            block_id="inserted-001", text="Inserted"
        )

        client._notion.blocks.children.append = AsyncMock(
            return_value={
                "object": "list",
                "results": [created_block],
            }
        )

        blocks_to_append = [Blocks.paragraph("Inserted")]
        await client.blocks.append(
            "page-insert-001", blocks_to_append, after="block-before-001"
        )

        call_kwargs = (
            client._notion.blocks.children.append.call_args.kwargs
        )
        assert call_kwargs["after"] == "blockbefore001"

    @pytest.mark.asyncio
    async def test_append_blocks_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Append to missing parent raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.blocks.children.append = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            )
        )

        with pytest.raises(NotFoundError):
            await client.blocks.append(
                "page-missing-001", [Blocks.paragraph("text")]
            )

    @pytest.mark.asyncio
    async def test_append_blocks_generic_error(self, async_notion_ops_client):
        """Append with generic error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.blocks.children.append = AsyncMock(
            side_effect=Exception("Server error")
        )

        with pytest.raises(NotionOpsError, match="Failed to append blocks"):
            await client.blocks.append(
                "page-err-001", [Blocks.paragraph("text")]
            )


class TestAsyncBlockDelete:
    """Tests for AsyncBlockOperations.delete."""

    @pytest.mark.asyncio
    async def test_delete_block(self, async_notion_ops_client):
        """Delete block success path: calls blocks.delete."""
        client = async_notion_ops_client
        client._notion.blocks.delete = AsyncMock(return_value=None)

        await client.blocks.delete("block-del-001")

        client._notion.blocks.delete.assert_called_once_with(
            block_id="blockdel001"
        )

    @pytest.mark.asyncio
    async def test_delete_block_not_found(
        self, async_notion_ops_client, make_api_error
    ):
        """Delete block with invalid ID raises NotFoundError."""
        client = async_notion_ops_client
        client._notion.blocks.delete = AsyncMock(
            side_effect=make_api_error(
                404, APIErrorCode.ObjectNotFound, "object_not_found"
            )
        )

        with pytest.raises(NotFoundError) as exc_info:
            await client.blocks.delete("block-missing-001")

        assert exc_info.value.resource_type == "Block"

    @pytest.mark.asyncio
    async def test_delete_block_generic_error(self, async_notion_ops_client):
        """Delete block with generic error raises NotionOpsError."""
        client = async_notion_ops_client
        client._notion.blocks.delete = AsyncMock(
            side_effect=Exception("Unexpected server error")
        )

        with pytest.raises(NotionOpsError, match="Failed to delete block"):
            await client.blocks.delete("block-err-001")


class TestAsyncBlockArchive:
    """Tests for AsyncBlockOperations.archive."""

    @pytest.mark.asyncio
    async def test_archive_block(
        self, async_notion_ops_client, mock_block_response
    ):
        """Archive block calls blocks.update with archived=True."""
        client = async_notion_ops_client
        archived_response = mock_block_response(
            block_id="block-arch-001",
            block_type="paragraph",
            text="Archived block",
            archived=True,
        )
        client._notion.blocks.update = AsyncMock(
            return_value=archived_response
        )

        block = await client.blocks.archive("block-arch-001")

        assert isinstance(block, Block)
        assert block.archived is True
        client._notion.blocks.update.assert_called_once_with(
            block_id="blockarch001",
            archived=True,
        )


class TestAsyncBlockUpdate:
    """Tests for AsyncBlockOperations.update."""

    @pytest.mark.asyncio
    async def test_update_block(
        self, async_notion_ops_client, mock_block_response
    ):
        """Update block success path: first gets block type, then updates."""
        client = async_notion_ops_client
        existing_block = mock_block_response(
            block_id="block-upd-001",
            block_type="paragraph",
            text="Old text",
        )
        updated_block = mock_block_response(
            block_id="block-upd-001",
            block_type="paragraph",
            text="New text",
        )

        client._notion.blocks.retrieve = AsyncMock(
            return_value=existing_block
        )
        client._notion.blocks.update = AsyncMock(
            return_value=updated_block
        )

        new_content = {
            "rich_text": [{"type": "text", "text": {"content": "New text"}}]
        }
        block = await client.blocks.update("block-upd-001", content=new_content)

        assert isinstance(block, Block)
        # Verify it first retrieved the block to get its type
        client._notion.blocks.retrieve.assert_called_once()
        # Then called update with the block type as key
        client._notion.blocks.update.assert_called_once()
        call_kwargs = client._notion.blocks.update.call_args.kwargs
        assert call_kwargs["block_id"] == "blockupd001"
        assert "paragraph" in call_kwargs
