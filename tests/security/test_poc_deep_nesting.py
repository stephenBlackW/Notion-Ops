"""AC-6 / AC-B: Deep block nesting in publish_block_tree terminates.

PASSING guard (all depths): asserts that deeply-nested block trees plan and execute
under a FakeClient without RecursionError, make ≥1 append calls, and reach no real
network.

HL-B (security-redteam-campaign-RUN): adds empirical probes at depth {300, 500, 800,
3000, 5000} -- exercising the Python call-stack boundary and beyond.

ISS-019 Phase C (initial, rev1): rewrote the three recursive leaf helpers (_height,
_total_blocks, _max_children_count) as iterative traversals. This moved the overflow
boundary from ~500 (helpers) to ~2000 (_plan_append still recursive).

ISS-019 Phase C rev2 (security-redteam-campaign-RUN rev2): de-recursed _plan_append
itself using an explicit work-stack (the iterative driver in _plan_append + the
_process_sibling_group helper). This eliminates the sys.getrecursionlimit() ceiling
entirely: build_publish_plan on a linear-chain tree of ANY depth terminates without
RecursionError regardless of Python's call-stack limit.

WHAT IS NOW TRUE: there is NO sys.getrecursionlimit() ceiling in the publish planner.
_height, _total_blocks, _max_children_count, and _plan_append are all iterative.
The probes at depth 3000 and 5000 (below) confirm this: they terminate cleanly on
the same default sys.getrecursionlimit()=1000 that previously would have raised
RecursionError at depth ~2000.

Vector: unbounded recursion / scheduling bomb (depth)
Attack surface: publish_block_tree(client, parent_id, blocks, ...) and
               build_publish_plan(blocks, ...)
Primitive: a caller-constructed deep tree that overflows the Python call stack
Status: HARDENED (ISS-019 Phase C rev2) -- all depth tests are plain passing guards.
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

        ISS-019 Phase C (initial): before hardening, RecursionError at depth ≥500 in
        _height. After Phase C rev1 iterative helper rewrites, depth 500/800 passed but
        depth ≥2000 still raised RecursionError in _plan_append.

        ISS-019 Phase C rev2: _plan_append also de-recursed (iterative work-stack).
        No sys.getrecursionlimit() ceiling remains in the publish planner.
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

        ISS-019 Phase C rev2: all planner components (_height, _total_blocks,
        _max_children_count, _plan_append) are iterative. No sys.getrecursionlimit()
        ceiling remains. Depth 800 passes with substantial headroom (previously passed
        only because 800 < the old ~2000 _plan_append ceiling; now it passes because
        the ceiling is eliminated entirely).
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

    # --- rev2 probes ABOVE the old ~2000 _plan_append boundary --- #
    # These probes are FALSIFIABLE against the pre-rev2 code: depth 3000 and 5000
    # raised RecursionError in the old recursive _plan_append (confirmed with
    # default sys.getrecursionlimit()=1000). After Phase C rev2 de-recursion,
    # both depths terminate cleanly. If _plan_append were reverted to recursive,
    # these tests would fail with RecursionError -- genuine regression detection.

    def test_depth_3000_terminates(self) -> None:
        """rev2: depth-3000 tree terminates -- above the old ~2000 _plan_append ceiling.

        FALSIFIABLE: would raise RecursionError in the pre-rev2 recursive _plan_append.
        After ISS-019-rev2, no sys.getrecursionlimit() ceiling remains.
        """
        tree = _make_deep_tree(3000)
        client = FakeClient()
        result = publish_block_tree(client, "parent-deep-3000", tree)
        assert hasattr(result, "request_count")
        assert result.request_count >= 1, (
            "publish_block_tree(depth=3000) must make at least 1 append call"
        )

    def test_depth_3000_plan_terminates(self) -> None:
        """rev2: build_publish_plan on depth-3000 tree terminates (ISS-019-rev2)."""
        tree = _make_deep_tree(3000)
        plan = build_publish_plan(tree)
        assert isinstance(plan, list)
        assert len(plan) >= 1

    def test_depth_5000_terminates(self) -> None:
        """rev2: depth-5000 tree terminates -- far above the old ~2000 ceiling.

        FALSIFIABLE: would raise RecursionError in the pre-rev2 recursive _plan_append.
        After ISS-019-rev2, no sys.getrecursionlimit() ceiling remains.
        sys.getrecursionlimit()=1000 is the default; depth 5000 >> 1000 proves no
        stack constraint remains.
        """
        tree = _make_deep_tree(5000)
        client = FakeClient()
        result = publish_block_tree(client, "parent-deep-5000", tree)
        assert hasattr(result, "request_count")
        assert result.request_count >= 1, (
            f"publish_block_tree(depth=5000) must make at least 1 append call "
            f"(sys.recursionlimit={sys.getrecursionlimit()})"
        )

    def test_depth_5000_plan_terminates(self) -> None:
        """rev2: build_publish_plan on depth-5000 tree terminates (ISS-019-rev2)."""
        tree = _make_deep_tree(5000)
        plan = build_publish_plan(tree)
        assert isinstance(plan, list)
        assert len(plan) >= 1
