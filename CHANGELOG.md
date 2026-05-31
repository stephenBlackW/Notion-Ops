# Changelog

All notable changes to `notion-ops` are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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
