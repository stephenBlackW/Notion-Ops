# Changelog

All notable changes to `notion-ops` are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Promoted the limit-aware publisher to top-level exports: `publish_block_tree`,
  `publish_markdown`, and `PublishResult` are now importable from `notion_ops`
  (and listed in `__all__`).

### Changed
- `PageTemplate` now publishes its body through `publish_block_tree` instead of
  an ad-hoc flat batcher (ISS-017), so templated bodies respect Notion's
  2-level inline-nesting limit and >100-row table splitting. Minor observable
  note: the body is appended under the page's *normalized* (dashless) id.
- Version is now single-sourced from `notion_ops/__init__.py::__version__`
  (hatchling dynamic version); `pyproject.toml` no longer duplicates it.
- Raised the `notion-client` dependency floor to `>=2.4.0` — the data-sources
  line. notion-ops defaults `Notion-Version` to `2025-09-03` and uses the
  SDK's `data_sources` namespace; the prior `>=2.3.0` floor predated it. The
  contract is now enforced by `tests/test_api_currency.py`.

### Added
- `CHANGELOG.md` (this file).
- Packaging metadata: author email and an explicit README content type.

## [0.1.0] — 2026-05-27

Initial public release, carved out of AgenticOS into the standalone
`stephenBlackW/Notion-Ops` repository.

### Added
- High-level CRUD operations for pages, databases, data sources, blocks,
  users, and file uploads, with sync (`NotionOps`) and async
  (`AsyncNotionOps`) clients.
- Type-safe Pydantic v2 models for Notion objects.
- Fluent `Filter` / `Sort` query builders and `Blocks` block builders.
- Markdown -> Notion blocks conversion (`markdown_to_blocks`).
- `publish_block_tree` / `publish_markdown`: a limit-aware publishing
  orchestrator that respects Notion's nesting, 100-children, and table-row
  caps automatically.
- Retry/backoff on transient (429/503) errors.
