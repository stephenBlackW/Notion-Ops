"""Block CRUD operations for Notion Operations library."""

from typing import TYPE_CHECKING, Any

from notion_ops.exceptions import NotFoundError, NotionOpsError
from notion_ops.models.block import Block

if TYPE_CHECKING:
    from notion_ops.client import NotionOps


class BlockOperations:
    """CRUD operations for Notion blocks."""

    def __init__(self, client: "NotionOps"):
        self._client = client

    def get(self, block_id: str) -> Block:
        """
        Retrieve a block by ID.

        Args:
            block_id: The block ID

        Returns:
            Block object
        """
        block_id = self._extract_id(block_id)

        try:
            response = self._client._notion.blocks.retrieve(block_id=block_id)
            return Block.from_api_response(response)
        except Exception as e:
            if "object_not_found" in str(e).lower():
                raise NotFoundError("Block", block_id) from e
            raise NotionOpsError(f"Failed to retrieve block: {e}") from e

    def get_children(
        self,
        block_id: str,
        *,
        page_size: int = 100,
        recursive: bool = False,
    ) -> list[Block]:
        """
        Get child blocks of a block or page.

        Args:
            block_id: Block or page ID
            page_size: Results per page
            recursive: Fetch nested children recursively

        Returns:
            List of Block objects

        Example:
            blocks = client.blocks.get_children(page_id, recursive=True)
            for block in blocks:
                print(f"{block.type}: {block.get_plain_text()}")
        """
        block_id = self._extract_id(block_id)

        blocks: list[Block] = []
        start_cursor: str | None = None

        while True:
            try:
                params: dict[str, Any] = {
                    "block_id": block_id,
                    "page_size": min(page_size, 100),
                }
                if start_cursor:
                    params["start_cursor"] = start_cursor

                response = self._client._notion.blocks.children.list(**params)

                for block_data in response.get("results", []):
                    block = Block.from_api_response(block_data)

                    # Recursively fetch children if requested
                    if recursive and block.has_children:
                        block.children = self.get_children(
                            block.id,  # type: ignore
                            page_size=page_size,
                            recursive=True,
                        )

                    blocks.append(block)

                if not response.get("has_more"):
                    break

                start_cursor = response.get("next_cursor")

            except Exception as e:
                if "object_not_found" in str(e).lower():
                    raise NotFoundError("Block", block_id) from e
                raise NotionOpsError(f"Failed to get block children: {e}") from e

        return blocks

    def append(
        self,
        parent_id: str,
        children: list[Block],
        *,
        after: str | None = None,
    ) -> list[Block]:
        """
        Append blocks to a parent block or page.

        Args:
            parent_id: Parent block or page ID
            children: Blocks to append
            after: Insert after this block ID (optional)

        Returns:
            Created Block objects

        Example:
            client.blocks.append(
                page_id,
                [
                    Blocks.heading_2("New Section"),
                    Blocks.paragraph("Some content here."),
                    Blocks.bulleted_list("First item"),
                    Blocks.bulleted_list("Second item"),
                ]
            )
        """
        parent_id = self._extract_id(parent_id)

        # Convert blocks to API format
        children_data = [block.to_api_format() for block in children]

        try:
            params: dict[str, Any] = {
                "block_id": parent_id,
                "children": children_data,
            }
            if after:
                params["after"] = self._extract_id(after)

            response = self._client._notion.blocks.children.append(**params)

            return [Block.from_api_response(b) for b in response.get("results", [])]

        except Exception as e:
            if "object_not_found" in str(e).lower():
                raise NotFoundError("Block", parent_id) from e
            raise NotionOpsError(f"Failed to append blocks: {e}") from e

    def update(
        self,
        block_id: str,
        content: dict[str, Any],
    ) -> Block:
        """
        Update a block's content.

        Args:
            block_id: The block ID
            content: New content for the block type

        Returns:
            Updated Block object

        Example:
            client.blocks.update(
                "block_abc123",
                content={"rich_text": [{"type": "text", "text": {"content": "Updated text"}}]}
            )
        """
        block_id = self._extract_id(block_id)

        # First get the block to know its type
        existing_block = self.get(block_id)

        try:
            update_data = {existing_block.type.value: content}
            response = self._client._notion.blocks.update(
                block_id=block_id,
                **update_data,
            )
            return Block.from_api_response(response)
        except Exception as e:
            if "object_not_found" in str(e).lower():
                raise NotFoundError("Block", block_id) from e
            raise NotionOpsError(f"Failed to update block: {e}") from e

    def delete(self, block_id: str) -> None:
        """
        Delete a block.

        Args:
            block_id: The block ID
        """
        block_id = self._extract_id(block_id)

        try:
            self._client._notion.blocks.delete(block_id=block_id)
        except Exception as e:
            if "object_not_found" in str(e).lower():
                raise NotFoundError("Block", block_id) from e
            raise NotionOpsError(f"Failed to delete block: {e}") from e

    def archive(self, block_id: str) -> Block:
        """
        Archive a block (same as delete).

        Args:
            block_id: The block ID

        Returns:
            Archived Block object
        """
        block_id = self._extract_id(block_id)

        try:
            response = self._client._notion.blocks.update(
                block_id=block_id,
                archived=True,
            )
            return Block.from_api_response(response)
        except Exception as e:
            if "object_not_found" in str(e).lower():
                raise NotFoundError("Block", block_id) from e
            raise NotionOpsError(f"Failed to archive block: {e}") from e

    def _extract_id(self, id_or_url: str) -> str:
        """Extract block ID from URL or return as-is."""
        if id_or_url.startswith("http"):
            # Extract from Notion URL
            path = id_or_url.split("notion.so/")[-1].split("?")[0]
            if "#" in path:
                # Block ID might be after #
                path = path.split("#")[-1]
            if "/" in path:
                path = path.split("/")[-1]
            if "-" in path:
                path = path.split("-")[-1]
            return path
        return id_or_url.replace("-", "")
