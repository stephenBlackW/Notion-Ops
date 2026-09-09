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

import hashlib
import json
import logging
from collections import deque
from collections.abc import Callable
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
    """Outcome of executing a publish plan.

    ``partial`` is ``True`` when one or more deferred (follow-up) appends could
    not be executed because the parent block id was not returned by the
    preceding request — i.e. some nested content was dropped. ``skipped_followups``
    counts the dropped follow-up requests. Callers that need every block to land
    (e.g. AOS atom publishing) can branch on ``partial`` instead of scraping the
    logs (HL-patchA-1).
    """

    request_count: int
    top_level_block_ids: list[str]
    partial: bool = False
    skipped_followups: int = 0


def _children_of(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the inline child blocks of an API-format block dict, if any."""
    body = block.get(block.get("type", ""))
    if isinstance(body, dict):
        children = body.get("children")
        if isinstance(children, list):
            return children
    return []


def _height(block: dict[str, Any]) -> int:
    """Height of a block's subtree (0 for a block with no children).

    ISS-019 (security-redteam-campaign-RUN Phase C): rewritten as an iterative
    stack-based post-order traversal to eliminate RecursionError on deep linear-
    chain trees (depth ≥500 would exhaust Python's default call-stack limit=1000
    when multiplied across the three helpers called by _can_inline).
    """
    # Iterative post-order with an explicit stack.
    # Each stack entry is (block, child_iterator, max_child_height).
    # When child_iterator is exhausted the block's height = 1 + max_child_height.
    stack: list[tuple[dict[str, Any], int, list[dict[str, Any]], int]] = []
    # (block, child_index, children, max_child_height_so_far)
    stack.append((block, 0, _children_of(block), -1))
    heights: dict[int, int] = {}  # id(block) -> computed height

    while stack:
        cur_block, child_idx, children, max_ch = stack[-1]
        if child_idx < len(children):
            child = children[child_idx]
            stack[-1] = (cur_block, child_idx + 1, children, max_ch)
            child_children = _children_of(child)
            stack.append((child, 0, child_children, -1))
        else:
            # All children processed; compute this block's height
            h = 0 if max_ch < 0 else 1 + max_ch
            heights[id(cur_block)] = h
            stack.pop()
            if stack:
                parent, p_idx, p_children, p_max = stack[-1]
                stack[-1] = (parent, p_idx, p_children, max(p_max, h))

    return heights.get(id(block), 0)


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
    """Total number of blocks in a subtree, including the block itself.

    ISS-019 (security-redteam-campaign-RUN Phase C): rewritten as an iterative
    stack-based pre-order traversal to eliminate RecursionError on deep trees.
    """
    total = 0
    stack: list[dict[str, Any]] = [block]
    while stack:
        node = stack.pop()
        total += 1
        stack.extend(_children_of(node))
    return total


def _max_children_count(block: dict[str, Any]) -> int:
    """Largest single ``children`` array anywhere in the subtree.

    ISS-019 (security-redteam-campaign-RUN Phase C): rewritten as an iterative
    stack-based traversal to eliminate RecursionError on deep trees.
    """
    maximum = 0
    stack: list[dict[str, Any]] = [block]
    while stack:
        node = stack.pop()
        children = _children_of(node)
        if len(children) > maximum:
            maximum = len(children)
        stack.extend(children)
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
    """Plan append requests for *nodes* using an explicit work-stack.

    ISS-019-rev2 (security-redteam-campaign-RUN Phase C rev2): de-recursed from a
    recursive self-call (the pre-rev2 version self-called at line 312) to an
    iterative explicit-stack formulation, consistent with the iterative helpers
    (_height / _total_blocks / _max_children_count). This eliminates the
    sys.getrecursionlimit() ceiling on _plan_append itself: a linear-chain tree of
    arbitrary depth no longer raises RecursionError regardless of Python's call-stack
    limit.

    The work stack carries (nodes_to_process, output_requests_list) frames.  Each
    frame processes one sibling list, batching nodes into AppendRequests and appending
    them into the provided output list.  When a non-inlinable, non-table node has
    children that need their own planning, a new frame is pushed onto the work stack
    (instead of a recursive call); its output list is pre-allocated and referenced by
    the Followup before the frame runs, so request ordering is preserved.

    Key invariants preserved vs the recursive form:
    - Requests within a sibling group are ordered left-to-right: each sibling group
      is processed in a single _process_sibling_group call (no reversal needed).
    - Followups are keyed by parent_index within the AppendRequest they hang off.
    - _total_blocks(inlined) is always called on the stripped/split/inlined node,
      never on the deep subtree (same as before).
    - The batch-flush logic (blocks/bytes caps) is identical to the recursive form.
    """
    # We need the sibling groups to be processed in order so that we can assign
    # parent_index values correctly.  An explicit stack is LIFO, so we push frames
    # in reverse order and process them front-to-back.
    #
    # Each stack entry: (nodes_list, output_list)
    # After processing, AppendRequest objects are appended to output_list.
    result: list[AppendRequest] = []
    # Stack of (sibling_nodes, destination_list) frames to process.
    # We seed with the top-level call's nodes going into `result`.
    work_stack: list[tuple[list[dict[str, Any]], list[AppendRequest]]] = [
        (nodes, result)
    ]

    while work_stack:
        cur_nodes, cur_requests = work_stack.pop()
        _process_sibling_group(
            cur_nodes, cur_requests, max_inline_depth, max_blocks, max_bytes,
            work_stack,
        )

    return result


def _process_sibling_group(
    nodes: list[dict[str, Any]],
    requests: list[AppendRequest],
    max_inline_depth: int,
    max_blocks: int,
    max_bytes: int,
    work_stack: list[tuple[list[dict[str, Any]], list[AppendRequest]]],
) -> None:
    """Process one sibling list into AppendRequests; push child frames onto work_stack.

    This is the body that the old recursive _plan_append executed per call.  It is
    now a plain function called by the iterative driver above.
    """
    payload: list[dict[str, Any]] = []
    followups: list[Followup] = []
    size = 0
    blocks = 0

    def _flush() -> None:
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
        #
        # child_requests is the list that will hold the child AppendRequests.
        # has_children_to_defer tracks whether we need to register a Followup
        # (cannot rely on `if child_requests` because the deferred list is empty
        # at this point -- it will be filled when the work_stack frame runs).
        child_requests: list[AppendRequest] = []
        has_children_to_defer = False

        if _can_inline(node, max_inline_depth, max_blocks, max_bytes):
            inlined = node
        elif node.get("type") == "table":
            inlined, child_requests = _split_table(node, max_blocks, max_bytes)
            has_children_to_defer = bool(child_requests)
        else:
            inlined = _without_children(node)
            # Pre-allocate the child AppendRequest list.  The Followup will
            # reference this list by identity; the work_stack frame will fill it
            # later (iteratively, not recursively).
            children = _children_of(node)
            if children:
                child_requests = []
                has_children_to_defer = True
                work_stack.append((children, child_requests))

        node_size = _estimate_block_size(inlined)
        node_blocks = _total_blocks(inlined)
        if payload and (
            blocks + node_blocks > max_blocks or size + node_size > max_bytes
        ):
            _flush()

        index = len(payload)
        payload.append(inlined)
        size += node_size
        blocks += node_blocks

        if has_children_to_defer:
            followups.append(Followup(parent_index=index, requests=child_requests))

    _flush()


def count_requests(plan: list[AppendRequest]) -> int:
    """Total number of append API calls a plan will make (incl. follow-ups).

    ISS-019-rev3 (security-redteam-campaign-RUN Phase C rev3): de-recursed from a
    recursive self-call (the pre-rev3 version called count_requests(followup.requests)
    at each level) to an iterative explicit-stack traversal.  This closes the FINAL
    recursive site in the publish planner: a depth-3000 plan (produced by
    build_publish_plan on a deep linear-chain tree) previously raised RecursionError
    because execute_plan's dropped-parent path calls count_requests(followup.requests)
    on a followup whose requests chain is as deep as the original tree.

    The iterative form is output-identical to the recursive form: it counts every
    AppendRequest object in the tree (each request + all transitively-nested followup
    requests).  The traversal uses an explicit work-stack (LIFO); push order matches
    DFS pre-order, which counts every node exactly once regardless of traversal order.
    """
    total = 0
    # Work stack holds lists of AppendRequest objects to process.
    stack: list[list[AppendRequest]] = [plan]
    while stack:
        requests = stack.pop()
        for request in requests:
            total += 1
            for followup in request.followups:
                if followup.requests:
                    stack.append(followup.requests)
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

    ISS-019-rev2: the inner execution loop is iterative (work-queue), consistent with
    the iterative _plan_append.  This eliminates the second recursion site: the old
    recursive ``run()`` inner function would overflow at depth ≥1000 when executing a
    deeply-nested plan (one AppendRequest per level, each with a Followup to the next).
    """

    @retry_on_transient
    def _append(block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        result = client.api.blocks.children.append(
            block_id=block_id, children=children
        )
        return result if isinstance(result, dict) else {}

    count = 0
    skipped = 0
    top_level_ids: list[str] = []

    # Work queue: (requests_to_execute, resolved_parent_id, collect_list_or_None)
    # FIFO: preserves the parent-ID chain (a request must run before its followups).
    queue: deque[tuple[list[AppendRequest], str, list[str] | None]] = deque()
    queue.append((plan, extract_notion_id(parent_id), top_level_ids))

    while queue:
        requests, resolved_parent_id, collect = queue.popleft()
        for request in requests:
            response = _append(resolved_parent_id, request.payload)
            count += 1
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
                    queue.append((followup.requests, parent, None))
                else:
                    dropped = count_requests(followup.requests)
                    skipped += dropped
                    logger.warning(
                        "No parent id returned for deferred append at index %s; "
                        "skipping %d follow-up request(s). Nested content may be "
                        "incomplete.",
                        followup.parent_index,
                        dropped,
                    )

    return PublishResult(
        request_count=count,
        top_level_block_ids=top_level_ids,
        partial=skipped > 0,
        skipped_followups=skipped,
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


# ---------------------------------------------------------------------------
# Idempotent republish (ISS-012)
# ---------------------------------------------------------------------------


@dataclass
class RepublishResult(PublishResult):
    """Outcome of an idempotent republish.

    Extends :class:`PublishResult` with ``deleted_count`` — the number of
    pre-existing top-level child blocks cleared before the new tree was
    published.
    """

    deleted_count: int = 0


def _list_children_blocks(client: Any, block_id: str) -> list[dict[str, Any]]:
    """List the full top-level child block dicts currently under *block_id*.

    Paginated and retry-wrapped, matching :func:`execute_plan`'s use of the raw
    SDK (``client.api``) so the publisher stays decoupled from the operations
    layer. Returns the API block dicts (id + type + body + ``has_children``),
    which the minimal-write diff needs to compare existing content.
    """

    @retry_on_transient
    def _list(bid: str, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"block_id": bid, "page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        result = client.api.blocks.children.list(**params)
        return result if isinstance(result, dict) else {}

    blocks: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        response = _list(block_id, cursor)
        for block in response.get("results", []) or []:
            if block.get("id"):
                blocks.append(block)
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
        if not cursor:
            logger.warning(
                "blocks.children.list reported has_more=True but returned no "
                "next_cursor for %s; stopping pagination. The child listing may "
                "be truncated.",
                block_id,
            )
            break
    return blocks


# ---------------------------------------------------------------------------
# Content hashing for the minimal-write republish diff (nops-cycle-2)
# ---------------------------------------------------------------------------

# Annotation flags at their Notion defaults carry no rendered meaning. The API
# returns the full set on every span; ``markdown_to_blocks`` emits only the
# non-default ones. Normalizing both to "non-default flags only" lets an
# API-returned block and a freshly-converted block hash equal when their content
# is the same. Dropping these can never make two *differently-rendered* blocks
# collide (a default flag renders identically to its absence), so the diff stays
# correctness-conservative: it may miss an optimization, never preserve a block
# whose content changed.
_DEFAULT_ANNOTATIONS: dict[str, Any] = {
    "bold": False,
    "italic": False,
    "strikethrough": False,
    "underline": False,
    "code": False,
    "color": "default",
}


def _norm_rich_text(rich_text: Any) -> list[Any]:
    """Canonicalize a ``rich_text`` array to content-only triples.

    Each span becomes ``[content, link, non_default_annotations]``. ``content``
    prefers ``text.content`` (what the converter emits) and falls back to
    ``plain_text`` (what the API returns); ``link`` unifies ``text.link.url`` and
    the API's ``href``; annotations keep only non-default flags.
    """
    out: list[Any] = []
    for span in rich_text or []:
        if not isinstance(span, dict):
            out.append(span)
            continue
        raw_text = span.get("text")
        text = raw_text if isinstance(raw_text, dict) else {}
        content = text.get("content")
        if content is None:
            content = span.get("plain_text", "")
        link = text.get("link")
        if isinstance(link, dict):
            link = link.get("url")
        if link is None:
            link = span.get("href")
        ann = span.get("annotations") or {}
        non_default = sorted(
            (k, v) for k, v in ann.items() if _DEFAULT_ANNOTATIONS.get(k) != v
        )
        out.append([content, link or None, non_default])
    return out


def _norm_body(body: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a block's type-body: drop children + render-neutral defaults."""
    out: dict[str, Any] = {}
    for key, value in body.items():
        if key == "children":
            continue
        if key == "rich_text":
            out[key] = _norm_rich_text(value)
        elif key == "color" and value == "default":
            continue
        elif key == "is_toggleable" and value is False:
            continue
        else:
            out[key] = value
    return out


def _content_key(block: dict[str, Any], child_keys: list[str]) -> str:
    """A canonical, id-free content key for *block* given its children's keys.

    The key is a fixed-size sha256 **digest**, not the raw JSON. Each child's key
    is already a digest, so the JSON serialized at this level is O(own content +
    number of children) — never the full descendant subtree. Digesting at every
    level keeps total work O(tree size); embedding raw child JSON instead would
    re-escape every descendant key at every level (O(2^depth) — an OOM on deeply
    nested pages reachable via the public API). Equality is preserved: identical
    content yields identical digests at every level.
    """
    btype = block.get("type", "")
    body = block.get(btype)
    norm = _norm_body(body) if isinstance(body, dict) else {}
    raw = json.dumps([btype, norm, child_keys], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _subtree_key(root: dict[str, Any], get_children: Any) -> str:
    """Content key for *root*'s whole subtree, computed iteratively.

    ``get_children(block)`` yields a block's child list (inline for new blocks;
    a live API fetch for existing ones). Iterative post-order (explicit stack)
    so a deep linear-chain page cannot raise ``RecursionError`` — consistent with
    the ISS-019 de-recursion of the publish planner.
    """
    keys: dict[int, str] = {}
    # Each frame: (node, next_child_index, children, accumulated_child_keys).
    stack: list[tuple[dict[str, Any], int, list[dict[str, Any]], list[str]]] = [
        (root, 0, list(get_children(root)), [])
    ]
    while stack:
        node, idx, children, acc = stack[-1]
        if idx < len(children):
            child = children[idx]
            stack[-1] = (node, idx + 1, children, acc)
            stack.append((child, 0, list(get_children(child)), []))
        else:
            key = _content_key(node, acc)
            keys[id(node)] = key
            stack.pop()
            if stack:
                stack[-1][3].append(key)
    return keys[id(root)]


def _new_subtree_key(block: dict[str, Any]) -> str:
    """Content key for a new (inline-children) block subtree."""
    return _subtree_key(block, _children_of)


def _existing_subtree_key(client: Any, block: dict[str, Any]) -> str:
    """Content key for an existing block subtree, fetching children on demand."""

    def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
        if node.get("has_children") and node.get("id"):
            return _list_children_blocks(client, node["id"])
        return []

    return _subtree_key(block, _children)


def _common_prefix_len(
    client: Any,
    existing: list[dict[str, Any]],
    new_blocks: list[dict[str, Any]],
) -> int:
    """Length of the leading run of top-level blocks with identical content.

    Compares existing (API) blocks against new (converted) blocks by content key,
    stopping at the first divergence. Only the matched existing blocks have their
    child subtrees fetched (bounded work)."""
    k = 0
    limit = min(len(existing), len(new_blocks))
    while k < limit:
        if _existing_subtree_key(client, existing[k]) != _new_subtree_key(
            new_blocks[k]
        ):
            break
        k += 1
    return k


def _is_already_gone(exc: Exception) -> bool:
    """True if *exc* says the block is already archived/absent (HTTP 404 /
    ``object_not_found``).

    Deleting a block that is already gone is a no-op, not a failure — duck-typed
    across the notion-client ``APIResponseError`` (``.status`` / ``.code``) and an
    httpx ``HTTPStatusError`` (``.response.status_code``) so the publisher need not
    import either error type.
    """
    if getattr(exc, "status", None) == 404:
        return True
    code = getattr(exc, "code", None)
    if str(getattr(code, "value", code)) == "object_not_found":
        return True
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 404:
        return True
    return False


def _delete_block(client: Any, block_id: str) -> None:
    """Archive (delete) a single block, retry-wrapped.

    A 404 (already archived / absent) is tolerated as a no-op (nops-cycle-1-HL-3):
    ``_should_retry`` covers only 429/503, so without this a non-transient 404 on
    an already-archived block would abort the whole republish mid-clear. Any other
    error still propagates.
    """

    @retry_on_transient
    def _delete(bid: str) -> None:
        client.api.blocks.delete(block_id=bid)

    try:
        _delete(block_id)
    except Exception as exc:
        if _is_already_gone(exc):
            logger.debug(
                "Block %s already archived/absent on delete; treating as no-op.",
                block_id,
            )
            return
        raise


def republish_block_tree(
    client: Any,
    parent_id: str,
    blocks: list[dict[str, Any]],
    *,
    allow_destructive: bool = False,
    protected: Callable[[str], bool] | None = None,
    **kwargs: Any,
) -> RepublishResult:
    """Idempotently (re)publish *blocks* under *parent_id* with a minimal-write diff.

    Unlike :func:`publish_block_tree`, which always **appends** (so calling it
    twice duplicates content), this converges *parent_id*'s top-level children to
    *blocks*. Running it repeatedly with the same input converges to the same page
    content — exactly what a re-run of an atom/report publish needs (Notion has no
    native "replace children" op).

    **Minimal-write (nops-cycle-2, closes nops-cycle-1-HL-1).** A content diff
    avoids rewriting blocks that have not changed:

    - **Identical content ⇒ zero writes.** If the page's current top-level
      subtrees hash equal to *blocks*, no append or delete is made and the
      existing block ids (and their comments) are preserved. (Detecting this
      trades writes for reads: the diff lists the existing top level and fetches
      the subtrees of the matched prefix, so a no-op republish still makes O(n)
      read calls — but zero writes.)
    - **Unchanged leading blocks keep their ids.** The longest common prefix of
      unchanged top-level blocks is left in place; only the divergent suffix is
      rewritten. Interior edits rewrite the divergent suffix (a full mid-list,
      id-stable LCS diff is a future optimization — routed forward).

    **Atomic-safe ordering (closes nops-cycle-1-HL-2).** The new suffix is
    appended **before** any old block is deleted (publish-before-delete). An
    interruption between the two phases therefore leaves the page with valid
    content (the kept prefix and/or the freshly published suffix) — never the
    empty page that the v0.1.0 clear-first order could leave. A re-run converges.
    (Notion still exposes no multi-block transaction, so a duplicate suffix can
    linger after an interruption until the next republish prunes it.)

    ``**kwargs`` are forwarded to :func:`publish_block_tree` (the limit knobs).
    """
    parent = extract_notion_id(parent_id)
    existing = _list_children_blocks(client, parent)
    existing_ids = [b["id"] for b in existing]

    prefix_len = _common_prefix_len(client, existing, blocks)
    prefix_ids = existing_ids[:prefix_len]
    old_suffix_ids = existing_ids[prefix_len:]
    new_suffix = blocks[prefix_len:]

    # Identical content falls through here with empty suffixes: nothing is
    # published and nothing is deleted, and every existing id is kept in the
    # prefix — i.e. zero writes, no special-case needed. (A no-op early return
    # was removed after mutation testing proved it redundant: deleting it broke
    # no test because this general path already delivers the zero-write result.)
    #
    # Publish-before-delete: land the new suffix first (appends at the end),
    # then archive the old suffix. The page is never empty mid-operation.
    if new_suffix:
        published = publish_block_tree(client, parent, new_suffix, **kwargs)
    else:
        published = PublishResult(request_count=0, top_level_block_ids=[])

    for block_id in old_suffix_ids:
        _delete_block(client, block_id)

    return RepublishResult(
        request_count=published.request_count,
        top_level_block_ids=prefix_ids + published.top_level_block_ids,
        partial=published.partial,
        skipped_followups=published.skipped_followups,
        deleted_count=len(old_suffix_ids),
    )


def republish_markdown(
    client: Any,
    parent_id: str,
    markdown: str,
    *,
    allow_destructive: bool = False,
    protected: Callable[[str], bool] | None = None,
    **kwargs: Any,
) -> RepublishResult:
    """Convert *markdown* to blocks and idempotently republish under *parent_id*.

    ``allow_destructive`` and ``protected`` are forwarded to
    :func:`republish_block_tree`; see its docstring for the guard's contract.
    """
    return republish_block_tree(
        client,
        parent_id,
        markdown_to_blocks(markdown),
        allow_destructive=allow_destructive,
        protected=protected,
        **kwargs,
    )
