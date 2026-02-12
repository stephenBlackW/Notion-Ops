"""Markdown to Notion block conversion utilities."""

from __future__ import annotations

import re
from typing import Any

from notion_ops.models.block import Blocks, Block, BlockType


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
    pattern = r'(\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~|\*(.+?)\*|_(.+?)_|`(.+?)`|\[([^\]]+)\]\(([^)]+)\))'

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
                while len(text) > 1900:
                    # Create paragraph with inline formatting
                    rich_text = _parse_inline_formatting(text[:1900])
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content={"rich_text": rich_text}
                    ))
                    text = text[1900:]
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
        Dict with 'page_id' and 'url' of created page

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
        TitleProperty,
        SelectProperty,
        RichTextProperty,
        RelationProperty,
        StatusProperty,
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
        properties['Status'] = StatusProperty(value=status)

    if meta:
        properties['Meta'] = RelationProperty(value=meta)

    # Create page
    page = notion_client.pages.create(
        parent_id=atoms_db_id,
        properties=properties,
        parent_type="database"
    )

    # Convert markdown to blocks (returns dicts in API format)
    blocks = markdown_to_blocks(content_markdown)

    # Append in batches (Notion limit ~100 blocks per request)
    # Use raw API since markdown_to_blocks returns dicts, not Block objects
    batch_size = 100
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i + batch_size]
        notion_client._notion.blocks.children.append(
            block_id=page.id,
            children=batch
        )

    page_id_clean = page.id.replace('-', '')
    return {
        'page_id': page.id,
        'url': f"https://notion.so/{page_id_clean}",
    }
