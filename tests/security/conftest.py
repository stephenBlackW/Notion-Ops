"""Security test suite conftest.py -- self-contained spoke copy (HL-F).

This file is a self-contained copy of the AOS security suite conftest.
It does NOT import from any AOS path. The spoke CI runs this suite standalone
via `python -m pytest tests/security/ -v` from vendor/notion-ops/.

Provides:
  - Hypothesis CI profile (bounded, deterministic)
  - FakeClient (mocked Notion client for append-only recording)
  - _block_network autouse fixture (blocks real transport + socket)
  - notion_env fixture (patches NOTION_API_KEY)
"""
from __future__ import annotations

import socket
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from hypothesis import HealthCheck, settings

# ---------------------------------------------------------------------------
# Hypothesis CI profile -- bounded, deterministic, non-flaky
# ---------------------------------------------------------------------------
# Re-running twice with the same codebase produces identical results because
# derandomize=True fixes the PRNG seed (hash of test name + settings).
# deadline=None removes per-test deadline flake.
settings.register_profile(
    "ci",
    max_examples=50,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("ci")


# ---------------------------------------------------------------------------
# HL-C: Autouse network-block fixture
# ---------------------------------------------------------------------------

class NetworkBlockedError(RuntimeError):
    """Raised when a test attempts a real network connection."""


def _blocked_socket_connect(self: Any, address: Any) -> None:
    host = address[0] if isinstance(address, tuple) else address
    port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
    raise NetworkBlockedError(
        f"Real socket.connect() blocked in security suite: {host}:{port}. "
        "All notion_ops publish calls must go through FakeClient."
    )


def _blocked_create_connection(address: Any, *args: Any, **kwargs: Any) -> None:
    host = address[0] if isinstance(address, tuple) else address
    port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
    raise NetworkBlockedError(
        f"Real socket.create_connection() blocked in security suite: {host}:{port}."
    )


def _blocked_httpx_handle(self: Any, request: Any) -> None:
    raise NetworkBlockedError(
        f"Real httpx transport.handle_request() blocked: "
        f"{request.method} {request.url}. "
        "notion_ops must not make any outbound HTTP calls in the security suite."
    )


@pytest.fixture(autouse=True)
def _block_network() -> Any:
    """Autouse: block ALL real outbound network I/O for every security test."""
    with (
        patch.object(socket.socket, "connect", _blocked_socket_connect),
        patch("socket.create_connection", _blocked_create_connection),
        patch.object(httpx.BaseTransport, "handle_request", _blocked_httpx_handle),
    ):
        yield


# ---------------------------------------------------------------------------
# FakeClient -- records append calls, makes NO real network calls
# ---------------------------------------------------------------------------

class FakeClient:
    """Mocked Notion client for security tests.

    Self-contained: does not import from AOS. Mirrors the FakeClient pattern
    from tests/test_publish.py but is simpler (append-only, deterministic IDs).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._n = 0

        client = self

        class _Append:
            def append(
                self, *, block_id: str, children: list[dict[str, Any]]
            ) -> dict[str, Any]:
                base = client._n
                client._n += 1
                client.calls.append({"block_id": block_id, "children": children})
                return {
                    "results": [
                        {"id": f"blk-{base}-{i}", "type": c.get("type")}
                        for i, c in enumerate(children)
                    ]
                }

        class _Blocks:
            children = _Append()

        class _API:
            blocks = _Blocks()

        self.api = _API()


@pytest.fixture
def fake_client() -> FakeClient:
    """A FakeClient instance that records calls and makes no real network requests."""
    return FakeClient()


@pytest.fixture
def notion_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch NOTION_API_KEY with a test value so client construction does not fail."""
    monkeypatch.setenv("NOTION_API_KEY", "test-secret-key-security-suite")
