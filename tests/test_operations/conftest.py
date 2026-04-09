"""Shared fixtures for operation tests, providing both sync and async clients."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notion_ops.client import AsyncNotionOps, NotionOps

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def maybe_await(result: Any) -> Any:
    """Await coroutines, return plain values as-is."""
    if inspect.isawaitable(result):
        return await result
    return result


async def collect_iter(iterable: Iterator | AsyncIterator) -> list:
    """Materialise a sync or async iterator into a list."""
    if isinstance(iterable, AsyncIterator):
        items = []
        async for item in iterable:
            items.append(item)
        return items
    return list(iterable)


# ---------------------------------------------------------------------------
# Parametrised client fixture
# ---------------------------------------------------------------------------


class ClientBundle:
    """Wraps a NotionOps/AsyncNotionOps instance with convenience metadata."""

    def __init__(self, client: NotionOps | AsyncNotionOps, *, is_async: bool):
        self.client = client
        self.is_async = is_async

    # Proxy attribute access to the underlying client for ergonomics.
    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def setup_mock(self, attr_chain: str, **kwargs: Any) -> None:
        """Set up a mock on the underlying _notion client.

        For async clients the target attribute is replaced with an AsyncMock;
        for sync clients we just set side_effect / return_value directly.

        ``attr_chain`` is a dot-separated path, e.g.
        ``"pages.retrieve"`` or ``"blocks.children.list"``.
        """
        parts = attr_chain.split(".")
        target = self.client._notion
        for part in parts[:-1]:
            target = getattr(target, part)

        method_name = parts[-1]

        if self.is_async:
            setattr(target, method_name, AsyncMock(**kwargs))
        else:
            mock_method = getattr(target, method_name)
            for key, value in kwargs.items():
                setattr(mock_method, key, value)

    def get_mock(self, attr_chain: str) -> Any:
        """Return the mock object at *attr_chain*."""
        obj = self.client._notion
        for part in attr_chain.split("."):
            obj = getattr(obj, part)
        return obj


@pytest.fixture(params=["sync", "async"])
def ops(request):
    """Provide a ``ClientBundle`` for both sync and async paths."""
    if request.param == "sync":
        with patch.dict("os.environ", {"NOTION_API_KEY": "test-secret-key"}):
            with patch("notion_ops.client.Client"):
                client = NotionOps()
                mock = MagicMock()
                mock.pages = MagicMock()
                mock.pages.properties = MagicMock()
                mock.blocks = MagicMock()
                mock.blocks.children = MagicMock()
                mock.databases = MagicMock()
                mock.users = MagicMock()
                mock.search = MagicMock()
                mock.request = MagicMock()
                client._notion = mock
                return ClientBundle(client, is_async=False)
    else:
        with patch.dict("os.environ", {"NOTION_API_KEY": "test-key"}):
            with patch("notion_ops.client.AsyncClient"):
                client = AsyncNotionOps()
                mock = AsyncMock()
                mock.pages = AsyncMock()
                mock.pages.properties = AsyncMock()
                mock.blocks = AsyncMock()
                mock.blocks.children = AsyncMock()
                mock.databases = AsyncMock()
                mock.users = AsyncMock()
                mock.search = AsyncMock()
                mock.request = AsyncMock()
                client._notion = mock
                return ClientBundle(client, is_async=True)
