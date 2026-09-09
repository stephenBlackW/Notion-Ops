# Changelog

All notable changes to `notion-ops` are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Versioned revision (`revise_page`, closes AOS ISS-029).** Publish new content
  for a page as a **new version** instead of rewriting the page in place, so the
  old page keeps its blocks and every comment anchored to them. `RevisionSchema`
  carries the caller's own property names (every relation/status field defaults to
  `None`, meaning "skip that step"), so no workspace convention is baked into the
  library. Two modes: `new-canonical` (the default; the successor becomes the
  canonical page and the original is retitled `"(vN)"`, status-stamped and linked
  forward — the only mode that preserves comment anchors) and `snapshot-in-place`
  (an explicit opt-in for a hub page whose id other pages relate to: it keeps its
  id and is rewritten, the old body is snapshotted to a `(vN)` page, and the
  discussion is transcribed onto that snapshot before the first write, because its
  anchors cannot survive and the API cannot move them). Exported alongside
  `RevisionSchema` and `RevisionResult`.
- **Read-only comments surface.** `client.comments.list(page_or_block_id)` and
  `client.comments.has_discussion(...)`, on both the sync and async clients,
  paginated and retry-wrapped. Note the Notion endpoint returns **un-resolved**
  comments only, so `has_discussion` means "carries an *open* discussion".

### Changed
- **`republish_block_tree` / `republish_markdown` refuse a destructive rewrite by
  default.** Both grow keyword-only `allow_destructive: bool = False` and
  `protected: Callable[[str], bool] | None = None`, and raise the new
  `DestructiveRepublishError` when a republish that *would write* targets a page
  carrying an open discussion, or one the caller's `protected` predicate flags.
  The check runs **after** the content diff and **before** the first write, so an
  identical-content no-op is neither refused nor charged a comments request, and
  `allow_destructive=True` reproduces the previous behaviour exactly. The
  positional signature is unchanged, so existing call sites still compile; a call
  site that republishes over a *discussed* page will now raise, which is the fix.

  **Migration:** if you republish pages people comment on, either switch to
  `revise_page` or pass `allow_destructive=True` where the loss is intended.

## [0.1.0] — 2026-06-02

First public **PyPI** release of `notion-ops`, carved out of AgenticOS into the
standalone `stephenBlackW/Notion-Ops` repository (the repo was created
2026-05-27; this is the first release published to PyPI).

### Added
- High-level CRUD operations for pages, databases, data sources, blocks,
  users, and file uploads, with sync (`NotionOps`) and async
  (`AsyncNotionOps`) clients.
- Type-safe Pydantic v2 models for Notion objects.
- Fluent `Filter` / `Sort` query builders and `Blocks` block builders.
- Markdown → Notion blocks conversion (`markdown_to_blocks`).
- `publish_block_tree` / `publish_markdown`: a limit-aware publishing
  orchestrator that respects Notion's nesting, 100-children, and table-row
  caps automatically. Exported at top level (in `__all__`) alongside
  `PublishResult`.
- **Idempotent republish (ISS-012):** `republish_block_tree` / `republish_markdown`
  (+ `RepublishResult`) clear a page's existing top-level children, then publish the
  new tree — so re-running converges to the same content instead of duplicating it
  (Notion has no native "replace children"). Content is idempotent; block ids are not
  stable (cleared blocks are archived + recreated).
- **Partial-publish observability (HL-patchA-1):** `PublishResult` gains `partial`
  and `skipped_followups`, set when a deferred nested append can't resolve its parent
  id — callers can branch on `partial` instead of scraping logs.
- `AsyncFileUploads` — async parity for the file-upload flow (`httpx.AsyncClient`
  + `retry_on_transient_async`). `AsyncNotionOps.file_uploads` now exposes it, so
  `await client.file_uploads.upload_file(...)` no longer blocks the event loop on
  a synchronous multipart POST (audit F3).
- Retry/backoff on transient (429/503) errors.
- **Security hardening (pre-publish red-team campaign):** a `tests/security/`
  regression suite — ReDoS (amortized + catastrophic-arm), deep-recursion,
  SSRF no-fetch (real-transport block), credential no-leak, and oversized-content
  guards, plus a bounded deterministic `hypothesis` fuzz layer — wired as a
  required CI gate for the release. The campaign found and fixed **ISS-019**: a
  `RecursionError` denial-of-service in the publish planner on deep block trees,
  resolved by fully de-recursing the planner (`_height`, `_total_blocks`,
  `_max_children_count`, `_plan_append`, `execute_plan`, `count_requests` — now
  all iterative; no `sys.getrecursionlimit()` ceiling remains).

### Changed
- `FileUploads.create`/`send` now raise the same typed `NotionOpsError`
  subclasses as every SDK-backed operation (`NotFoundError`, `AuthenticationError`,
  `PermissionError`, `RateLimitError`, `ValidationError`, `ConflictError`) instead
  of a bare `NotionOpsError` carrying the status string (audit F3). A 429 maps to
  `RateLimitError` and is retried with backoff like the rest of the library.
- `PageTemplate` now publishes its body through `publish_block_tree` instead of
  an ad-hoc flat batcher (ISS-017), so templated bodies respect Notion's
  2-level inline-nesting limit and >100-row table splitting. Minor observable
  note: the body is appended under the page's *normalized* (dashless) id.
- Version is single-sourced from `notion_ops/__init__.py::__version__`
  (hatchling dynamic version).
- The `notion-client` dependency floor is `>=2.4.0` — the data-sources line.
  notion-ops defaults `Notion-Version` to `2025-09-03` and uses the SDK's
  `data_sources` namespace; the prior `>=2.3.0` floor predated it. The contract
  is enforced by `tests/test_api_currency.py`.
- License metadata uses the SPDX `license = "MIT"` expression (PEP 639) with no
  redundant `License ::` classifier.
