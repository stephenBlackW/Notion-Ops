"""Phase A (ao-cycle-3): block->markdown extraction + non-destructive repair.

Covers `notion_ops.utils.repair.blocks_to_markdown` / `repair_blocks`:
- clean blocks round-trip unchanged (T-01);
- a paragraph carrying layered markdown (a list) expands into proper blocks,
  and an over-long paragraph splits (T-02);
- the API-response rich-text shape (plain_text/annotations) is read correctly.
"""

from __future__ import annotations

import textwrap

from notion_ops.utils.markdown import markdown_to_blocks
from notion_ops.utils.repair import blocks_to_markdown, repair_blocks


def _plain(block: dict) -> str:
    btype = block["type"]
    content = block.get(btype, {})
    if btype == "table":
        cells: list[str] = []
        for row in content.get("children", []):
            for cell in row["table_row"]["cells"]:
                cells.append("".join(s.get("text", {}).get("content", "") for s in cell))
        return "|".join(cells)
    return "".join(s.get("text", {}).get("content", "") for s in content.get("rich_text", []))


def _types(blocks: list[dict]) -> list[str]:
    return [b["type"] for b in blocks]


def _plains(blocks: list[dict]) -> list[str]:
    return [_plain(b) for b in blocks]


# --------------------------------------------------------------------------- #
# T-01 — clean blocks round-trip
# --------------------------------------------------------------------------- #

CLEAN_DOC = textwrap.dedent(
    """\
    ## Section heading

    A paragraph with **bold**, *italic*, `code` and a [link](https://x.test).

    ### Sub heading

    - first bullet
    - second bullet

    1. step one
    2. step two

    - [ ] open task
    - [x] done task

    > a quoted line

    ---

    ```python
    print("hi")
    ```

    | Name | Value |
    |------|-------|
    | a    | 1     |
    | b    | 2     |
    """
)


def test_blocks_to_markdown_roundtrips_clean_blocks() -> None:
    clean = markdown_to_blocks(CLEAN_DOC)
    repaired = repair_blocks(clean)

    # Re-converting clean blocks neither drops nor reshapes them.
    assert _types(repaired) == _types(clean)
    assert _plains(repaired) == _plains(clean)

    # Every enumerated block type survived the round-trip.
    for expected in (
        "heading_2",
        "heading_3",
        "paragraph",
        "bulleted_list_item",
        "numbered_list_item",
        "to_do",
        "quote",
        "divider",
        "code",
        "table",
    ):
        assert expected in _types(repaired), f"{expected} lost in round-trip"


def test_blocks_to_markdown_preserves_inline_formatting() -> None:
    blocks = markdown_to_blocks(
        "A paragraph with **bold**, *italic*, `code`, ~~strike~~ "
        "and a [link](https://x.test)."
    )
    md = blocks_to_markdown(blocks)
    assert "**bold**" in md
    assert "*italic*" in md
    assert "`code`" in md
    assert "~~strike~~" in md
    assert "[link](https://x.test)" in md


def test_blocks_to_markdown_reads_api_response_shape() -> None:
    # Blocks from blocks.children.list carry plain_text + full annotations dicts.
    blocks = [
        {
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": "Live heading",
                        "annotations": {"bold": False, "italic": False, "code": False},
                    }
                ]
            },
        },
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": "emphasised",
                        "annotations": {"bold": True, "italic": False, "code": False},
                    }
                ]
            },
        },
    ]
    md = blocks_to_markdown(blocks)
    assert "## Live heading" in md
    assert "**emphasised**" in md


# --------------------------------------------------------------------------- #
# T-02 — repair expands layered / over-long paragraphs
# --------------------------------------------------------------------------- #


def _api_paragraph(text: str) -> dict:
    return {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {"type": "text", "plain_text": text, "text": {"content": text}}
            ]
        },
    }


def test_repair_expands_layered_paragraph() -> None:
    # A single paragraph whose body is really a markdown list (garbled publish).
    garbled = [_api_paragraph("- alpha\n- beta\n- gamma")]
    repaired = repair_blocks(garbled)

    assert _types(repaired) == ["bulleted_list_item"] * 3
    assert _plains(repaired) == ["alpha", "beta", "gamma"]


def test_repair_splits_overlong_paragraph() -> None:
    # An over-long (but splittable) paragraph splits across the Notion block
    # limit instead of staying one oversized block.
    long_text = "word " * 600  # ~3000 chars, splittable at spaces
    repaired = repair_blocks([_api_paragraph(long_text)])

    assert len(repaired) >= 2
    assert all(b["type"] == "paragraph" for b in repaired)


def test_repair_is_idempotent() -> None:
    # Repairing already-repaired blocks is a fixed point.
    once = repair_blocks(markdown_to_blocks(CLEAN_DOC))
    twice = repair_blocks(once)
    assert _types(twice) == _types(once)
    assert _plains(twice) == _plains(once)
