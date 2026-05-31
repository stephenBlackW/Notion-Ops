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
