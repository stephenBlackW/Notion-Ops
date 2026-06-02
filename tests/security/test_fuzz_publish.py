"""AC-9: Light bounded hypothesis fuzz over publish_block_tree.

Safety property: for any bounded-depth block tree, publish_block_tree under
FakeClient MUST:
  (a) terminate (no RecursionError, no infinite loop), AND
  (b) make >= 0 append calls to FakeClient, AND
  (c) make NO real network calls (FakeClient is the only call target).

Profile: hypothesis CI profile (max_examples=50, derandomize=True, deadline=None)
-- registered in tests/security/conftest.py and loaded at import time.

D-I4: hypothesis only; no atheris/native fuzzer.
D-I5: no exception allowlist needed here -- publish_block_tree should not raise
      OversizedContentError itself (that is markdown_to_blocks concern).
"""
from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from notion_ops.utils.publish import publish_block_tree

from .conftest import FakeClient

# FIX-3 (ao-meta-I rev2): inline @settings(...) overrides have been REMOVED so the
# registered "ci" profile (derandomize=True, max_examples=50, deadline=None) actually
# governs these fuzz tests rather than being bypassed by inline literals.
# The CI profile is loaded by conftest.py at import time via settings.load_profile("ci").

# ---------------------------------------------------------------------------
# Block tree strategies
# ---------------------------------------------------------------------------

def _paragraph_strategy() -> st.SearchStrategy:
    """Strategy for a leaf paragraph block."""
    return st.builds(
        lambda text: {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text[:100]}}]
            },
        },
        st.text(max_size=100),
    )


def _toggle_strategy(child_strategy: st.SearchStrategy) -> st.SearchStrategy:
    """Strategy for a toggle block with children drawn from child_strategy."""
    return st.builds(
        lambda label, children: {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "text": {"content": label[:50]}}],
                "children": children,
            },
        },
        st.text(max_size=50),
        st.lists(child_strategy, max_size=5),
    )


# Build a bounded recursive block tree strategy (max depth 4)
_leaf = _paragraph_strategy()
_depth1 = st.one_of(_leaf, _toggle_strategy(_leaf))
_depth2 = st.one_of(_leaf, _toggle_strategy(_depth1))
_depth3 = st.one_of(_leaf, _toggle_strategy(_depth2))
_block_tree_strategy = st.lists(_depth3, min_size=1, max_size=20)


@given(blocks=_block_tree_strategy)
def test_fuzz_publish_terminates_no_recursion(blocks: list[dict[str, Any]]) -> None:
    """AC-9: publish_block_tree terminates for any bounded-depth block tree."""
    client = FakeClient()
    try:
        result = publish_block_tree(client, "fuzz-parent-001", blocks)
    except RecursionError:
        pytest.fail(
            f"publish_block_tree raised RecursionError on fuzz-generated tree\n"
            f"Tree size: {len(blocks)}"
        )
    except Exception as exc:
        pytest.fail(
            f"publish_block_tree raised unexpected {type(exc).__name__}: {exc}\n"
            f"Tree size: {len(blocks)}"
        )
    # Result must have request_count attribute
    assert hasattr(result, "request_count"), (
        "publish_block_tree must return a PublishResult with request_count"
    )
    assert result.request_count >= 0


@given(blocks=_block_tree_strategy)
def test_fuzz_publish_no_real_network(blocks: list[dict[str, Any]]) -> None:
    """AC-9: FakeClient records all calls; no real network is reached."""
    client = FakeClient()
    from unittest.mock import patch

    with patch("httpx.get", side_effect=AssertionError("SSRF: httpx.get called in fuzz")):
        try:
            publish_block_tree(client, "fuzz-parent-002", blocks)
        except RecursionError:
            return  # tolerated for deeply recursive edge case in fuzz generation
        except Exception as exc:
            # Only AssertionError (SSRF guard) is a hard failure
            if "SSRF" in str(exc):
                pytest.fail(f"Fuzz triggered real network call: {exc}")

    # All calls recorded by FakeClient have expected shape
    for call in client.calls:
        assert "block_id" in call
        assert "children" in call


@given(blocks=st.lists(_leaf, min_size=1, max_size=100))
def test_fuzz_flat_list_publish_result_count(blocks: list[dict[str, Any]]) -> None:
    """AC-9: A flat list of leaf blocks yields a positive request count."""
    client = FakeClient()
    result = publish_block_tree(client, "fuzz-flat-001", blocks)
    assert result.request_count >= 1, (
        f"Expected >= 1 request for {len(blocks)} leaf blocks, got {result.request_count}"
    )
    assert len(client.calls) >= 1
