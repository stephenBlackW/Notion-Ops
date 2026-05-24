"""Tests for inline emphasis and table rendering in markdown_to_blocks.

Covers the ISS-005 / ISS-013 fixes: underscores inside identifiers must not
become italics, stray asterisks must stay literal, bold must survive the
2000-char split boundary, and table cells/headers must parse inline markup.
"""

from __future__ import annotations

import pytest

from notion_ops.exceptions import OversizedContentError
from notion_ops.utils.markdown import (
    _SAFE_CHAR_LIMIT,
    _normalize_language,
    _parse_inline_formatting,
    markdown_to_blocks,
)


def _flags(seg: dict) -> set[str]:
    return {k for k, v in seg.get("annotations", {}).items() if v}


def _texts(segments: list[dict]) -> str:
    return "".join(s["text"]["content"] for s in segments)


# ---------------------------------------------------------------------------
# Inline emphasis
# ---------------------------------------------------------------------------

class TestInlineEmphasis:
    def test_bold_basic(self):
        segs = _parse_inline_formatting("a **b** c")
        assert [(_texts([s]), _flags(s)) for s in segs] == [
            ("a ", set()),
            ("b", {"bold"}),
            (" c", set()),
        ]

    def test_multiple_bold_runs(self):
        segs = _parse_inline_formatting("**one** and **two**")
        bolds = [s["text"]["content"] for s in segs if "bold" in _flags(s)]
        assert bolds == ["one", "two"]

    def test_intraword_single_underscore_is_literal(self):
        # The core bug: snake_case identifiers must not become italics.
        segs = _parse_inline_formatting("snake_case_name in text")
        assert len(segs) == 1
        assert _flags(segs[0]) == set()
        assert segs[0]["text"]["content"] == "snake_case_name in text"

    def test_dotted_config_id_is_literal(self):
        text = "config:gas_turbine.capex_per_kw"
        segs = _parse_inline_formatting(text)
        assert len(segs) == 1
        assert _texts(segs) == text
        assert _flags(segs[0]) == set()

    def test_stray_asterisks_with_spaces_stay_literal(self):
        segs = _parse_inline_formatting("a * b and c * d then **real bold**")
        assert _texts(segs) == "a * b and c * d then real bold"
        assert [s["text"]["content"] for s in segs if "italic" in _flags(s)] == []
        assert [s["text"]["content"] for s in segs if "bold" in _flags(s)] == ["real bold"]

    def test_word_flanked_italic_still_works(self):
        segs = _parse_inline_formatting("_real italic_ here")
        assert segs[0]["text"]["content"] == "real italic"
        assert _flags(segs[0]) == {"italic"}

    def test_padded_bold_not_emphasized(self):
        # "** x **" has whitespace adjacent to the delimiters -> literal.
        segs = _parse_inline_formatting("a ** x ** b")
        assert "bold" not in {f for s in segs for f in _flags(s)}

    def test_code_and_link(self):
        segs = _parse_inline_formatting("see `func()` at [docs](http://x)")
        code = [s for s in segs if "code" in _flags(s)]
        assert code and code[0]["text"]["content"] == "func()"
        link = [s for s in segs if s["text"].get("link")]
        assert link and link[0]["text"]["link"]["url"] == "http://x"
        assert link[0]["text"]["content"] == "docs"

    def test_never_emits_empty_or_none_content(self):
        for sample in [
            "snake_case",
            "**bold** _italic_ `code` __dunder__ ~~strike~~",
            "a*b*c",
            "trailing _",
            "",
        ]:
            for seg in _parse_inline_formatting(sample):
                assert seg["text"]["content"]  # non-empty, non-None


# ---------------------------------------------------------------------------
# Bold survives the block-size split
# ---------------------------------------------------------------------------

class TestBoldSurvivesSplit:
    def test_bold_not_broken_at_split_boundary(self):
        # A bold span positioned right around the safe limit must not be cut
        # in half (which used to leave literal ** markers).
        filler = "word " * 500  # ~2500 chars, forces a split
        md = filler + "**important phrase** " + filler
        blocks = markdown_to_blocks(md)
        assert len(blocks) >= 2
        bold_runs = [
            rt["text"]["content"]
            for b in blocks
            for rt in b["paragraph"]["rich_text"]
            if rt.get("annotations", {}).get("bold")
        ]
        assert "important phrase" in bold_runs
        for b in blocks:
            content = "".join(rt["text"]["content"] for rt in b["paragraph"]["rich_text"])
            assert len(content) <= _SAFE_CHAR_LIMIT
            assert "**" not in content  # no orphaned markers


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class TestTables:
    def _rows(self, md: str):
        table = markdown_to_blocks(md)[0]
        assert table["type"] == "table"
        return table["table"]

    def test_header_parses_markup_and_is_bold(self):
        md = (
            "| **Fact id** | Value |\n"
            "|-------------|-------|\n"
            "| x | y |\n"
        )
        rows = self._rows(md)["children"]
        header = rows[0]["table_row"]["cells"]
        # Marker consumed (no literal **), bold applied.
        assert header[0][0]["text"]["content"] == "Fact id"
        assert header[0][0]["annotations"]["bold"] is True

    def test_cell_underscores_not_italicized(self):
        md = (
            "| Fact | Value |\n"
            "|------|-------|\n"
            "| idem_air_permit p_pass | 0.55 |\n"
        )
        rows = self._rows(md)["children"]
        cell = rows[1]["table_row"]["cells"][0]
        assert _texts(cell) == "idem_air_permit p_pass"
        assert all("italic" not in _flags(s) for s in cell)

    def test_cell_bold_markup_renders(self):
        md = (
            "| Fact | Value |\n"
            "|------|-------|\n"
            "| **GO** | yes |\n"
        )
        rows = self._rows(md)["children"]
        cell = rows[1]["table_row"]["cells"][0]
        assert cell[0]["text"]["content"] == "GO"
        assert cell[0]["annotations"]["bold"] is True

    def test_table_width_and_padding(self):
        md = (
            "| A | B | C |\n"
            "|---|---|---|\n"
            "| 1 | 2 |\n"  # short row padded
        )
        table = self._rows(md)
        assert table["table_width"] == 3
        short_row = table["children"][1]["table_row"]["cells"]
        assert len(short_row) == 3
        assert short_row[2] == []  # padded empty cell


# ---------------------------------------------------------------------------
# Headings beyond level 3
# ---------------------------------------------------------------------------

class TestDeepHeadings:
    def test_h4_demoted_to_h3(self):
        blocks = markdown_to_blocks("#### Deep section")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "heading_3"
        assert blocks[0]["heading_3"]["rich_text"][0]["text"]["content"] == "Deep section"

    def test_h5_demoted_to_h3(self):
        blocks = markdown_to_blocks("##### Deeper")
        assert blocks[0]["type"] == "heading_3"


# ---------------------------------------------------------------------------
# To-do items
# ---------------------------------------------------------------------------

class TestTodoItems:
    def test_unchecked_and_checked(self):
        blocks = markdown_to_blocks("- [ ] open\n- [x] done\n- [X] also done")
        assert [b["type"] for b in blocks] == ["to_do", "to_do", "to_do"]
        assert [b["to_do"]["checked"] for b in blocks] == [False, True, True]

    def test_plain_bullet_is_not_todo(self):
        blocks = markdown_to_blocks("- normal bullet")
        assert blocks[0]["type"] == "bulleted_list_item"

    def test_todo_keeps_inline_formatting(self):
        blocks = markdown_to_blocks("- [ ] validate **gas_turbine** prior")
        rt = blocks[0]["to_do"]["rich_text"]
        bold = [s["text"]["content"] for s in rt if s.get("annotations", {}).get("bold")]
        assert bold == ["gas_turbine"]


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

class TestImages:
    def test_external_image_block(self):
        blocks = markdown_to_blocks("![a chart](https://example.com/c.png)")
        assert len(blocks) == 1
        img = blocks[0]
        assert img["type"] == "image"
        assert img["image"]["type"] == "external"
        assert img["image"]["external"]["url"] == "https://example.com/c.png"
        assert img["image"]["caption"][0]["text"]["content"] == "a chart"

    def test_image_without_alt_has_no_caption(self):
        blocks = markdown_to_blocks("![](https://example.com/c.png)")
        assert blocks[0]["type"] == "image"
        assert "caption" not in blocks[0]["image"]

    def test_local_image_falls_through_to_text(self):
        # Cannot embed local files by reference; keep visible rather than drop.
        blocks = markdown_to_blocks("![local](./figure.png)")
        assert blocks[0]["type"] == "paragraph"
        assert "figure.png" in blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]


# ---------------------------------------------------------------------------
# Collapsible <details> -> toggle
# ---------------------------------------------------------------------------

class TestDetailsToggle:
    def test_summary_becomes_label_and_body_becomes_children(self):
        md = (
            "<details>\n"
            "<summary>Config lens</summary>\n\n"
            "Some body text.\n\n"
            "| Fact | Value |\n"
            "|------|-------|\n"
            "| capex_per_kw | 1100 |\n\n"
            "</details>"
        )
        blocks = markdown_to_blocks(md)
        assert len(blocks) == 1
        toggle = blocks[0]
        assert toggle["type"] == "toggle"
        assert toggle["toggle"]["rich_text"][0]["text"]["content"] == "Config lens"
        child_types = [c["type"] for c in toggle["toggle"]["children"]]
        assert child_types == ["paragraph", "table"]

    def test_summary_parses_inline_markup(self):
        md = "<details>\n<summary>**Bold** label</summary>\n\nbody\n\n</details>"
        rt = markdown_to_blocks(md)[0]["toggle"]["rich_text"]
        assert rt[0]["text"]["content"] == "Bold"
        assert rt[0]["annotations"]["bold"] is True

    def test_missing_summary_uses_default_label(self):
        md = "<details>\n\njust body\n\n</details>"
        toggle = markdown_to_blocks(md)[0]
        assert toggle["toggle"]["rich_text"][0]["text"]["content"] == "Details"

    def test_nested_details_nests_toggles(self):
        md = (
            "<details>\n<summary>Outer</summary>\n\n"
            "<details>\n<summary>Inner</summary>\n\nleaf\n\n</details>\n\n"
            "</details>"
        )
        outer = markdown_to_blocks(md)[0]
        assert outer["toggle"]["rich_text"][0]["text"]["content"] == "Outer"
        inner = outer["toggle"]["children"][0]
        assert inner["type"] == "toggle"
        assert inner["toggle"]["rich_text"][0]["text"]["content"] == "Inner"
        assert inner["toggle"]["children"][0]["type"] == "paragraph"

    def test_unterminated_details_degrades_to_text(self):
        blocks = markdown_to_blocks("<details>\n<summary>x</summary>\nno close")
        assert all(b["type"] != "toggle" for b in blocks)


# ---------------------------------------------------------------------------
# Code-fence language normalization
# ---------------------------------------------------------------------------

class TestCodeLanguage:
    def test_unsupported_language_falls_back(self):
        assert _normalize_language("csv") == "plain text"
        assert _normalize_language("tsv") == "plain text"
        assert _normalize_language("ini") == "plain text"

    def test_aliases_map_to_enum(self):
        assert _normalize_language("py") == "python"
        assert _normalize_language("ts") == "typescript"
        assert _normalize_language("sh") == "bash"

    def test_supported_language_preserved(self):
        for lang in ("python", "yaml", "toml", "mermaid", "rust"):
            assert _normalize_language(lang) == lang

    def test_empty_is_plain_text(self):
        assert _normalize_language("") == "plain text"

    def test_csv_fence_block_uses_plain_text(self):
        blocks = markdown_to_blocks("```csv\na,b,c\n1,2,3\n```")
        assert blocks[0]["type"] == "code"
        assert blocks[0]["code"]["language"] == "plain text"


# ---------------------------------------------------------------------------
# Oversized / unsplittable text escalation
# ---------------------------------------------------------------------------

class TestOversizedText:
    def test_spaced_long_paragraph_splits_without_escalating(self):
        # Long but full of spaces -> splits cleanly at word boundaries.
        blocks = markdown_to_blocks("word " * 600)
        assert len(blocks) >= 2
        assert all(b["type"] == "paragraph" for b in blocks)

    def test_unsplittable_blob_escalates(self):
        with pytest.raises(OversizedContentError) as exc:
            markdown_to_blocks("x" * 2500)
        assert exc.value.run_length == 2500
        assert exc.value.limit == _SAFE_CHAR_LIMIT

    def test_long_token_inside_paragraph_escalates(self):
        text = "see this " + ("a" * 2100) + " token"
        with pytest.raises(OversizedContentError):
            markdown_to_blocks(text)

    def test_run_at_limit_does_not_escalate(self):
        # Exactly the safe limit is fine; only longer runs escalate.
        blocks = markdown_to_blocks("a" * _SAFE_CHAR_LIMIT)
        assert blocks[0]["type"] == "paragraph"

    def test_long_code_line_does_not_escalate(self):
        # Code blocks legitimately contain long lines (minified payloads);
        # escalation applies to prose paragraphs only.
        md = "```js\n" + ("a=" * 1500) + "\n```"
        blocks = markdown_to_blocks(md)
        assert blocks[0]["type"] == "code"
