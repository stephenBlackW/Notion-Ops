"""AC-4: Demonstrative PoC -- oversized unsplittable text escalates (ISS-013).

PASSING guard: asserts the library already raises OversizedContentError when
a whitespace-free run longer than ~1900 chars is passed to markdown_to_blocks().
This is a DEFENSIVE behavior: the library escalates rather than silently
fragmenting the input. The test is a permanent regression guard.

Vector: oversized / malformed payload (ISS-013)
Attack surface: markdown_to_blocks(markdown: str)
Primitive: silent fragmentation vs. explicit escalation
Status: ALREADY DEFENDED -- plain passing test (no xfail)
"""
from __future__ import annotations

import pytest

from notion_ops.exceptions import OversizedContentError
from notion_ops.utils.markdown import markdown_to_blocks


class TestOversizedEscalation:
    """AC-4: Oversized unsplittable text raises OversizedContentError."""

    def test_whitespace_free_run_exceeds_limit_raises(self) -> None:
        """A run of 2000 non-whitespace chars raises OversizedContentError."""
        # Adversarial input: no spaces or newlines, longer than the ~1900-char limit
        oversized = "x" * 2000
        with pytest.raises(OversizedContentError) as exc_info:
            markdown_to_blocks(oversized)
        err = exc_info.value
        assert err.run_length > 1900, (
            f"Expected run_length > 1900, got {err.run_length}"
        )

    def test_base64_blob_raises(self) -> None:
        """A pasted base64 blob (no whitespace) raises OversizedContentError."""
        # Simulates a pasted base64-encoded payload (a common malformed-input vector)
        import base64
        blob = base64.b64encode(b"A" * 2000).decode("ascii")  # ~2668 chars, no whitespace
        with pytest.raises(OversizedContentError):
            markdown_to_blocks(blob)

    def test_normal_prose_does_not_raise(self) -> None:
        """Normal prose (word-separated) does NOT raise OversizedContentError."""
        # Verify the defense is scoped to unsplittable runs, not all long text
        normal = " ".join(["word"] * 500)  # ~2500 chars but whitespace-separated
        result = markdown_to_blocks(normal)
        assert isinstance(result, list), "Expected a list of blocks for normal prose"

    def test_error_has_preview_attribute(self) -> None:
        """OversizedContentError carries a preview attribute (ISS-013 contract)."""
        oversized = "z" * 2000
        with pytest.raises(OversizedContentError) as exc_info:
            markdown_to_blocks(oversized)
        err = exc_info.value
        assert hasattr(err, "preview"), "OversizedContentError must have a preview attribute"
        assert hasattr(err, "limit"), "OversizedContentError must have a limit attribute"
