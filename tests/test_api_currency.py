"""API-currency contract (nops-housekeeping-A, 2026-05-31).

notion-ops targets Notion's data-sources API: it defaults the
``Notion-Version`` header to ``2025-09-03`` (the databases -> data_sources
model) and relies on the underlying SDK's ``data_sources`` endpoint namespace.

These tests BIND that contract so a regression fails loudly — e.g. a
default-version downgrade, or a too-old ``notion-client`` (the dependency
floor predates data-sources support) that lacks the ``data_sources``
namespace. Construction is offline (no network), so a real client is used
rather than a mock, which would make the namespace assertion vacuous.
"""

import inspect

import notion_ops
from notion_ops.client import AsyncNotionOps, NotionOps

EXPECTED_NOTION_VERSION = "2025-09-03"


def test_sync_client_defaults_to_data_sources_api_version():
    client = NotionOps(auth="test-secret-key")
    assert client._notion_version == EXPECTED_NOTION_VERSION


def test_async_client_defaults_to_data_sources_api_version():
    client = AsyncNotionOps(auth="test-secret-key")
    assert client._notion_version == EXPECTED_NOTION_VERSION


def test_constructor_default_is_data_sources_version():
    # The default is part of the public contract, independent of construction.
    for cls in (NotionOps, AsyncNotionOps):
        default = inspect.signature(cls).parameters["notion_version"].default
        assert default == EXPECTED_NOTION_VERSION


def test_underlying_sdk_exposes_data_sources_namespace():
    # Enforces the notion-client>=2.4.0 floor at runtime: the data_sources
    # endpoint family must exist on the wrapped SDK client.
    client = NotionOps(auth="test-secret-key")
    assert hasattr(client.api, "data_sources")


# ---------------------------------------------------------------------------
# AC-12 (nops-cycle-3) — the published export surface
# ---------------------------------------------------------------------------
#
# `notion_ops/__init__.py` is the spoke's declared `public_surface`, so what it
# exports is a contract, not an implementation detail. This cycle ADDS four
# names and removes none, and leaves both republish functions callable exactly as
# a pre-cycle caller called them.

#: Everything `__all__` carried before nops-cycle-3. None of it may disappear.
_PRE_CYCLE_EXPORTS = frozenset(
    {
        "NotionOps", "AsyncNotionOps", "Page", "PageCreate", "Database",
        "Block", "Blocks", "BlockType", "Filter", "Sort",
        "PropertyType", "PropertyValue", "PropertyDefinition",
        "TitleProperty", "RichTextProperty", "NumberProperty", "SelectProperty",
        "MultiSelectProperty", "DateProperty", "CheckboxProperty", "URLProperty",
        "EmailProperty", "PeopleProperty", "RelationProperty", "StatusProperty",
        "FilesProperty", "PhoneProperty",
        "NotionOpsError", "NotFoundError", "ValidationError", "AuthenticationError",
        "PermissionError", "RateLimitError", "ConflictError",
        "PageTemplate", "markdown_to_blocks", "blocks_to_markdown", "repair_blocks",
        "extract_notion_id",
        "publish_block_tree", "publish_markdown", "PublishResult",
        "republish_block_tree", "republish_markdown", "RepublishResult",
    }
)

#: Exactly what nops-cycle-3 adds.
_CYCLE_ADDITIONS = frozenset(
    {"revise_page", "RevisionSchema", "RevisionResult", "DestructiveRepublishError"}
)


def test_export_surface_gains_exactly_the_cycle_additions():
    exported = set(notion_ops.__all__)
    assert _PRE_CYCLE_EXPORTS <= exported, (
        "an export was REMOVED from the public surface: "
        f"{sorted(_PRE_CYCLE_EXPORTS - exported)}"
    )
    assert exported == _PRE_CYCLE_EXPORTS | _CYCLE_ADDITIONS, (
        "unexpected change to the public surface: "
        f"{sorted(exported ^ (_PRE_CYCLE_EXPORTS | _CYCLE_ADDITIONS))}"
    )


def test_every_exported_name_resolves():
    missing = [name for name in notion_ops.__all__ if not hasattr(notion_ops, name)]
    assert missing == [], f"__all__ names nothing importable: {missing}"


def test_republish_positional_signature_is_unchanged():
    """A pre-cycle call site passes three positional arguments and no keywords."""
    params = list(inspect.signature(notion_ops.republish_block_tree).parameters.values())
    positional = [
        p.name for p in params if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    assert positional == ["client", "parent_id", "blocks"]

    for func in (notion_ops.republish_block_tree, notion_ops.republish_markdown):
        kwdefaults = func.__kwdefaults__ or {}
        # The new knobs are keyword-only with safe defaults, so nothing that
        # compiled before this cycle changes meaning.
        assert kwdefaults["allow_destructive"] is False
        assert kwdefaults["protected"] is None


def test_the_comments_surface_is_registered_on_both_clients():
    sync = NotionOps(auth="test-secret-key")
    asyn = AsyncNotionOps(auth="test-secret-key")
    for client in (sync, asyn):
        assert hasattr(client, "comments")
        assert hasattr(client.comments, "list")
        assert hasattr(client.comments, "has_discussion")
