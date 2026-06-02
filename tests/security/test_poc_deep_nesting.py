"""AC-6 / AC-B: Deep block nesting in publish_block_tree terminates.

PASSING guard (depths ≤300): asserts that deeply-nested-but-bounded block trees
plan and execute under a FakeClient without RecursionError, make ≥1 append calls,
and reach no real network.

HL-B (security-redteam-campaign-RUN): adds empirical probes at depth {300, 500, 800}
-- the REAL Python recursion boundary (sys.getrecursionlimit()=1000). The demonstrative
depth-50 never exercises the stack-overflow boundary; these deeper probes do.

Vector: unbounded recursion / scheduling bomb (depth)
Attack surface: publish_block_tree(client, parent_id, blocks, ...) and
               build_publish_plan(blocks, ...)
Primitive: a caller-constructed deep tree that overflows the Python call stack
Status (depth ≤300): ALREADY BOUNDED -- passing guards
Status (depth ≥500): EXPLOITABLE (ISS-019) -- RecursionError in _height/_total_blocks/
                     _max_children_count at depth ≥500 with default sys.recursionlimit=1000.
                     HARDENED in Phase C via iterative stack-based traversal.
                     Tests below were xfail(strict=True) before hardening; now passing guards.

NOTE: Once Phase C lands, ALL depth tests become plain passing guards.
"""
from __future__ import annotations

import sys

import pytest

from notion_ops.utils.publish import build_publish_plan, publish_block_tree

from .conftest import FakeClient


def _leaf(i: int) -> dict:
    """Minimal paragraph block."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": f"p{i}"}}]
        },
    }


def _toggle(label: str, children: list) -> dict:
    """Toggle block with children."""
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": label}}],
            "children": children,
        },
    }


def _make_deep_tree(depth: int) -> list:
    """Build a single linear chain of toggles depth levels deep.

    depth=1 -> [toggle([leaf])]
    depth=2 -> [toggle([toggle([leaf])])]
    etc.
    This is the canonical HL-B adversarial input: a pure linear chain that
    maximises call-stack consumption in recursive tree helpers.
    """
    node: dict = _leaf(0)
    for i in range(depth - 1, 0, -1):
        node = _toggle(f"level-{i}", [node])
    return [node]


class TestDeepNestingBound:
    """AC-6 / AC-B: Deep block nesting terminates under FakeClient (recursion bound)."""

    # --- Demonstrative guards (depth ≤50) --- #

    def test_depth_10_terminates(self) -> None:
        """A tree of depth 10 plans and executes without RecursionError."""
        tree = _make_deep_tree(10)
        client = FakeClient()
        result = publish_block_tree(client, "parent-id-001", tree)
        # Result must be a PublishResult (has request_count and top_level_block_ids)
        assert hasattr(result, "request_count")
        assert result.request_count >= 1, "Expected at least 1 append call"
        assert len(client.calls) >= 1, "FakeClient recorded no calls"

    def test_depth_20_terminates(self) -> None:
        """A tree of depth 20 plans and executes without RecursionError."""
        tree = _make_deep_tree(20)
        client = FakeClient()
        result = publish_block_tree(client, "parent-id-002", tree)
        assert hasattr(result, "request_count")
        assert result.request_count >= 1

    def test_depth_50_terminates(self) -> None:
        """A tree of depth 50 terminates -- a depth-50 nesting is a realistic upper bound."""
        tree = _make_deep_tree(50)
        client = FakeClient()
        # Must NOT raise RecursionError
        try:
            result = publish_block_tree(client, "parent-id-003", tree)
        except RecursionError:
            pytest.fail("publish_block_tree raised RecursionError on depth-50 tree")
        assert hasattr(result, "request_count")

    def test_no_real_network_on_deep_tree(self) -> None:
        """Deep tree execution uses only FakeClient calls (no real network).

        The autouse _block_network fixture in conftest.py blocks real transport/socket.
        This test verifies all calls were recorded by FakeClient.
        """
        tree = _make_deep_tree(20)
        client = FakeClient()
        # FakeClient does not make any HTTP calls; if publish_block_tree tried to,
        # it would have to bypass the FakeClient -- this test ensures it does not.
        publish_block_tree(client, "parent-id-004", tree)
        # Verify all calls were recorded by FakeClient (each has block_id + children)
        for call in client.calls:
            assert "block_id" in call
            assert "children" in call

    def test_plan_only_on_deep_tree_terminates(self) -> None:
        """build_publish_plan terminates on a depth-50 tree (planning-only assertion)."""
        tree = _make_deep_tree(50)
        try:
            plan = build_publish_plan(tree)
        except RecursionError:
            pytest.fail("build_publish_plan raised RecursionError on depth-50 tree")
        assert isinstance(plan, list), "Expected a list of AppendRequest objects"
        assert len(plan) >= 1

    def test_wide_shallow_tree_terminates(self) -> None:
        """A wide (100 siblings) shallow (depth 2) tree terminates correctly."""
        # 100 toggles each with 2 leaf children -- wide but shallow
        tree = [_toggle(f"t{i}", [_leaf(0), _leaf(1)]) for i in range(100)]
        client = FakeClient()
        result = publish_block_tree(client, "parent-id-005", tree)
        assert result.request_count >= 1

    # --- HL-B probes: real recursion boundary (depth 300/500/800) --- #

    def test_depth_300_terminates(self) -> None:
        """HL-B: depth-300 linear-chain tree terminates without RecursionError.

        Empirical result (security-redteam-campaign-RUN Phase B probe):
        depth-300 completes near the stack margin (~900 frames used across the
        three recursive helpers). After Phase C iterative hardening, this passes
        with substantial headroom.
        """
        tree = _make_deep_tree(300)
        client = FakeClient()
        result = publish_block_tree(client, "parent-deep-300", tree)
        assert hasattr(result, "request_count")
        assert result.request_count >= 1, (
            "publish_block_tree(depth=300) must make at least 1 append call"
        )

    def test_depth_300_plan_terminates(self) -> None:
        """HL-B: build_publish_plan on a depth-300 tree terminates."""
        tree = _make_deep_tree(300)
        plan = build_publish_plan(tree)
        assert isinstance(plan, list)
        assert len(plan) >= 1, "Expected at least 1 AppendRequest"

    def test_depth_500_terminates(self) -> None:
        """HL-B: depth-500 linear-chain tree terminates without RecursionError.

        ISS-019 (hardened in Phase C): before hardening, this raised RecursionError
        in _height/_total_blocks/_max_children_count at depth ≥500. After iterative
        rewrite, it completes cleanly regardless of sys.getrecursionlimit().

        Phase C fix: _height, _total_blocks, _max_children_count rewritten as
        iterative stack-based traversals (vendor/notion-ops/notion_ops/utils/publish.py).
        """
        tree = _make_deep_tree(500)
        client = FakeClient()
        result = publish_block_tree(client, "parent-deep-500", tree)
        assert hasattr(result, "request_count")
        assert result.request_count >= 1, (
            f"publish_block_tree(depth=500) must make at least 1 append call "
            f"(sys.recursionlimit={sys.getrecursionlimit()})"
        )

    def test_depth_500_plan_terminates(self) -> None:
        """HL-B: build_publish_plan on a depth-500 tree terminates (ISS-019 hardened)."""
        tree = _make_deep_tree(500)
        plan = build_publish_plan(tree)
        assert isinstance(plan, list)
        assert len(plan) >= 1

    def test_depth_800_terminates(self) -> None:
        """HL-B: depth-800 linear-chain tree terminates without RecursionError.

        ISS-019 (hardened in Phase C): deepest empirical probe. Before hardening,
        RecursionError at depth 800. After iterative rewrite, cleanly terminates
        with no stack constraint from sys.getrecursionlimit().
        """
        tree = _make_deep_tree(800)
        client = FakeClient()
        result = publish_block_tree(client, "parent-deep-800", tree)
        assert hasattr(result, "request_count")
        assert result.request_count >= 1, (
            f"publish_block_tree(depth=800) must make at least 1 append call "
            f"(sys.recursionlimit={sys.getrecursionlimit()})"
        )

    def test_depth_800_plan_terminates(self) -> None:
        """HL-B: build_publish_plan on a depth-800 tree terminates (ISS-019 hardened)."""
        tree = _make_deep_tree(800)
        plan = build_publish_plan(tree)
        assert isinstance(plan, list)
        assert len(plan) >= 1
