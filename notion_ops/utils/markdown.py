"""Markdown to Notion block conversion utilities."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from notion_ops.exceptions import OversizedContentError
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


def _check_text_splittable(text: str) -> None:
    """Escalate when *text* has a whitespace-free run too long to split.

    Normal prose splits cleanly at newlines or spaces. A single run longer
    than the safe block limit has no natural break point, so placing it in a
    Notion block would cut mid-token — a red flag for malformed input (pasted
    blobs, minified payloads). Raise rather than silently fragment it.
    """
    longest = 0
    run = 0
    for ch in text:
        if ch.isspace():
            run = 0
        else:
            run += 1
            if run > longest:
                longest = run
    if longest > _SAFE_CHAR_LIMIT:
        preview = max(re.split(r'\s+', text), key=len)[:80]
        logger.error(
            "Oversized unsplittable text run (%d chars) in markdown content; "
            "escalating instead of hard-splitting.",
            longest,
        )
        raise OversizedContentError(longest, _SAFE_CHAR_LIMIT, preview)


def _estimate_block_size(block: dict[str, Any]) -> int:
    """Estimate the serialised JSON size of a Notion block dict (bytes)."""
    try:
        return len(json.dumps(block))
    except (TypeError, ValueError):
        return 1000  # conservative default


# Inline markdown -> Notion rich_text.
# Emphasis rules follow CommonMark/GitHub closely enough for real docs:
#   - bold/italic content may not begin or end with whitespace, so stray
#     delimiters ("a * b") and padded ones ("** x **") stay literal;
#   - underscore emphasis is not intraword, so identifiers like
#     ``snake_case_name`` and ``capex_per_kw`` are never italicised.
_INLINE_PATTERN = re.compile(
    r'\*\*(?P<bold_a>\S(?:.*?\S)?)\*\*'                # **bold**
    r'|(?<!\w)__(?P<bold_u>\S(?:.*?\S)?)__(?!\w)'      # __bold__
    r'|~~(?P<strike>\S(?:.*?\S)?)~~'                   # ~~strikethrough~~
    r'|\*(?!\*)(?P<ital_a>\S(?:.*?\S)?)\*(?!\*)'       # *italic*
    r'|(?<!\w)_(?P<ital_u>\S(?:.*?\S)?)_(?!\w)'        # _italic_
    r'|`(?P<code>[^`]+?)`'                             # `code`
    r'|\[(?P<link_t>[^\]]+)\]\((?P<link_u>[^)]+)\)'    # [text](url)
)


def _text_seg(content: str, **flags: bool) -> dict[str, Any]:
    """A rich_text text segment; only true annotation flags are emitted."""
    seg: dict[str, Any] = {"type": "text", "text": {"content": content}}
    annotations = {name: True for name, on in flags.items() if on}
    if annotations:
        seg["annotations"] = annotations
    return seg


def _link_seg(label: str, url: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": label, "link": {"url": url}}}


def _parse_inline_formatting(text: str) -> list[dict[str, Any]]:
    """
    Parse inline markdown formatting into a Notion rich_text array.

    Supports **bold**, __bold__, *italic*, _italic_, ~~strikethrough~~,
    `code`, and [links](url). Plain runs between matches are preserved.
    Never emits a segment with empty or ``None`` content.
    """
    if not text:
        return []

    rich_text: list[dict[str, Any]] = []
    last_end = 0
    for match in _INLINE_PATTERN.finditer(text):
        if match.start() > last_end:
            rich_text.append(_text_seg(text[last_end:match.start()]))

        if match.group('bold_a') is not None:
            rich_text.append(_text_seg(match.group('bold_a'), bold=True))
        elif match.group('bold_u') is not None:
            rich_text.append(_text_seg(match.group('bold_u'), bold=True))
        elif match.group('strike') is not None:
            rich_text.append(_text_seg(match.group('strike'), strikethrough=True))
        elif match.group('ital_a') is not None:
            rich_text.append(_text_seg(match.group('ital_a'), italic=True))
        elif match.group('ital_u') is not None:
            rich_text.append(_text_seg(match.group('ital_u'), italic=True))
        elif match.group('code') is not None:
            rich_text.append(_text_seg(match.group('code'), code=True))
        elif match.group('link_t') is not None:
            rich_text.append(_link_seg(match.group('link_t'), match.group('link_u')))

        last_end = match.end()

    if last_end < len(text):
        rich_text.append(_text_seg(text[last_end:]))

    if not rich_text:
        return [_text_seg(text)]

    return rich_text


def _clone_seg(seg: dict[str, Any], content: str) -> dict[str, Any]:
    """Copy a rich_text segment with new *content*, preserving formatting."""
    new: dict[str, Any] = {"type": "text", "text": {"content": content}}
    link = seg["text"].get("link")
    if link is not None:
        new["text"]["link"] = link
    if "annotations" in seg:
        new["annotations"] = dict(seg["annotations"])
    return new


def _chunk_rich_text(
    rich_text: list[dict[str, Any]], limit: int
) -> list[list[dict[str, Any]]]:
    """Pack rich_text segments into blocks each <= *limit* characters.

    Splits happen between segments, or within an over-long segment at a
    natural boundary — never inside a parsed span — so emphasis added by
    :func:`_parse_inline_formatting` survives the Notion ~2000-char limit.
    """
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_len = 0

    for seg in rich_text:
        content = seg["text"]["content"]
        while len(content) > limit:
            split_at = _find_split_point(content, limit)
            head = content[:split_at].rstrip()
            if head:
                if current:
                    result.append(current)
                    current, current_len = [], 0
                result.append([_clone_seg(seg, head)])
            content = content[split_at:].lstrip()
        if not content:
            continue
        if current and current_len + len(content) > limit:
            result.append(current)
            current, current_len = [], 0
        current.append(_clone_seg(seg, content))
        current_len += len(content)

    if current:
        result.append(current)
    return result


def _force_bold(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return *segments* with bold forced on (used for table headers)."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        new = _clone_seg(seg, seg["text"]["content"])
        annotations = dict(new.get("annotations", {}))
        annotations["bold"] = True
        new["annotations"] = annotations
        out.append(new)
    return out


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

    # Header row (parse inline formatting, then force bold)
    header_rich_text = [_force_bold(_parse_inline_formatting(cell)) for cell in header_cells]
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


# Standalone image line:  ![alt](url)   with an optional "title" suffix.
_IMAGE_LINE_RE = re.compile(
    r'^\s*!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+"[^"]*")?\)\s*$'
)

# HTML <details>/<summary> collapsibles -> Notion toggle blocks.
_DETAILS_OPEN_RE = re.compile(r'^\s*<details\b', re.IGNORECASE)
_DETAILS_CLOSE_RE = re.compile(r'^\s*</details>\s*$', re.IGNORECASE)
_SUMMARY_RE = re.compile(r'<summary>(.*?)</summary>', re.IGNORECASE | re.DOTALL)


def _parse_details_block(
    lines: list[str], start_idx: int
) -> tuple[dict[str, Any] | None, int]:
    """Parse an HTML ``<details>...</details>`` block into a Notion toggle.

    The ``<summary>`` becomes the toggle label; the inner markdown becomes the
    toggle's children (converted recursively, so nested ``<details>`` nest as
    nested toggles). Returns ``(toggle_dict, next_idx)`` or ``(None, start_idx)``
    if there is no matching close tag.

    Note: Notion's ``blocks.children.append`` accepts at most two levels of
    nesting per request, so toggles nested more than one deep may need a
    follow-up append by the caller.
    """
    depth = 0
    idx = start_idx
    inner: list[str] = []

    while idx < len(lines):
        line = lines[idx]
        if _DETAILS_OPEN_RE.match(line):
            depth += 1
            if depth == 1:
                # Keep anything after the opening tag (e.g. an inline <summary>).
                remainder = re.sub(
                    r'^\s*<details\b[^>]*>', '', line, count=1, flags=re.IGNORECASE
                )
                if remainder.strip():
                    inner.append(remainder)
            else:
                inner.append(line)
            idx += 1
            continue
        if _DETAILS_CLOSE_RE.match(line):
            depth -= 1
            idx += 1
            if depth == 0:
                break
            inner.append(line)
            continue
        inner.append(line)
        idx += 1
    else:
        return None, start_idx  # unterminated <details>

    inner_text = '\n'.join(inner)
    summary_match = _SUMMARY_RE.search(inner_text)
    if summary_match:
        label = summary_match.group(1).strip()
        inner_text = inner_text[:summary_match.start()] + inner_text[summary_match.end():]
    else:
        label = "Details"

    summary_rich = _parse_inline_formatting(label) or [_text_seg("Details")]
    toggle: dict[str, Any] = {
        "object": "block",
        "type": "toggle",
        "toggle": {"rich_text": summary_rich},
    }
    children = markdown_to_blocks(inner_text)
    if children:
        toggle["toggle"]["children"] = children
    return toggle, idx


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """
    Convert a markdown string to a list of Notion blocks.

    Supports:
    - Headings (##, ###; #### and deeper are demoted to heading_3)
    - Paragraphs with inline formatting (**bold**, *italic*, `code`,
      [links](url), ~~strike~~)
    - Code blocks (``` with language; unsupported languages fall back to
      "plain text")
    - Tables (pipe-delimited markdown tables, with inline markup in cells)
    - Dividers (---)
    - Bulleted lists (- item) and to-do items (- [ ] / - [x])
    - Numbered lists (1. item)
    - Block quotes (> quote)
    - Images (![alt](https://...)) -> external image block
    - Collapsibles (<details><summary>..</summary>..</details>) -> toggle block

    Local image files cannot be embedded by reference. Upload them first with
    ``client.file_uploads.upload_and_attach(path)`` (see
    ``notion_ops.operations.file_uploads``) and append the returned block, or
    pass an https URL in the markdown.

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
                # Red flag: a text run with no newline/space can't be split at a
                # natural boundary — escalate instead of cutting mid-token.
                _check_text_splittable(text)
                # Parse inline formatting first, then split at the Notion
                # ~2000-char block limit between spans (never mid-span).
                rich_text = _parse_inline_formatting(text)
                for chunk in _chunk_rich_text(rich_text, _SAFE_CHAR_LIMIT):
                    blocks.append(Block(
                        type=BlockType.PARAGRAPH,
                        content={"rich_text": chunk}
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

        # Collapsible <details> -> toggle block (with recursive children)
        if _DETAILS_OPEN_RE.match(line):
            flush_text()
            toggle_block, new_idx = _parse_details_block(lines, idx)
            if toggle_block:
                blocks.append(toggle_block)
                idx = new_idx
                continue

        # Standalone image: ![alt](url)
        image_match = _IMAGE_LINE_RE.match(line)
        if image_match:
            url = image_match.group('url')
            alt = image_match.group('alt').strip()
            flush_text()
            if url.startswith(('http://', 'https://')):
                blocks.append(Blocks.image(url, caption=alt or None))
            else:
                # Local path / data URI: cannot embed by reference. Emit the raw
                # markdown verbatim so it stays visible and the caller knows to
                # pre-upload via client.file_uploads.upload_and_attach.
                blocks.append(Block(
                    type=BlockType.PARAGRAPH,
                    content={"rich_text": [_text_seg(line.strip())]},
                ))
            idx += 1
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

        # Heading 4+ (Notion supports only three heading levels) -> heading_3
        if re.match(r'^#{4,}\s', line):
            flush_text()
            heading_text = re.sub(r'^#{4,}\s+', '', line).strip()
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

        # To-do item: - [ ] / - [x]  (check before generic bulleted list)
        todo_match = re.match(r'^[-*+]\s+\[([ xX])\]\s+(.*)$', line)
        if todo_match:
            flush_text()
            checked = todo_match.group(1).lower() == 'x'
            rich_text = _parse_inline_formatting(todo_match.group(2))
            blocks.append(Block(
                type=BlockType.TO_DO,
                content={"rich_text": rich_text, "checked": checked}
            ))
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


# Languages accepted by Notion's code-block `language` enum. Anything outside
# this set (e.g. csv, tsv, ini) is rejected with a 400, so it falls back to
# "plain text" (ISS-008).
_NOTION_CODE_LANGUAGES: frozenset[str] = frozenset({
    "abap", "agda", "arduino", "ascii art", "assembly", "bash", "basic", "bnf",
    "c", "c#", "c++", "clojure", "coffeescript", "coq", "css", "dart", "dhall",
    "diff", "docker", "ebnf", "elixir", "elm", "erlang", "f#", "flow",
    "fortran", "gherkin", "glsl", "go", "graphql", "groovy", "haskell", "hcl",
    "html", "idris", "java", "javascript", "json", "julia", "kotlin", "latex",
    "less", "lisp", "livescript", "llvm ir", "lua", "makefile", "markdown",
    "markup", "mathematica", "matlab", "mermaid", "nix", "notion formula",
    "objective-c", "ocaml", "pascal", "perl", "php", "plain text", "powershell",
    "prolog", "protobuf", "purescript", "python", "r", "racket", "reason",
    "ruby", "rust", "sass", "scala", "scheme", "scss", "shell", "smalltalk",
    "solidity", "sql", "swift", "toml", "typescript", "vb.net", "verilog",
    "vhdl", "visual basic", "webassembly", "xml", "yaml", "java/c/c++/c#",
})

# Aliases for languages whose fenced-block name differs from the Notion enum.
_LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "jsx": "javascript",
    "tsx": "typescript",
    "sh": "bash",
    "zsh": "bash",
    "console": "shell",
    "yml": "yaml",
    "md": "markdown",
    "rb": "ruby",
    "rs": "rust",
    "cpp": "c++",
    "cs": "c#",
    "csharp": "c#",
    "objc": "objective-c",
    "dockerfile": "docker",
    "tex": "latex",
    "text": "plain text",
    "txt": "plain text",
    "plaintext": "plain text",
    # Common-but-unsupported tabular/data fences -> closest valid language.
    "csv": "plain text",
    "tsv": "plain text",
    "ini": "plain text",
    "cfg": "plain text",
    "conf": "plain text",
    "log": "plain text",
}


def _normalize_language(lang: str) -> str:
    """Normalize a fenced-block language to one Notion's API accepts.

    Applies known aliases, then falls back to ``"plain text"`` for anything
    outside Notion's supported language enum so appends never 400 (ISS-008).
    """
    lang = lang.lower().strip()
    if not lang:
        return "plain text"
    normalized = _LANGUAGE_ALIASES.get(lang, lang)
    return normalized if normalized in _NOTION_CODE_LANGUAGES else "plain text"


