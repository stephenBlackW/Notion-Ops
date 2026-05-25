"""Block-to-markdown extraction and non-destructive block repair.

The generic, DB-agnostic inverse of :func:`markdown_to_blocks`: render a page's
blocks back to markdown so layered or garbled content — a paragraph that is
really a markdown list, a dumped YAML blob, an over-long block — can be
re-parsed into proper blocks.

:func:`repair_blocks` round-trips clean blocks unchanged and expands the layered
ones (``markdown_to_blocks(blocks_to_markdown(blocks))``). It is pure: it never
calls the Notion API.

Accepts both block shapes: the API-response shape from
``blocks.children.list`` (rich-text segments carry ``plain_text``/``href``) and
the API-input shape produced by :func:`markdown_to_blocks` (segments carry
``text.content``/``text.link``). Nested children (table rows, toggle bodies) are
read from ``<block>[<type>]["children"]`` or a top-level ``"children"`` key.
"""

from __future__ import annotations

from typing import Any

from notion_ops.utils.markdown import markdown_to_blocks

__all__ = ["blocks_to_markdown", "repair_blocks"]


def _seg_text(seg: dict[str, Any]) -> str:
    """Plain text of a rich-text segment (API-response or API-input shape)."""
    text = seg.get("plain_text")
    if text is None:
        text = seg.get("text", {}).get("content", "")
    return text or ""


def _seg_link(seg: dict[str, Any]) -> str | None:
    """Link URL of a rich-text segment, if any (API-response or API-input)."""
    href = seg.get("href")
    if href:
        return href
    link = seg.get("text", {}).get("link")
    if isinstance(link, dict):
        return link.get("url")
    return None


def _rich_to_md(rich_text: list[dict[str, Any]] | None) -> str:
    """Render a rich-text array back to inline markdown.

    Re-emits annotations as markdown delimiters so clean blocks round-trip
    through :func:`markdown_to_blocks`. ``code`` is innermost and links
    outermost, matching how the inline parser nests them.
    """
    parts: list[str] = []
    for seg in rich_text or []:
        text = _seg_text(seg)
        if text == "":
            continue
        ann = seg.get("annotations", {}) or {}
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("bold"):
            text = f"**{text}**"
        link = _seg_link(seg)
        if link:
            text = f"[{text}]({link})"
        parts.append(text)
    return "".join(parts)


def _children(block: dict[str, Any], content: dict[str, Any]) -> list[dict[str, Any]]:
    return content.get("children") or block.get("children") or []


def _raw_text(rich_text: list[dict[str, Any]] | None) -> str:
    """Concatenate segment text without markdown wrapping (for code blocks)."""
    return "".join(_seg_text(seg) for seg in (rich_text or []))


def _table_to_md(block: dict[str, Any], content: dict[str, Any]) -> list[str]:
    rows = _children(block, content)
    rendered: list[list[str]] = []
    for row in rows:
        row_content = row.get(row.get("type", "table_row"), {})
        cells = row_content.get("cells", [])
        rendered.append([_rich_to_md(cell) for cell in cells])
    if not rendered:
        return []
    width = content.get("table_width") or len(rendered[0])
    header, *body = rendered
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return lines


def _block_to_md(block: dict[str, Any]) -> list[str]:
    """Render a single block to a list of markdown lines."""
    btype = block.get("type")
    if not btype:
        return []
    content = block.get(btype, {}) or {}
    rich = content.get("rich_text", [])
    text = _rich_to_md(rich)

    if btype == "paragraph":
        return [text]
    if btype == "heading_1":
        return [f"# {text}"]
    if btype == "heading_2":
        return [f"## {text}"]
    if btype == "heading_3":
        return [f"### {text}"]
    if btype == "bulleted_list_item":
        return [f"- {text}"]
    if btype == "numbered_list_item":
        return [f"1. {text}"]
    if btype == "to_do":
        mark = "x" if content.get("checked") else " "
        return [f"- [{mark}] {text}"]
    if btype == "quote":
        return [f"> {text}"]
    if btype == "divider":
        return ["---"]
    if btype == "code":
        lang = content.get("language") or "plain text"
        fence = "" if lang == "plain text" else lang
        return [f"```{fence}", _raw_text(rich), "```"]
    if btype == "table":
        return _table_to_md(block, content)
    if btype == "toggle":
        lines = ["<details>", f"<summary>{text}</summary>", ""]
        for child in _children(block, content):
            lines.extend(_block_to_md(child))
        lines.append("</details>")
        return lines
    if btype == "callout":
        return [text] if text else []
    # Unknown / unsupported block type: surface whatever text it carries so the
    # repair never silently drops content.
    return [text] if text else []


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    """Render a list of Notion blocks back to a markdown string.

    Per-block-type extraction (paragraph, headings, code, lists, to-dos, quote,
    divider, table, toggle); other types degrade to their plain text. Blocks are
    separated by a blank line so :func:`markdown_to_blocks` re-parses them
    cleanly.
    """
    chunks: list[str] = []
    for block in blocks or []:
        lines = _block_to_md(block)
        chunk = "\n".join(lines)
        if chunk.strip() or block.get("type") == "divider":
            chunks.append(chunk)
    return "\n\n".join(chunks)


def repair_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-destructively repair blocks via versioned re-conversion.

    ``markdown_to_blocks(blocks_to_markdown(blocks))``: clean blocks round-trip
    unchanged; blocks carrying layered/raw-markdown content (a paragraph that is
    really a list, dumped YAML, an over-long block) expand into proper blocks.
    Pure — no API calls.
    """
    return markdown_to_blocks(blocks_to_markdown(blocks))
