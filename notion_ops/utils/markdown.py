"""Markdown to Notion block conversion utilities."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from notion_ops.models.block import Block, Blocks, BlockType

logger = logging.getLogger(__name__)

# Notion API limits
_RICH_TEXT_CHAR_LIMIT = 2000
_SAFE_CHAR_LIMIT = 1900  # Leave room for formatting overhead
_MAX_BLOCKS_PER_REQUEST = 100
_MAX_PAYLOAD_BYTES = 300_000  # Conservative payload size limit


def _find_split_point(text: str, max_len: int) -> int:
    """Find a natural split point in text at or before *max_len*.

    Looks for (in order of preference):
    1. Last newline before *max_len*
    2. Last space before *max_len*
    3. Exactly at *max_len* (hard fallback)

    Will not split before ``max_len // 2`` to avoid producing tiny chunks.
    """
    if len(text) <= max_len:
        return len(text)

    min_pos = max_len // 2

    # Prefer splitting at a line boundary
    nl = text.rfind('\n', min_pos, max_len)
    if nl > 0:
        return nl + 1  # include the newline in the first chunk

    # Next best: split at a word boundary
    sp = text.rfind(' ', min_pos, max_len)
    if sp > 0:
        return sp + 1  # include the space in the first chunk

    # Hard fallback – split at the exact limit
    return max_len


def _estimate_block_size(block: dict[str, Any]) -> int:
    """Estimate the serialised JSON size of a Notion block dict (bytes)."""
    try:
        return len(json.dumps(block))
    except (TypeError, ValueError):
        return 1000  # conservative default


def _parse_inline_formatting(text: str) -> list[dict[str, Any]]:
    """
    Parse inline markdown formatting into Notion rich_text array.

    Supports:
    - **bold**
    - *italic* or _italic_
    - `code`
    - [links](url)
    - ~~strikethrough~~
    """
    if not text:
        return []

    rich_text: list[dict[str, Any]] = []

    # Pattern to match markdown inline formatting
    # Order matters: bold before italic (** before *)
    pattern = (
        r'(\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~'
        r'|\*(.+?)\*|_(.+?)_|`(.+?)`'
        r'|\[([^\]]+)\]\(([^)]+)\))'
    )

    last_end = 0
    for match in re.finditer(pattern, text):
        # Add plain text before this match
        if match.start() > last_end:
            plain = text[last_end:match.start()]
            if plain:
                rich_text.append({
                    "type": "text",
                    "text": {"content": plain}
                })

        full_match = match.group(0)

        # Determine formatting type
        if full_match.startswith('**') or full_match.startswith('__'):
            # Bold
            content = match.group(2) or match.group(3)
            rich_text.append({
                "type": "text",
                "text": {"content": content},
                "annotations": {"bold": True}
            })
        elif full_match.startswith('~~'):
            # Strikethrough
            content = match.group(4)
            rich_text.append({
                "type": "text",
                "text": {"content": content},
                "annotations": {"strikethrough": True}
            })
        elif full_match.startswith('*') or full_match.startswith('_'):
            # Italic (single * or _)
            content = match.group(5) or match.group(6)
            rich_text.append({
                "type": "text",
                "text": {"content": content},
                "annotations": {"italic": True}
            })
        elif full_match.startswith('`'):
            # Inline code
            content = match.group(7)
            rich_text.append({
                "type": "text",
                "text": {"content": content},
                "annotations": {"code": True}
            })
        elif full_match.startswith('['):
            # Link
            link_text = match.group(8)
            link_url = match.group(9)
            rich_text.append({
                "type": "text",
                "text": {"content": link_text, "link": {"url": link_url}}
            })

        last_end = match.end()

    # Add remaining plain text
    if last_end < len(text):
        remaining = text[last_end:]
        if remaining:
            rich_text.append({
                "type": "text",
                "text": {"content": remaining}
            })

    # If no formatting found, return plain text
    if not rich_text:
        return [{"type": "text", "text": {"content": text}}]

    return rich_text


def _make_rich_text_formatted(text: str) -> list[dict[str, Any]]:
    """Create rich text array with inline formatting support."""
    return _parse_inline_formatting(text)


def _parse_table(lines: list[str], start_idx: int) -> tuple[Block | None, int]:
    """
    Parse a markdown table starting at start_idx.

    Returns (table_block, next_line_idx) or (None, start_idx) if not a table.
    """
    if start_idx >= len(lines):
        return None, start_idx

    # Check if this looks like a table (has pipes)
    first_line = lines[start_idx].strip()
    if '|' not in first_line:
        return None, start_idx

    # Collect table lines
    table_lines: list[str] = []
    idx = start_idx

    while idx < len(lines):
        line = lines[idx].strip()
        if not line or '|' not in line:
            break
        table_lines.append(line)
        idx += 1

    if len(table_lines) < 2:
        return None, start_idx

    # Check for separator line (second line should be like |---|---|)
    separator = table_lines[1]
    if not re.match(r'^[\s|:-]+$', separator.replace('-', '')):
        # Not a valid table separator
        return None, start_idx

    # Parse cells from each row
    def parse_row(line: str) -> list[str]:
        # Remove leading/trailing pipes and split
        line = line.strip()
        if line.startswith('|'):
            line = line[1:]
        if line.endswith('|'):
            line = line[:-1]
        return [cell.strip() for cell in line.split('|')]

    # Get header and data rows (skip separator)
    header_cells = parse_row(table_lines[0])
    data_rows = [parse_row(line) for line in table_lines[2:]]

    table_width = len(header_cells)

    # Create table rows with rich text formatting
    rows: list[Block] = []

    # Header row
    header_rich_text = [[{"type": "text", "text": {"content": cell}, "annotations": {"bold": True}}]
                        for cell in header_cells]
    rows.append(Block(
        type=BlockType.TABLE_ROW,
        content={"cells": header_rich_text}
    ))

    # Data rows
    for row_cells in data_rows:
        # Pad or truncate to match table width
        while len(row_cells) < table_width:
            row_cells.append("")
        row_cells = row_cells[:table_width]

        cells_rich_text = [_parse_inline_formatting(cell) for cell in row_cells]
        rows.append(Block(
            type=BlockType.TABLE_ROW,
            content={"cells": cells_rich_text}
        ))

    # Create table block
    table_block = Block(
        type=BlockType.TABLE,
        content={
            "table_width": table_width,
            "has_column_header": True,
            "has_row_header": False,
        },
        children=rows
    )

    return table_block, idx


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """
    Convert a markdown string to a list of Notion blocks.

    Supports:
    - Headings (##, ###)
    - Paragraphs with inline formatting (**bold**, *italic*, `code`, [links](url))
    - Code blocks (``` with language)
    - Tables (pipe-delimited markdown tables)
    - Dividers (---)
    - Bulleted lists (- item)
    - Numbered lists (1. item)
    - Block quotes (> quote)

    Args:
        markdown: Markdown formatted string

    Returns:
        List of Notion block dictionaries ready for blocks.append()

    Example:
        blocks = markdown_to_blocks('''
        ## Section Title

        Some **bold** and *italic* text.

        | Header 1 | Header 2 |
        |----------|----------|
        | Cell 1   | Cell 2   |

        ```python
        print("hello")
        ```
        ''')
        notion.blocks.append(page_id, blocks)
    """
    lines = markdown.split('\n')
    blocks: list[dict[str, Any]] = []
    current_text: list[str] = []
    in_code_block = False
    code_content: list[str] = []
    code_lang = "plain text"
    idx = 0

    def flush_text() -> None:
        nonlocal current_text
        if current_text:
            text = '\n'.join(current_text).strip()
            if text:
                # Handle text length limits (Notion max ~2000 chars per block)
                while len(text) > _SAFE_CHAR_LIMIT:
                    split_at = _find_split_point(text, _SAFE_CHAR_LIMIT)
                    chunk = text[:split_at].rstrip()
                    if chunk:
                        rich_text = _parse_inline_formatting(chunk)
                        blocks.append(Block(
                            type=BlockType.PARAGRAPH,
                            content={"rich_text": rich_text}
                        ))
                    text = text[split_at:].lstrip()
                if text:
                    rich_text = _parse_inline_formatting(text)
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content={"rich_text": rich_text}
                    ))
            current_text = []

    def flush_code() -> None:
        nonlocal code_content, code_lang
        code_text = '\n'.join(code_content)
        if code_text.strip():
            # Split long code blocks at line boundaries
            if len(code_text) > _SAFE_CHAR_LIMIT:
                remaining = code_text
                while len(remaining) > _SAFE_CHAR_LIMIT:
                    split_at = _find_split_point(remaining, _SAFE_CHAR_LIMIT)
                    chunk = remaining[:split_at].rstrip('\n')
                    if chunk:
                        blocks.append(Blocks.code(chunk, language=code_lang))
                    remaining = remaining[split_at:]
                if remaining.strip():
                    blocks.append(Blocks.code(remaining, language=code_lang))
            else:
                blocks.append(Blocks.code(code_text, language=code_lang))
        code_content = []
        code_lang = "plain text"

    while idx < len(lines):
        line = lines[idx]

        # Code block handling
        if line.startswith('```'):
            if in_code_block:
                # End code block
                flush_text()
                flush_code()
                in_code_block = False
            else:
                # Start code block
                flush_text()
                lang = line[3:].strip()
                code_lang = _normalize_language(lang) if lang else "plain text"
                in_code_block = True
            idx += 1
            continue

        if in_code_block:
            code_content.append(line)
            idx += 1
            continue

        # Table detection (line contains | and next line is separator)
        if '|' in line and idx + 1 < len(lines):
            next_line = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
            if re.match(r'^[\s|:-]+$', next_line.replace('-', '')):
                flush_text()
                table_block, new_idx = _parse_table(lines, idx)
                if table_block:
                    blocks.append(table_block)
                    idx = new_idx
                    continue

        # Heading 1 (skip - usually title)
        if line.startswith('# ') and not line.startswith('## '):
            flush_text()
            # Skip h1 as it's typically the page title
            idx += 1
            continue

        # Heading 2
        if line.startswith('## '):
            flush_text()
            heading_text = line[3:].strip()
            rich_text = _parse_inline_formatting(heading_text)
            blocks.append(Block(
                type=BlockType.HEADING_2,
                content={"rich_text": rich_text, "is_toggleable": False}
            ))
            idx += 1
            continue

        # Heading 3
        if line.startswith('### '):
            flush_text()
            heading_text = line[4:].strip()
            rich_text = _parse_inline_formatting(heading_text)
            blocks.append(Block(
                type=BlockType.HEADING_3,
                content={"rich_text": rich_text, "is_toggleable": False}
            ))
            idx += 1
            continue

        # Divider
        if line.strip() in ('---', '***', '___'):
            flush_text()
            blocks.append(Blocks.divider())
            idx += 1
            continue

        # Bulleted list
        if re.match(r'^[-*+]\s+', line):
            flush_text()
            content = re.sub(r'^[-*+]\s+', '', line)
            rich_text = _parse_inline_formatting(content)
            blocks.append(Block(
                type=BlockType.BULLETED_LIST_ITEM,
                content={"rich_text": rich_text}
            ))
            idx += 1
            continue

        # Numbered list
        if re.match(r'^\d+\.\s+', line):
            flush_text()
            content = re.sub(r'^\d+\.\s+', '', line)
            rich_text = _parse_inline_formatting(content)
            blocks.append(Block(
                type=BlockType.NUMBERED_LIST_ITEM,
                content={"rich_text": rich_text}
            ))
            idx += 1
            continue

        # Block quote
        if line.startswith('> '):
            flush_text()
            quote_text = line[2:]
            rich_text = _parse_inline_formatting(quote_text)
            blocks.append(Block(
                type=BlockType.QUOTE,
                content={"rich_text": rich_text}
            ))
            idx += 1
            continue

        # Regular text - accumulate
        current_text.append(line)
        idx += 1

    # Flush remaining content
    flush_text()
    if in_code_block:
        flush_code()

    # Convert Block objects to API format
    return [b.to_api_format() if isinstance(b, Block) else b for b in blocks]


def _normalize_language(lang: str) -> str:
    """Normalize language identifier for Notion code blocks."""
    lang = lang.lower().strip()

    # Map common aliases to Notion-supported languages
    lang_map = {
        'py': 'python',
        'js': 'javascript',
        'ts': 'typescript',
        'sh': 'bash',
        'shell': 'bash',
        'yml': 'yaml',
        'md': 'markdown',
        'rb': 'ruby',
        'rs': 'rust',
        'go': 'go',
        'java': 'java',
        'c': 'c',
        'cpp': 'c++',
        'c++': 'c++',
        'cs': 'c#',
        'csharp': 'c#',
        'php': 'php',
        'sql': 'sql',
        'json': 'json',
        'xml': 'xml',
        'html': 'html',
        'css': 'css',
        'swift': 'swift',
        'kotlin': 'kotlin',
        'scala': 'scala',
        'r': 'r',
        'matlab': 'matlab',
        'dockerfile': 'docker',
        'makefile': 'makefile',
        'toml': 'toml',
        'ini': 'ini',
        'diff': 'diff',
        'graphql': 'graphql',
        'latex': 'latex',
        'tex': 'latex',
    }

    return lang_map.get(lang, lang if lang else "plain text")


def create_atom_page(
    notion_client: Any,
    title: str,
    content_markdown: str,
    *,
    atom_type: str = "Document",
    description: str | None = None,
    parent_id: str | None = None,
    topics: list[str] | None = None,
    project_id: str | None = None,
    status: str | None = None,
    meta: list[str] | None = None,
    extra_properties: dict[str, Any] | None = None,
    atoms_db_id: str = "REDACTED-PRIVATE-DB-ID",
) -> dict[str, Any]:
    """
    Create a new Atom page in Notion with markdown content.

    This is the standard way to create documented artifacts in AgenticOS.
    Uses the Notion Doc & Dev Skill patterns.

    Args:
        notion_client: NotionOps client instance
        title: Page title (Name property)
        content_markdown: Markdown content for the page body
        atom_type: Type property value (Document, Spec, Note, Skill, etc.)
        description: Optional 1-3 sentence description
        parent_id: Optional Parent relation (page that spawned this)
        topics: Optional list of topic page IDs for Topics relation
        project_id: Optional Project relation page ID
        status: Optional Status property value (e.g. "Draft", "Complete", "In Progress")
        meta: Optional Meta relation (list of page IDs for Meta/Mesa relationship)
        extra_properties: Optional dict of additional properties. Keys are property names,
            values should be property objects (e.g. SelectProperty(value="x")). These are
            merged into the properties dict and will not override explicitly set properties.
        atoms_db_id: Atoms database ID (defaults to AgenticOS Atoms)

    Returns:
        Dict with keys:
            - 'page_id': Notion page ID of the created page
            - 'id': Backward-compatible alias for 'page_id'
            - 'url': Notion URL of the created page
            - 'title': Title of the created page
            - 'content_error': None if content was appended successfully,
              or an error string if block append failed. The page still exists
              in Notion even when this is set — the caller should NOT retry
              page creation.

    Example:
        from notion_ops import NotionOps
        from notion_ops.utils.markdown import create_atom_page

        notion = NotionOps()
        result = create_atom_page(
            notion,
            title="My Design Spec",
            content_markdown=spec_md,
            atom_type="Spec",
            description="Design spec for feature X",
            parent_id="action-item-page-id",
            status="Draft",
            meta=["meta-page-id-1", "meta-page-id-2"],
        )
        print(result['url'])
    """
    from notion_ops.models.properties import (
        RelationProperty,
        RichTextProperty,
        SelectProperty,
        TitleProperty,
    )

    # Build properties
    # Start with extra_properties if provided (will be overridden by explicit properties)
    properties: dict[str, Any] = {}
    if extra_properties:
        properties.update(extra_properties)

    # Set explicit properties (these take precedence over extra_properties)
    properties['Name'] = TitleProperty(value=title)
    properties['Type'] = SelectProperty(value=atom_type)

    if description:
        properties['Description'] = RichTextProperty(value=description)

    if parent_id:
        properties['Parent'] = RelationProperty(value=[parent_id])

    if topics:
        properties['Topics'] = RelationProperty(value=topics)

    if project_id:
        properties['Project'] = RelationProperty(value=[project_id])

    if status:
        # Atoms DB defines Status as 'select', not 'status' type
        properties['Status'] = SelectProperty(value=status)

    if meta:
        properties['Meta'] = RelationProperty(value=meta)

    # Create page
    page = notion_client.pages.create(
        parent_id=atoms_db_id,
        properties=properties,
        parent_type="database"
    )

    # Page created successfully — build result immediately so callers always
    # get the page_id even if the subsequent block append fails (ISS-006).
    page_id_clean = page.id.replace('-', '')
    result: dict[str, Any] = {
        'page_id': page.id,
        'id': page.id,  # backward-compatible alias
        'url': f"https://notion.so/{page_id_clean}",
        'title': title,
        'content_error': None,
    }

    # Attempt to append content blocks — failure is non-fatal
    if content_markdown:
        try:
            # Convert markdown to blocks (returns dicts in API format)
            blocks = markdown_to_blocks(content_markdown)

            # Size-aware batching: respect both the block-count limit and
            # a conservative payload-size limit to avoid API rejections.
            current_batch: list[dict[str, Any]] = []
            current_size = 0

            for block in blocks:
                block_size = _estimate_block_size(block)

                if current_batch and (
                    len(current_batch) >= _MAX_BLOCKS_PER_REQUEST
                    or current_size + block_size > _MAX_PAYLOAD_BYTES
                ):
                    notion_client.api.blocks.children.append(
                        block_id=page.id,
                        children=current_batch,
                    )
                    current_batch = []
                    current_size = 0

                current_batch.append(block)
                current_size += block_size

            if current_batch:
                notion_client.api.blocks.children.append(
                    block_id=page.id,
                    children=current_batch,
                )
        except Exception as e:
            result['content_error'] = str(e)
            logger.warning(
                "Failed to append content blocks to page %s (%s): %s",
                page.id, title, e,
            )

    return result
