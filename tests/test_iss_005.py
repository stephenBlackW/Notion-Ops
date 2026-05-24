"""Reproducing test for ISS-005 — markdown_to_blocks() drops h4+ headings.

Before the fix, `#### Heading` falls through to a literal-text paragraph
(content "#### Heading"). Notion supports only h1-h3, so h4-h6 must map to a
heading_3 block with the markers stripped.
"""

from __future__ import annotations

from notion_ops.utils.markdown import markdown_to_blocks


def test_iss_005_h4_heading_becomes_heading_3():
    blocks = markdown_to_blocks("#### Sub-sub heading")
    assert len(blocks) == 1
    b = blocks[0]
    assert b["type"] == "heading_3", f"expected heading_3, got {b['type']}"
    assert b["heading_3"]["rich_text"][0]["text"]["content"] == "Sub-sub heading"


def test_iss_005_h5_and_h6_also_map_to_heading_3():
    for md, expected in (("##### Five", "Five"), ("###### Six", "Six")):
        b = markdown_to_blocks(md)[0]
        assert b["type"] == "heading_3"
        assert b["heading_3"]["rich_text"][0]["text"]["content"] == expected


def test_iss_005_h4_inline_formatting_preserved():
    # A bold span inside an h4 should still parse (no literal '####', no '**').
    b = markdown_to_blocks("#### A **bold** word")[0]
    assert b["type"] == "heading_3"
    contents = "".join(rt["text"]["content"] for rt in b["heading_3"]["rich_text"])
    assert contents == "A bold word"
