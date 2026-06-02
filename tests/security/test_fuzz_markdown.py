"""AC-8: Bounded, deterministic hypothesis fuzz over markdown_to_blocks.

Safety property: for any text input, markdown_to_blocks MUST either:
  (a) return a list of well-formed block dicts (each has "type" and a body
      keyed by that type), OR
  (b) raise exactly OversizedContentError (the one declared escalation, ISS-013).
It MUST NOT raise any other exception type and MUST always terminate.

Profile: hypothesis CI profile (max_examples=50, derandomize=True, deadline=None)
-- registered in tests/security/conftest.py and loaded at import time.
Re-running twice with the same codebase yields identical outcomes (determinism).

D-I5: OversizedContentError is EXPECTED and ALLOWED (not a fuzz failure).
D-I4: hypothesis only; no atheris/native fuzzer (CI must be deterministic).
"""
from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from notion_ops.exceptions import OversizedContentError
from notion_ops.utils.markdown import markdown_to_blocks

# FIX-3 (ao-meta-I rev2): inline @settings(...) overrides have been REMOVED so the
# registered "ci" profile (derandomize=True, max_examples=50, deadline=None) actually
# governs these fuzz tests rather than being bypassed by inline literals.
# The CI profile is loaded by conftest.py at import time via settings.load_profile("ci").


def _is_well_formed_block(block: Any) -> bool:
    """Return True if block is a well-formed Notion API block dict.

    A well-formed block must have:
    - "type" key (str)
    - A body key matching the type (dict or list)
    """
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    if not isinstance(btype, str):
        return False
    # The body key must be present (Notion block format: {"type": "paragraph", "paragraph": {...}})
    if btype not in block:
        return False
    return True


@given(text=st.text(max_size=5000))
def test_fuzz_markdown_to_blocks_safety_property(text: str) -> None:
    """AC-8: markdown_to_blocks(text) never raises unexpected exceptions; always terminates."""
    try:
        result = markdown_to_blocks(text)
    except OversizedContentError:
        # EXPECTED and ALLOWED (D-I5, ISS-013): not a fuzz failure
        return
    except Exception as exc:
        pytest.fail(
            f"markdown_to_blocks raised unexpected {type(exc).__name__}: {exc}\n"
            f"Input (first 100 chars): {text[:100]!r}"
        )

    # If no exception: result must be a list of well-formed block dicts
    assert isinstance(result, list), (
        f"markdown_to_blocks must return a list, got {type(result).__name__}\n"
        f"Input: {text[:100]!r}"
    )
    for i, block in enumerate(result):
        assert _is_well_formed_block(block), (
            f"Block at index {i} is malformed: {block!r}\n"
            f"Input (first 100 chars): {text[:100]!r}"
        )


@given(text=st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Pc", "Pd")), max_size=3000))
def test_fuzz_alphanumeric_text_no_unexpected_exception(text: str) -> None:
    """AC-8: Alphanumeric text with punctuation never raises unexpected exceptions."""
    try:
        result = markdown_to_blocks(text)
    except OversizedContentError:
        return  # allowed
    except Exception as exc:
        pytest.fail(
            f"Unexpected {type(exc).__name__} on alphanumeric input: {exc}\n"
            f"Input: {text[:100]!r}"
        )
    assert isinstance(result, list)


@given(
    text=st.one_of(
        # Adversarial delimiter inputs
        st.text(alphabet="*_~`[]()", max_size=200),
        # Mixed normal + delimiter
        st.builds(
            lambda a, b: a + b,
            st.text(max_size=50),
            st.text(alphabet="*_~`", max_size=50),
        ),
    )
)
def test_fuzz_adversarial_delimiters_no_unexpected_exception(text: str) -> None:
    """AC-8: Adversarial delimiter inputs (potential ReDoS triggers) never raise unexpectedly."""
    try:
        result = markdown_to_blocks(text)
    except OversizedContentError:
        return  # allowed
    except Exception as exc:
        pytest.fail(
            f"Unexpected {type(exc).__name__} on delimiter input: {exc}\n"
            f"Input: {text[:100]!r}"
        )
    assert isinstance(result, list)


@given(text=st.text(max_size=300))
def test_fuzz_blocks_have_type_field(text: str) -> None:
    """AC-8: Every block in markdown_to_blocks output has a 'type' field."""
    try:
        result = markdown_to_blocks(text)
    except OversizedContentError:
        return  # allowed (D-I5)
    except Exception as exc:
        pytest.fail(f"Unexpected exception: {exc}")

    for block in result:
        assert "type" in block, f"Block missing 'type' field: {block!r}"
        assert isinstance(block["type"], str), f"Block 'type' must be str: {block!r}"


# ---------------------------------------------------------------------------
# HL-D (AC-D): Meta-test binding _is_well_formed_block
# ---------------------------------------------------------------------------
# The fuzz invariant in test_fuzz_markdown_to_blocks_safety_property relies on
# _is_well_formed_block to detect malformed output. If the predicate is a no-op
# (accepts everything), the fuzz guard would silently pass on malformed output.
#
# This meta-test asserts that _is_well_formed_block:
#   (a) REJECTS malformed inputs (non-dict, missing 'type', type-without-body)
#   (b) ACCEPTS a known-good well-formed block
# so the fuzz invariant cannot silently pass on a broken predicate.


class TestIsWellFormedBlockMetaTest:
    """AC-D (HL-D): Meta-test binding _is_well_formed_block -- not a no-op predicate."""

    def test_accepts_known_good_paragraph_block(self) -> None:
        """Predicate accepts a correctly-formed paragraph block."""
        block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "hello"}}]
            },
        }
        assert _is_well_formed_block(block) is True, (
            "_is_well_formed_block should accept a well-formed paragraph block"
        )

    def test_accepts_known_good_heading_block(self) -> None:
        """Predicate accepts a correctly-formed heading_1 block."""
        block = {
            "type": "heading_1",
            "heading_1": {
                "rich_text": [{"type": "text", "text": {"content": "Title"}}]
            },
        }
        assert _is_well_formed_block(block) is True, (
            "_is_well_formed_block should accept a well-formed heading_1 block"
        )

    def test_accepts_known_good_toggle_block(self) -> None:
        """Predicate accepts a correctly-formed toggle block."""
        block = {
            "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "text": {"content": "Toggle"}}],
                "children": [],
            },
        }
        assert _is_well_formed_block(block) is True, (
            "_is_well_formed_block should accept a well-formed toggle block"
        )

    def test_rejects_non_dict(self) -> None:
        """Predicate rejects non-dict inputs (list, string, None, int)."""
        for bad in [[], "string", None, 42, True]:
            assert _is_well_formed_block(bad) is False, (
                f"_is_well_formed_block should reject non-dict: {bad!r}"
            )

    def test_rejects_dict_missing_type_key(self) -> None:
        """Predicate rejects dicts without a 'type' key."""
        assert _is_well_formed_block({}) is False, (
            "_is_well_formed_block should reject empty dict (no 'type')"
        )
        assert _is_well_formed_block({"paragraph": {"rich_text": []}}) is False, (
            "_is_well_formed_block should reject dict missing 'type' key"
        )

    def test_rejects_type_not_string(self) -> None:
        """Predicate rejects blocks where 'type' is not a string."""
        assert _is_well_formed_block({"type": 42, "paragraph": {}}) is False, (
            "_is_well_formed_block should reject block with non-string 'type'"
        )
        assert _is_well_formed_block({"type": None, "paragraph": {}}) is False, (
            "_is_well_formed_block should reject block with None 'type'"
        )

    def test_rejects_type_without_matching_body_key(self) -> None:
        """Predicate rejects blocks where the body key matching 'type' is absent."""
        # Block says type='paragraph' but has no 'paragraph' body key
        assert _is_well_formed_block({"type": "paragraph"}) is False, (
            "_is_well_formed_block should reject block with type='paragraph' but no 'paragraph' body"
        )
        # Block has a different key, not matching type
        assert _is_well_formed_block({"type": "paragraph", "heading_1": {}}) is False, (
            "_is_well_formed_block should reject block with type mismatch (type=paragraph, body=heading_1)"
        )

    def test_predicate_is_not_trivially_true(self) -> None:
        """Sanity: the predicate rejects at least one clearly malformed input.

        This test ensures the predicate is not a constant-True no-op.
        If _is_well_formed_block always returned True, the fuzz invariant in
        test_fuzz_markdown_to_blocks_safety_property would never catch malformed output.
        """
        clearly_malformed = [
            {},
            {"type": "paragraph"},  # missing body key
            {"paragraph": {}},      # missing type key
            "not_a_dict",
            None,
        ]
        rejections = sum(
            1 for m in clearly_malformed if not _is_well_formed_block(m)
        )
        assert rejections == len(clearly_malformed), (
            f"_is_well_formed_block must reject all {len(clearly_malformed)} "
            f"clearly-malformed inputs; only rejected {rejections}. "
            "If the predicate accepts everything, the fuzz invariant is a no-op."
        )
