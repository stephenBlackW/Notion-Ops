"""Plan and execute multi-request publishing of nested Notion block trees.

``markdown_to_blocks()`` produces a fully nested block tree, but Notion's
``blocks.children.append`` endpoint has two constraints that make a single
request insufficient for arbitrary structure:

1. **At most two levels of nesting per request** (a block may carry its
   children and grandchildren inline, but no deeper).
2. The response returns IDs for **only the directly-appended (top-level)
   blocks** — deeper descendants' IDs are not returned.

Together these mean a sub-tree too deep to inline must be appended in a
follow-up request, and that follow-up can only attach to a block whose ID we
got back — i.e. a block that was top-level in some earlier request.

This module turns a nested tree into the minimum number of append requests
that respects those limits: sub-trees that fit every limit (nesting depth,
per-array child count, total block count, payload size) are sent inline;
larger ones have their children stripped and deferred into follow-up requests
keyed, by index, to the parent block IDs returned by the preceding append. A
table with more rows than fit in one request is created with its first rows
inline and the remaining rows appended to its own ID. Top-level siblings are
batched up to the 100-block and payload-size caps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.markdown import (
    _MAX_BLOCKS_PER_REQUEST,
    _MAX_PAYLOAD_BYTES,
    _estimate_block_size,
    markdown_to_blocks,
)
from notion_ops.utils.retry import retry_on_transient

logger = logging.getLogger(__name__)

# Notion accepts at most two levels of nesting (children + grandchildren)
# inside a single append request.
_MAX_INLINE_DEPTH = 2


@dataclass
class Followup:
    """A deferred append keyed to a block created by the parent request.

    ``parent_index`` indexes into the parent request's append response
    (``results``) to identify the block these ``requests`` should be appended
    under.
    """

    parent_index: int
    requests: list[AppendRequest]


@dataclass
class AppendRequest:
    """One ``blocks.children.append`` call.

    ``payload`` is the list of API-format block dicts appended under this
    request's (runtime-resolved) parent. ``followups`` are appends whose parent
    is one of the blocks created by *this* request.
    """

    payload: list[dict[str, Any]]
    followups: list[Followup] = field(default_factory=list)


@dataclass
class PublishResult:
    """Outcome of executing a publish plan."""

    request_count: int
    top_level_block_ids: list[str]


def _children_of(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the inline child blocks of an API-format block dict, if any."""
    body = block.get(block.get("type", ""))
    if isinstance(body, dict):
        children = body.get("children")
        if isinstance(children, list):
            return children
    return []


def _height(block: dict[str, Any]) -> int:
    """Height of a block's subtree (0 for a block with no children)."""
    children = _children_of(block)
    if not children:
        return 0
    return 1 + max(_height(child) for child in children)


def _without_children(block: dict[str, Any]) -> dict[str, Any]:
    """A shallow copy of *block* with its inline ``children`` removed."""
    btype = block.get("type", "")
    new = dict(block)
    body = block.get(btype)
    if isinstance(body, dict) and "children" in body:
        new_body = dict(body)
        new_body.pop("children", None)
        new[btype] = new_body
    return new


def _with_children(
    block: dict[str, Any], children: list[dict[str, Any]]
) -> dict[str, Any]:
    """A shallow copy of *block* whose inline ``children`` are *children*."""
    btype = block.get("type", "")
    new = dict(block)
    body = block.get(btype)
    if isinstance(body, dict):
        new_body = dict(body)
        new_body["children"] = children
        new[btype] = new_body
    return new


def _total_blocks(block: dict[str, Any]) -> int:
    """Total number of blocks in a subtree, including the block itself."""
    return 1 + sum(_total_blocks(child) for child in _children_of(block))


def _max_children_count(block: dict[str, Any]) -> int:
    """Largest single ``children`` array anywhere in the subtree."""
    children = _children_of(block)
    maximum = len(children)
    for child in children:
        maximum = max(maximum, _max_children_count(child))
    return maximum


def _can_inline(
    block: dict[str, Any],
    max_inline_depth: int,
    max_blocks: int,
    max_bytes: int,
) -> bool:
    """Whether *block*'s whole subtree fits inline in one append request.

    Requires it to be shallow enough (``max_inline_depth``), to keep every
    ``children`` array within the per-parent cap, to fit the total-block
    budget, and to stay within the payload-size budget.
    """
    return (
        _height(block) <= max_inline_depth
        and _max_children_count(block) <= max_blocks
        and _total_blocks(block) <= max_blocks
        and _estimate_block_size(block) <= max_bytes
    )


def _batch_rows(
    rows: list[dict[str, Any]], max_rows: int, max_bytes: int
) -> list[list[dict[str, Any]]]:
    """Group table rows into batches within the count and size caps."""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        row_size = _estimate_block_size(row)
        if current and (len(current) >= max_rows or size + row_size > max_bytes):
            batches.append(current)
            current = []
            size = 0
        current.append(row)
        size += row_size
    if current:
        batches.append(current)
    return batches


def _split_table(
    table: dict[str, Any], max_blocks: int, max_bytes: int
) -> tuple[dict[str, Any], list[AppendRequest]]:
    """Split a table that is too large for one request.

    A table is created with its first batch of rows inline (it must stay a
    top-level block so its ID is returned), and the remaining rows are appended
    to that table ID in follow-up requests. A table is never emitted with zero
    rows.
    """
    rows = _children_of(table)
    # The first batch shares the request with the table block itself, so leave
    # room for it under the per-request block budget.
    head_limit = max(1, max_blocks - 1)
    head_batches = _batch_rows(rows, head_limit, max_bytes)
    head = head_batches[0] if head_batches else []
    rest = rows[len(head):]
    inlined = _with_children(table, head)
    followups = [
        AppendRequest(payload=batch)
        for batch in _batch_rows(rest, max_blocks, max_bytes)
    ]
    return inlined, followups


def build_publish_plan(
    blocks: list[dict[str, Any]],
    *,
    max_inline_depth: int = _MAX_INLINE_DEPTH,
    max_blocks_per_request: int = _MAX_BLOCKS_PER_REQUEST,
    max_payload_bytes: int = _MAX_PAYLOAD_BYTES,
) -> list[AppendRequest]:
    """Plan the minimum sequence of append requests for a nested block tree.

    The returned requests are pure data (no IDs); execute them with
    :func:`execute_plan` / :func:`publish_block_tree`.
    """
    return _plan_append(
        blocks, max_inline_depth, max_blocks_per_request, max_payload_bytes
    )


def _plan_append(
    nodes: list[dict[str, Any]],
    max_inline_depth: int,
    max_blocks: int,
    max_bytes: int,
) -> list[AppendRequest]:
    requests: list[AppendRequest] = []
    payload: list[dict[str, Any]] = []
    followups: list[Followup] = []
    size = 0
    blocks = 0

    def flush() -> None:
        nonlocal payload, followups, size, blocks
        if payload:
            requests.append(AppendRequest(payload=payload, followups=followups))
            payload = []
            followups = []
            size = 0
            blocks = 0

    for node in nodes:
        # Decide how this node enters the request:
        #  - inline the whole sub-tree when it fits every limit; else
        #  - a table too big for one request keeps its first rows and defers
        #    the rest to its own ID (it must stay top-level); else
        #  - append the node alone and defer its children (deferral can only
        #    attach to a top-level block, which this node now is).
        if _can_inline(node, max_inline_depth, max_blocks, max_bytes):
            inlined = node
            child_requests: list[AppendRequest] = []
        elif node.get("type") == "table":
            inlined, child_requests = _split_table(node, max_blocks, max_bytes)
        else:
            inlined = _without_children(node)
            child_requests = _plan_append(
                _children_of(node), max_inline_depth, max_blocks, max_bytes
            )

        node_size = _estimate_block_size(inlined)
        node_blocks = _total_blocks(inlined)
        if payload and (
            blocks + node_blocks > max_blocks or size + node_size > max_bytes
        ):
            flush()

        index = len(payload)
        payload.append(inlined)
        size += node_size
        blocks += node_blocks

        if child_requests:
            followups.append(Followup(parent_index=index, requests=child_requests))

    flush()
    return requests


def count_requests(plan: list[AppendRequest]) -> int:
    """Total number of append API calls a plan will make (incl. follow-ups)."""
    total = 0
    for request in plan:
        total += 1
        for followup in request.followups:
            total += count_requests(followup.requests)
    return total


def execute_plan(
    client: Any,
    parent_id: str,
    plan: list[AppendRequest],
) -> PublishResult:
    """Execute a publish plan against a live client.

    Each append's response provides the IDs used to resolve the parents of its
    follow-up requests. Returns the number of requests made and the IDs of the
    top-level blocks created directly under *parent_id*.
    """

    @retry_on_transient
    def _append(block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        result = client.api.blocks.children.append(
            block_id=block_id, children=children
        )
        return result if isinstance(result, dict) else {}

    state = {"count": 0}
    top_level_ids: list[str] = []

    def run(
        requests: list[AppendRequest],
        resolved_parent_id: str,
        collect: list[str] | None,
    ) -> None:
        for request in requests:
            response = _append(resolved_parent_id, request.payload)
            state["count"] += 1
            results = response.get("results", []) or []
            ids = [block.get("id") for block in results]
            if collect is not None:
                collect.extend(i for i in ids if i)

            for followup in request.followups:
                parent = (
                    ids[followup.parent_index]
                    if followup.parent_index < len(ids)
                    else None
                )
                if parent:
                    run(followup.requests, parent, None)
                else:
                    logger.warning(
                        "No parent id returned for deferred append at index %s; "
                        "skipping %d follow-up request(s). Nested content may be "
                        "incomplete.",
                        followup.parent_index,
                        count_requests(followup.requests),
                    )

    run(plan, extract_notion_id(parent_id), top_level_ids)
    return PublishResult(
        request_count=state["count"], top_level_block_ids=top_level_ids
    )


def publish_block_tree(
    client: Any,
    parent_id: str,
    blocks: list[dict[str, Any]],
    *,
    max_inline_depth: int = _MAX_INLINE_DEPTH,
    max_blocks_per_request: int = _MAX_BLOCKS_PER_REQUEST,
    max_payload_bytes: int = _MAX_PAYLOAD_BYTES,
) -> PublishResult:
    """Plan and publish a nested block tree under *parent_id*."""
    plan = build_publish_plan(
        blocks,
        max_inline_depth=max_inline_depth,
        max_blocks_per_request=max_blocks_per_request,
        max_payload_bytes=max_payload_bytes,
    )
    return execute_plan(client, parent_id, plan)


def publish_markdown(
    client: Any,
    parent_id: str,
    markdown: str,
    **kwargs: Any,
) -> PublishResult:
    """Convert *markdown* to blocks and publish them under *parent_id*."""
    return publish_block_tree(client, parent_id, markdown_to_blocks(markdown), **kwargs)
