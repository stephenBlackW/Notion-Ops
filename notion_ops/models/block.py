"""Block models for Notion Operations library."""

from enum import Enum
from typing import Any

from pydantic import BaseModel


class BlockType(str, Enum):
    """Notion block types."""

    PARAGRAPH = "paragraph"
    HEADING_1 = "heading_1"
    HEADING_2 = "heading_2"
    HEADING_3 = "heading_3"
    BULLETED_LIST_ITEM = "bulleted_list_item"
    NUMBERED_LIST_ITEM = "numbered_list_item"
    TO_DO = "to_do"
    TOGGLE = "toggle"
    CODE = "code"
    QUOTE = "quote"
    CALLOUT = "callout"
    DIVIDER = "divider"
    TABLE_OF_CONTENTS = "table_of_contents"
    BREADCRUMB = "breadcrumb"
    BOOKMARK = "bookmark"
    IMAGE = "image"
    VIDEO = "video"
    FILE = "file"
    PDF = "pdf"
    EMBED = "embed"
    TABLE = "table"
    TABLE_ROW = "table_row"
    COLUMN_LIST = "column_list"
    COLUMN = "column"
    SYNCED_BLOCK = "synced_block"
    TEMPLATE = "template"
    LINK_PREVIEW = "link_preview"
    LINK_TO_PAGE = "link_to_page"
    EQUATION = "equation"
    CHILD_PAGE = "child_page"
    CHILD_DATABASE = "child_database"
    UNSUPPORTED = "unsupported"

    @classmethod
    def _missing_(cls, value: object) -> "BlockType | None":
        """Handle unknown block types from the API gracefully."""
        return cls.UNSUPPORTED


class Block(BaseModel):
    """Represents a Notion block."""

    id: str | None = None
    type: BlockType
    content: dict[str, Any]
    children: list["Block"] | None = None
    has_children: bool = False
    created_time: str | None = None
    last_edited_time: str | None = None
    archived: bool = False
    parent: dict[str, Any] | None = None

    def to_api_format(self) -> dict[str, Any]:
        """Convert to Notion API format for creating blocks."""
        block_data: dict[str, Any] = {
            "object": "block",
            "type": self.type.value,
            self.type.value: self.content,
        }

        if self.children:
            block_data[self.type.value]["children"] = [
                child.to_api_format() for child in self.children
            ]

        return block_data

    def get_plain_text(self) -> str:
        """Extract plain text from the block's rich_text content."""
        rich_text = self.content.get("rich_text", [])
        return "".join(rt.get("plain_text", "") for rt in rich_text)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "Block":
        """Create a Block from Notion API response."""
        block_type = BlockType(data["type"])
        content = data.get(data["type"], {})

        return cls(
            id=data.get("id"),
            type=block_type,
            content=content,
            has_children=data.get("has_children", False),
            created_time=data.get("created_time"),
            last_edited_time=data.get("last_edited_time"),
            archived=data.get("archived", False),
            parent=data.get("parent"),
        )


def _make_rich_text(text: str, **annotations: Any) -> list[dict[str, Any]]:
    """Create rich text array from plain text."""
    rt: dict[str, Any] = {"type": "text", "text": {"content": text}}

    # Add annotations if provided
    if annotations:
        rt["annotations"] = {
            "bold": annotations.get("bold", False),
            "italic": annotations.get("italic", False),
            "strikethrough": annotations.get("strikethrough", False),
            "underline": annotations.get("underline", False),
            "code": annotations.get("code", False),
            "color": annotations.get("color", "default"),
        }

    # Add link if provided
    if "link" in annotations:
        rt["text"]["link"] = {"url": annotations["link"]}

    return [rt]


class Blocks:
    """Factory for creating block objects."""

    @staticmethod
    def paragraph(text: str, **annotations: Any) -> Block:
        """Create a paragraph block."""
        return Block(
            type=BlockType.PARAGRAPH,
            content={"rich_text": _make_rich_text(text, **annotations)},
        )

    @staticmethod
    def heading_1(text: str, toggleable: bool = False) -> Block:
        """Create a heading 1 block."""
        return Block(
            type=BlockType.HEADING_1,
            content={
                "rich_text": _make_rich_text(text),
                "is_toggleable": toggleable,
            },
        )

    @staticmethod
    def heading_2(text: str, toggleable: bool = False) -> Block:
        """Create a heading 2 block."""
        return Block(
            type=BlockType.HEADING_2,
            content={
                "rich_text": _make_rich_text(text),
                "is_toggleable": toggleable,
            },
        )

    @staticmethod
    def heading_3(text: str, toggleable: bool = False) -> Block:
        """Create a heading 3 block."""
        return Block(
            type=BlockType.HEADING_3,
            content={
                "rich_text": _make_rich_text(text),
                "is_toggleable": toggleable,
            },
        )

    @staticmethod
    def bulleted_list(text: str, children: list[Block] | None = None) -> Block:
        """Create a bulleted list item block."""
        content: dict[str, Any] = {"rich_text": _make_rich_text(text)}
        return Block(
            type=BlockType.BULLETED_LIST_ITEM,
            content=content,
            children=children,
        )

    @staticmethod
    def numbered_list(text: str, children: list[Block] | None = None) -> Block:
        """Create a numbered list item block."""
        content: dict[str, Any] = {"rich_text": _make_rich_text(text)}
        return Block(
            type=BlockType.NUMBERED_LIST_ITEM,
            content=content,
            children=children,
        )

    @staticmethod
    def todo(text: str, checked: bool = False, children: list[Block] | None = None) -> Block:
        """Create a to-do block."""
        content: dict[str, Any] = {
            "rich_text": _make_rich_text(text),
            "checked": checked,
        }
        return Block(
            type=BlockType.TO_DO,
            content=content,
            children=children,
        )

    @staticmethod
    def toggle(text: str, children: list[Block] | None = None) -> Block:
        """Create a toggle block."""
        content: dict[str, Any] = {"rich_text": _make_rich_text(text)}
        return Block(
            type=BlockType.TOGGLE,
            content=content,
            children=children,
        )

    @staticmethod
    def code(code: str, language: str = "python", caption: str | None = None) -> Block:
        """Create a code block."""
        content: dict[str, Any] = {
            "rich_text": _make_rich_text(code),
            "language": language,
        }
        if caption:
            content["caption"] = _make_rich_text(caption)
        return Block(type=BlockType.CODE, content=content)

    @staticmethod
    def quote(text: str, children: list[Block] | None = None) -> Block:
        """Create a quote block."""
        return Block(
            type=BlockType.QUOTE,
            content={"rich_text": _make_rich_text(text)},
            children=children,
        )

    @staticmethod
    def callout(
        text: str,
        emoji: str = "💡",
        color: str = "gray_background",
        children: list[Block] | None = None,
    ) -> Block:
        """Create a callout block."""
        return Block(
            type=BlockType.CALLOUT,
            content={
                "rich_text": _make_rich_text(text),
                "icon": {"type": "emoji", "emoji": emoji},
                "color": color,
            },
            children=children,
        )

    @staticmethod
    def divider() -> Block:
        """Create a divider block."""
        return Block(type=BlockType.DIVIDER, content={})

    @staticmethod
    def table_of_contents(color: str = "default") -> Block:
        """Create a table of contents block."""
        return Block(
            type=BlockType.TABLE_OF_CONTENTS,
            content={"color": color},
        )

    @staticmethod
    def breadcrumb() -> Block:
        """Create a breadcrumb block."""
        return Block(type=BlockType.BREADCRUMB, content={})

    @staticmethod
    def bookmark(url: str, caption: str | None = None) -> Block:
        """Create a bookmark block."""
        content: dict[str, Any] = {"url": url}
        if caption:
            content["caption"] = _make_rich_text(caption)
        return Block(type=BlockType.BOOKMARK, content=content)

    @staticmethod
    def image(url: str, caption: str | None = None) -> Block:
        """Create an external image block."""
        content: dict[str, Any] = {
            "type": "external",
            "external": {"url": url},
        }
        if caption:
            content["caption"] = _make_rich_text(caption)
        return Block(type=BlockType.IMAGE, content=content)

    @staticmethod
    def video(url: str, caption: str | None = None) -> Block:
        """Create an external video block."""
        content: dict[str, Any] = {
            "type": "external",
            "external": {"url": url},
        }
        if caption:
            content["caption"] = _make_rich_text(caption)
        return Block(type=BlockType.VIDEO, content=content)

    @staticmethod
    def embed(url: str, caption: str | None = None) -> Block:
        """Create an embed block."""
        content: dict[str, Any] = {"url": url}
        if caption:
            content["caption"] = _make_rich_text(caption)
        return Block(type=BlockType.EMBED, content=content)

    @staticmethod
    def equation(expression: str) -> Block:
        """Create an equation block (LaTeX)."""
        return Block(
            type=BlockType.EQUATION,
            content={"expression": expression},
        )

    @staticmethod
    def link_to_page(page_id: str) -> Block:
        """Create a link to page block."""
        return Block(
            type=BlockType.LINK_TO_PAGE,
            content={"type": "page_id", "page_id": page_id},
        )

    @staticmethod
    def table(
        table_width: int,
        has_column_header: bool = False,
        has_row_header: bool = False,
        children: list[Block] | None = None,
    ) -> Block:
        """Create a table block."""
        return Block(
            type=BlockType.TABLE,
            content={
                "table_width": table_width,
                "has_column_header": has_column_header,
                "has_row_header": has_row_header,
            },
            children=children,
        )

    @staticmethod
    def table_row(cells: list[str]) -> Block:
        """Create a table row block."""
        return Block(
            type=BlockType.TABLE_ROW,
            content={
                "cells": [_make_rich_text(cell) for cell in cells],
            },
        )
