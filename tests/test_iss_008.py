"""Reproducing test for ISS-008 — markdown_to_blocks() emits unsupported
code-fence languages (e.g. csv), which Notion's code-block language enum
rejects with a 400. Unsupported languages must map to 'plain text'; supported
languages (and existing aliases) must be preserved.
"""

from __future__ import annotations

from notion_ops.utils.markdown import markdown_to_blocks


def _code_block(md: str) -> dict:
    blocks = markdown_to_blocks(md)
    assert blocks and blocks[0]["type"] == "code", blocks
    return blocks[0]["code"]


def test_iss_008_csv_fence_maps_to_plain_text():
    assert _code_block("```csv\na,b\n```")["language"] == "plain text"


def test_iss_008_tsv_fence_maps_to_plain_text():
    assert _code_block("```tsv\na\tb\n```")["language"] == "plain text"


def test_iss_008_supported_language_preserved():
    assert _code_block("```python\nprint(1)\n```")["language"] == "python"


def test_iss_008_existing_alias_still_maps():
    # py -> python alias must keep working after the allow-list is added.
    assert _code_block("```py\nx = 1\n```")["language"] == "python"
