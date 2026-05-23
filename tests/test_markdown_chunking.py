"""Tests for markdown-to-blocks chunking: natural split points and size-aware batching."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from notion_ops.models.page import Page
from notion_ops.utils.markdown import (
    _MAX_BLOCKS_PER_REQUEST,
    _SAFE_CHAR_LIMIT,
    _estimate_block_size,
    _find_split_point,
    markdown_to_blocks,
)
from cli.atoms import create_atom_page

# ---------------------------------------------------------------------------
# _find_split_point
# ---------------------------------------------------------------------------

class TestFindSplitPoint:
    """Tests for _find_split_point helper."""

    def test_short_text_returns_full_length(self):
        assert _find_split_point("hello", 100) == 5

    def test_exact_limit_returns_full_length(self):
        text = "a" * 100
        assert _find_split_point(text, 100) == 100

    def test_splits_at_newline(self):
        # Newline at position 80 in a 120-char string with limit 100
        text = "a" * 80 + "\n" + "b" * 39
        result = _find_split_point(text, 100)
        assert result == 81  # right after the newline
        assert text[:result].endswith("\n")

    def test_splits_at_space_when_no_newline(self):
        # Space at position 85 in a 120-char string with limit 100
        text = "a" * 85 + " " + "b" * 34
        result = _find_split_point(text, 100)
        assert result == 86  # right after the space
        assert text[:result].endswith(" ")

    def test_prefers_newline_over_space(self):
        # Both newline (pos 70) and space (pos 90) before limit of 100
        text = "a" * 70 + "\n" + "b" * 19 + " " + "c" * 29
        result = _find_split_point(text, 100)
        # Should pick the newline at 70 since it's the last newline before limit
        assert result == 71

    def test_hard_fallback_at_limit(self):
        # No newlines or spaces in the allowed range
        text = "a" * 200
        result = _find_split_point(text, 100)
        assert result == 100

    def test_ignores_breaks_before_halfway(self):
        # Space at position 10 but nothing after halfway (50) in 200-char string
        text = "a" * 10 + " " + "b" * 189
        result = _find_split_point(text, 100)
        # Space at 10 is before min_pos (50), so hard fallback
        assert result == 100

    def test_zero_length_text(self):
        assert _find_split_point("", 100) == 0

    def test_multiple_newlines_picks_last_valid(self):
        # Newlines at 60, 75, 90 with limit 100
        text = "a" * 60 + "\n" + "b" * 14 + "\n" + "c" * 14 + "\n" + "d" * 110
        result = _find_split_point(text, 100)
        # Should pick the last newline before 100
        assert text[result - 1] == "\n"
        assert result <= 100


# ---------------------------------------------------------------------------
# _estimate_block_size
# ---------------------------------------------------------------------------

class TestEstimateBlockSize:
    """Tests for _estimate_block_size helper."""

    def test_simple_block(self):
        block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "hello"}}
                ]
            },
        }
        size = _estimate_block_size(block)
        assert size == len(json.dumps(block))

    def test_non_serializable_returns_default(self):
        # An object that json.dumps can't handle
        block = {"bad": object()}
        size = _estimate_block_size(block)
        assert size == 1000


# ---------------------------------------------------------------------------
# markdown_to_blocks – paragraph splitting
# ---------------------------------------------------------------------------

class TestParagraphSplitting:
    """Tests that long paragraphs split at natural boundaries."""

    def test_short_paragraph_no_split(self):
        text = "Short paragraph."
        blocks = markdown_to_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"

    def test_long_paragraph_splits_at_space(self):
        # Build a paragraph that exceeds the safe limit
        # Use words separated by spaces so there are natural break points
        words = ["word"] * 500  # ~2500 chars
        text = " ".join(words)
        blocks = markdown_to_blocks(text)

        assert len(blocks) >= 2
        for block in blocks:
            assert block["type"] == "paragraph"
            # Each block's text content should be <= _SAFE_CHAR_LIMIT
            content = "".join(
                rt["text"]["content"]
                for rt in block["paragraph"]["rich_text"]
            )
            assert len(content) <= _SAFE_CHAR_LIMIT

    def test_long_paragraph_doesnt_cut_mid_word(self):
        # Build text with clear word boundaries
        word = "abcdefghij"  # 10-char words
        words = [word] * 250  # ~2750 chars with spaces
        text = " ".join(words)
        blocks = markdown_to_blocks(text)

        for block in blocks:
            content = "".join(
                rt["text"]["content"]
                for rt in block["paragraph"]["rich_text"]
            )
            # Should not end with a partial word (except possibly trailing whitespace)
            stripped = content.strip()
            if stripped:
                # Each chunk should end at a word boundary
                assert stripped[-1] != "-"  # no mid-word breaks
                assert not stripped.endswith(word[:5])  # no half-words

    def test_paragraph_with_newlines_splits_at_newline(self):
        # Long text with embedded newlines
        lines = ["Line number " + str(i) + " with some padding text here." for i in range(100)]
        text = "\n".join(lines)
        blocks = markdown_to_blocks(text)

        assert len(blocks) >= 2
        for block in blocks:
            content = "".join(
                rt["text"]["content"]
                for rt in block["paragraph"]["rich_text"]
            )
            assert len(content) <= _SAFE_CHAR_LIMIT


# ---------------------------------------------------------------------------
# markdown_to_blocks – code block splitting
# ---------------------------------------------------------------------------

class TestCodeBlockSplitting:
    """Tests that long code blocks split at line boundaries."""

    def test_short_code_block_no_split(self):
        md = "```python\nprint('hello')\n```"
        blocks = markdown_to_blocks(md)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "code"

    def test_long_code_block_splits(self):
        # Build a code block with many lines exceeding the limit
        lines = [f"line_{i} = 'value_{i}'  # some comment to pad the line" for i in range(100)]
        code_content = "\n".join(lines)
        md = f"```python\n{code_content}\n```"
        blocks = markdown_to_blocks(md)

        assert len(blocks) >= 2
        for block in blocks:
            assert block["type"] == "code"
            content = "".join(
                rt["text"]["content"]
                for rt in block["code"]["rich_text"]
            )
            assert len(content) <= _SAFE_CHAR_LIMIT
            # All blocks should preserve the language
            assert block["code"]["language"] == "python"

    def test_code_block_splits_at_line_boundary(self):
        # Each line is 50 chars, so 50 lines = 2500 chars + newlines
        lines = [f"x = {'a' * 46}  #" for _ in range(50)]
        code_content = "\n".join(lines)
        md = f"```python\n{code_content}\n```"
        blocks = markdown_to_blocks(md)

        for block in blocks:
            content = "".join(
                rt["text"]["content"]
                for rt in block["code"]["rich_text"]
            )
            # Each chunk should end cleanly (not mid-line)
            # If there's content, the last non-whitespace char should be '#'
            stripped = content.strip()
            if stripped:
                assert stripped.endswith("#")


# ---------------------------------------------------------------------------
# create_atom_page – size-aware batching
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_notion_client():
    """A NotionOps-like mock with pages.create and blocks.children.append."""
    client = MagicMock()

    mock_page = MagicMock(spec=Page)
    mock_page.id = "test-page-id-1234"
    client.pages.create.return_value = mock_page

    # Wire _notion and api to the same mock (mirrors the property in NotionOps)
    sdk_mock = MagicMock()
    sdk_mock.blocks.children.append.return_value = {}
    client._notion = sdk_mock
    client.api = sdk_mock
    return client


class TestSizeAwareBatching:
    """Tests that create_atom_page batches by both count and payload size."""

    def test_small_content_single_batch(self, mock_notion_client):
        """Small content should result in exactly one append call."""
        md = "## Hello\n\nShort paragraph."
        result = create_atom_page(
            mock_notion_client,
            title="Small",
            content_markdown=md,
            atom_type="Note",
        )

        assert result['content_error'] is None
        assert mock_notion_client._notion.blocks.children.append.call_count == 1

    def test_many_blocks_batches_by_count(self, mock_notion_client):
        """More than 100 blocks should result in multiple append calls."""
        # Generate 150+ blocks (each bullet becomes a block)
        lines = [f"- Item {i}" for i in range(150)]
        md = "\n".join(lines)

        result = create_atom_page(
            mock_notion_client,
            title="Many blocks",
            content_markdown=md,
            atom_type="Note",
        )

        assert result['content_error'] is None
        # Should have at least 2 calls (150 blocks / 100 per batch)
        assert mock_notion_client._notion.blocks.children.append.call_count >= 2

        # Verify no single call has more than 100 blocks
        for call in mock_notion_client._notion.blocks.children.append.call_args_list:
            children = call[1].get("children", call[0][0] if call[0] else [])
            if isinstance(children, list):
                assert len(children) <= _MAX_BLOCKS_PER_REQUEST

    def test_large_blocks_batches_by_size(self, mock_notion_client):
        """Even with fewer than 100 blocks, large payloads should split."""
        # Create blocks that are individually large (close to 2000 chars each)
        # 20 blocks * ~1800 chars each ≈ 36KB which is under limit
        # But 200 blocks * ~1800 chars = ~360KB which should trigger size batching
        big_lines = []
        for i in range(200):
            big_lines.append(f"- {'x' * 1700} item{i}")
        md = "\n".join(big_lines)

        result = create_atom_page(
            mock_notion_client,
            title="Large payload",
            content_markdown=md,
            atom_type="Note",
        )

        assert result['content_error'] is None
        # Should have multiple calls due to payload size
        assert mock_notion_client._notion.blocks.children.append.call_count >= 2
