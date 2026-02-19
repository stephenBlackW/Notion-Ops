"""Tests for BlockOperations."""

import pytest
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.block import Block, Blocks, BlockType
from notion_ops.operations.blocks import ChildPageInfo


class TestBlockGet:
    """Tests for BlockOperations.get."""

    def test_get_block(self, notion_ops_client, mock_block_response):
        """Get block success path: calls blocks.retrieve and returns Block."""
        expected_response = mock_block_response(
            block_id="block-get-001", block_type="paragraph", text="Test content"
        )
        notion_ops_client._notion.blocks.retrieve.return_value = expected_response

        block = notion_ops_client.blocks.get("block-get-001")

        assert isinstance(block, Block)
        assert block.id == "block-get-001"
        assert block.type == BlockType.PARAGRAPH
        assert block.get_plain_text() == "Test content"
        notion_ops_client._notion.blocks.retrieve.assert_called_once_with(
            block_id="blockget001"
        )

    def test_get_block_not_found(self, notion_ops_client, make_api_error):
        """Get block with invalid ID raises NotFoundError."""
        notion_ops_client._notion.blocks.retrieve.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "Could not find block."
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.blocks.get("block-missing-001")

        assert exc_info.value.resource_type == "Block"
        assert exc_info.value.resource_id == "blockmissing001"

    def test_get_block_generic_error(self, notion_ops_client):
        """Get block with generic error raises NotionOpsError."""
        notion_ops_client._notion.blocks.retrieve.side_effect = Exception(
            "Unexpected error"
        )

        with pytest.raises(NotionOpsError, match="Failed to retrieve block"):
            notion_ops_client.blocks.get("block-err-001")


class TestBlockGetChildren:
    """Tests for BlockOperations.get_children."""

    def test_get_children(self, notion_ops_client, mock_block_response):
        """Get children success path: calls blocks.children.list and returns Blocks."""
        block1 = mock_block_response(block_id="child-001", text="First paragraph")
        block2 = mock_block_response(
            block_id="child-002", block_type="heading_1", text="Title"
        )

        notion_ops_client._notion.blocks.children.list.return_value = {
            "object": "list",
            "results": [block1, block2],
            "has_more": False,
            "next_cursor": None,
        }

        blocks = notion_ops_client.blocks.get_children("page-parent-001")

        assert len(blocks) == 2
        assert all(isinstance(b, Block) for b in blocks)
        assert blocks[0].id == "child-001"
        assert blocks[0].type == BlockType.PARAGRAPH
        assert blocks[1].id == "child-002"
        assert blocks[1].type == BlockType.HEADING_1

    def test_get_children_with_pagination(self, notion_ops_client, mock_block_response):
        """Get children handles pagination across multiple API calls."""
        block1 = mock_block_response(block_id="child-p1", text="Page 1 block")
        block2 = mock_block_response(block_id="child-p2", text="Page 2 block")

        # First call returns 1 result with has_more=True
        # Second call returns 1 result with has_more=False
        notion_ops_client._notion.blocks.children.list.side_effect = [
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

        blocks = notion_ops_client.blocks.get_children("page-parent-002")

        assert len(blocks) == 2
        assert blocks[0].id == "child-p1"
        assert blocks[1].id == "child-p2"

        # Verify pagination cursor was used
        assert notion_ops_client._notion.blocks.children.list.call_count == 2
        second_call = notion_ops_client._notion.blocks.children.list.call_args_list[1]
        assert second_call.kwargs.get("start_cursor") == "cursor-block-abc"

    def test_get_children_recursive(self, notion_ops_client, mock_block_response):
        """Get children with recursive=True fetches nested children."""
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
        notion_ops_client._notion.blocks.children.list.side_effect = [
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

        blocks = notion_ops_client.blocks.get_children(
            "page-recursive-001", recursive=True
        )

        assert len(blocks) == 1
        assert blocks[0].id == "parent-block"
        assert blocks[0].children is not None
        assert len(blocks[0].children) == 1
        assert blocks[0].children[0].id == "nested-child"

    def test_get_children_not_found(self, notion_ops_client, make_api_error):
        """Get children for missing block raises NotFoundError."""
        notion_ops_client._notion.blocks.children.list.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "object_not_found"
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.blocks.get_children("block-missing-001")

        assert exc_info.value.resource_type == "Block"


class TestBlockAppend:
    """Tests for BlockOperations.append."""

    def test_append_blocks(self, notion_ops_client, mock_block_response):
        """Append blocks success path: calls blocks.children.append and returns Blocks."""
        created_block = mock_block_response(
            block_id="new-block-001", text="Appended text"
        )

        notion_ops_client._notion.blocks.children.append.return_value = {
            "object": "list",
            "results": [created_block],
        }

        blocks_to_append = [Blocks.paragraph("Appended text")]
        result = notion_ops_client.blocks.append("page-append-001", blocks_to_append)

        assert len(result) == 1
        assert isinstance(result[0], Block)
        assert result[0].id == "new-block-001"

        notion_ops_client._notion.blocks.children.append.assert_called_once()
        call_kwargs = notion_ops_client._notion.blocks.children.append.call_args.kwargs
        assert call_kwargs["block_id"] == "pageappend001"
        assert "children" in call_kwargs
        assert len(call_kwargs["children"]) == 1

    def test_append_blocks_with_after(self, notion_ops_client, mock_block_response):
        """Append blocks with after parameter inserts after specified block."""
        created_block = mock_block_response(block_id="inserted-001", text="Inserted")

        notion_ops_client._notion.blocks.children.append.return_value = {
            "object": "list",
            "results": [created_block],
        }

        blocks_to_append = [Blocks.paragraph("Inserted")]
        notion_ops_client.blocks.append(
            "page-insert-001", blocks_to_append, after="block-before-001"
        )

        call_kwargs = notion_ops_client._notion.blocks.children.append.call_args.kwargs
        assert call_kwargs["after"] == "blockbefore001"

    def test_append_blocks_not_found(self, notion_ops_client, make_api_error):
        """Append to missing parent raises NotFoundError."""
        notion_ops_client._notion.blocks.children.append.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "object_not_found"
        )

        with pytest.raises(NotFoundError):
            notion_ops_client.blocks.append(
                "page-missing-001", [Blocks.paragraph("text")]
            )


class TestBlockUpdate:
    """Tests for BlockOperations.update."""

    def test_update_block(self, notion_ops_client, mock_block_response):
        """Update block success path: first gets block type, then updates."""
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

        notion_ops_client._notion.blocks.retrieve.return_value = existing_block
        notion_ops_client._notion.blocks.update.return_value = updated_block

        new_content = {
            "rich_text": [{"type": "text", "text": {"content": "New text"}}]
        }
        block = notion_ops_client.blocks.update("block-upd-001", content=new_content)

        assert isinstance(block, Block)
        # Verify it first retrieved the block to get its type
        notion_ops_client._notion.blocks.retrieve.assert_called_once()
        # Then called update with the block type as key
        notion_ops_client._notion.blocks.update.assert_called_once()
        call_kwargs = notion_ops_client._notion.blocks.update.call_args.kwargs
        assert call_kwargs["block_id"] == "blockupd001"
        assert "paragraph" in call_kwargs

    def test_update_block_not_found(
        self, notion_ops_client, mock_block_response, make_api_error
    ):
        """Update block with not-found on update step raises NotFoundError."""
        existing_block = mock_block_response(
            block_id="block-upd-nf",
            block_type="paragraph",
            text="Exists",
        )
        notion_ops_client._notion.blocks.retrieve.return_value = existing_block
        notion_ops_client._notion.blocks.update.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "object_not_found"
        )

        with pytest.raises(NotFoundError):
            notion_ops_client.blocks.update(
                "block-upd-nf",
                content={"rich_text": [{"type": "text", "text": {"content": "x"}}]},
            )


class TestBlockDelete:
    """Tests for BlockOperations.delete."""

    def test_delete_block(self, notion_ops_client):
        """Delete block success path: calls blocks.delete."""
        notion_ops_client._notion.blocks.delete.return_value = None

        notion_ops_client.blocks.delete("block-del-001")

        notion_ops_client._notion.blocks.delete.assert_called_once_with(
            block_id="blockdel001"
        )

    def test_delete_block_not_found(self, notion_ops_client, make_api_error):
        """Delete block with invalid ID raises NotFoundError."""
        notion_ops_client._notion.blocks.delete.side_effect = make_api_error(
            404, APIErrorCode.ObjectNotFound, "object_not_found"
        )

        with pytest.raises(NotFoundError) as exc_info:
            notion_ops_client.blocks.delete("block-missing-001")

        assert exc_info.value.resource_type == "Block"

    def test_delete_block_generic_error(self, notion_ops_client):
        """Delete block with generic error raises NotionOpsError."""
        notion_ops_client._notion.blocks.delete.side_effect = Exception(
            "Unexpected server error"
        )

        with pytest.raises(NotionOpsError, match="Failed to delete block"):
            notion_ops_client.blocks.delete("block-err-001")


class TestBlockArchive:
    """Tests for BlockOperations.archive."""

    def test_archive_block(self, notion_ops_client, mock_block_response):
        """Archive block calls blocks.update with archived=True."""
        archived_response = mock_block_response(
            block_id="block-arch-001",
            block_type="paragraph",
            text="Archived block",
            archived=True,
        )
        notion_ops_client._notion.blocks.update.return_value = (
            archived_response
        )

        block = notion_ops_client.blocks.archive("block-arch-001")

        assert isinstance(block, Block)
        assert block.archived is True
        notion_ops_client._notion.blocks.update.assert_called_once_with(
            block_id="blockarch001",
            archived=True,
        )


class TestBlockGetChildPages:
    """Tests for BlockOperations.get_child_pages."""

    def test_get_child_pages(self, notion_ops_client):
        """get_child_pages returns ChildPageInfo for child_page blocks."""
        notion_ops_client._notion.blocks.children.list.return_value = {
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
                    "paragraph": {
                        "rich_text": [{"plain_text": "text"}]
                    },
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
        }

        result = notion_ops_client.blocks.get_child_pages(
            "parent-page-001"
        )

        assert len(result) == 2
        assert all(isinstance(cp, ChildPageInfo) for cp in result)
        assert result[0].id == "child-page-001"
        assert result[0].title == "Sub Page A"
        assert result[0].has_children is True
        assert result[1].id == "child-page-002"
        assert result[1].title == "Sub Page B"
        assert result[1].has_children is False


class TestBlockExtractId:
    """Tests for BlockOperations._extract_id."""

    def test_extract_id_plain(self, notion_ops_client):
        """Plain ID with dashes gets dashes removed."""
        ops = notion_ops_client.blocks
        assert ops._extract_id("abc-def-123") == "abcdef123"

    def test_extract_id_from_url_with_fragment(self, notion_ops_client):
        """Notion URL with # fragment extracts block ID."""
        ops = notion_ops_client.blocks
        url = "https://www.notion.so/Page-Title-abc123#blockid456"
        result = ops._extract_id(url)
        assert result == "blockid456"
