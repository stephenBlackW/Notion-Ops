"""Narrow a notion-client ``SyncAsync`` result for the synchronous client.

The notion-client SDK types every endpoint as returning ``SyncAsync[T]``
(``T | Awaitable[T]``) because the same method bodies back both the sync
``Client`` and the async ``AsyncClient``. The synchronous :class:`NotionOps`
always receives the *resolved* value — never an awaitable — so narrowing the
union to the concrete response dict is sound. ``sync_dict`` is the single,
documented place that narrowing happens, keeping the sync operations
mypy-clean without scattering ``cast`` / ``# type: ignore`` across every call
site. It is a no-op at runtime.
"""

from __future__ import annotations

from typing import Any, cast


def sync_dict(result: Any) -> dict[str, Any]:
    """Return *result* narrowed to the JSON dict a sync SDK call produced."""
    return cast("dict[str, Any]", result)
