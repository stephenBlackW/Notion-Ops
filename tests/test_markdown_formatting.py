"""Tests for inline emphasis and table rendering in markdown_to_blocks.

Covers the ISS-005 / ISS-010 fixes: underscores inside identifiers must not
become italics, stray asterisks must stay literal, bold must survive the
2000-char split boundary, and table cells/headers must parse inline markup.
"""

from __future__ import annotations

from notion_ops.utils.markdown import (
    _SAFE_CHAR_LIMIT,
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
