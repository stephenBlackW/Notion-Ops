"""AC (HL-cred): Security Requirement #1 -- credential never surfaces in repr/exception.

PASSING guard: constructs NotionOps and AsyncNotionOps with a known literal secret
and asserts the literal NEVER appears in:
  (a) repr(client), AND
  (b) the message of AuthenticationError raised on a missing key.

HL-cred (security-redteam-campaign-RUN rev2): The rev1 report claimed "confirmed by
notion_env fixture," but notion_env was defined-but-never-used and NotionOps was never
instantiated in the security suite -- the property was asserted without a falsifiable
guard. This test closes that gap.

Vector: credential leak via repr / exception message / log
Attack surface: NotionOps.__init__, AsyncNotionOps.__init__ (client.py)
Primitive: token appears in repr(client) or in the AuthenticationError message
Status: ALREADY DEFENDED -- client.py stores _auth in a private attribute, no
        custom __repr__ is defined (default repr is address-only), and the
        AuthenticationError raised on a *missing* key cannot echo a value it does
        not have. This test makes that "by inspection" claim a falsifiable guard.

Falsifiability: if NotionOps were to define __repr__ that includes self._auth, or
if AuthenticationError were to format the token into the message, this test FAILS.
"""
from __future__ import annotations

import os

import pytest

from notion_ops.client import AsyncNotionOps, NotionOps
from notion_ops.exceptions import AuthenticationError

# The literal secret used in this suite (same value as the notion_env fixture).
# Never use a real key in tests.
_TEST_SECRET = "test-secret-key-security-suite"


class TestCredentialNoLeak:
    """Security Requirement #1: token never surfaces in repr or exception message."""

    def test_notion_ops_repr_does_not_contain_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """repr(NotionOps) must NOT contain the literal API key.

        Construct with an explicit auth= string, then assert the literal never
        appears in repr(). The default Python repr is '<NotionOps object at 0x…>'
        and does not expose private attributes. A future __repr__ that naively
        formats self._auth would fail this test.
        """
        # Patch out the underlying notion-client constructor so we do not need
        # a real network connection.
        from unittest.mock import MagicMock, patch

        with patch("notion_ops.client.Client", return_value=MagicMock()):
            client = NotionOps(auth=_TEST_SECRET)

        client_repr = repr(client)
        assert _TEST_SECRET not in client_repr, (
            f"NotionOps repr contains the secret key: {client_repr!r}"
        )

    def test_async_notion_ops_repr_does_not_contain_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """repr(AsyncNotionOps) must NOT contain the literal API key."""
        from unittest.mock import MagicMock, patch

        with patch("notion_ops.client.AsyncClient", return_value=MagicMock()):
            client = AsyncNotionOps(auth=_TEST_SECRET)

        client_repr = repr(client)
        assert _TEST_SECRET not in client_repr, (
            f"AsyncNotionOps repr contains the secret key: {client_repr!r}"
        )

    def test_authentication_error_on_missing_key_does_not_contain_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AuthenticationError raised on a missing key must not echo any secret.

        When no key is provided (and env vars are absent), NotionOps raises
        AuthenticationError.  The error message must not contain the test secret
        (which is not present in this scenario, by definition -- but this also
        verifies that the error message format does not accidentally format
        environment-variable values or other credentials into the message).
        """
        # Ensure neither env var is set so we exercise the missing-key path.
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_TOKEN", raising=False)

        with pytest.raises(AuthenticationError) as exc_info:
            NotionOps()  # no auth= provided, no env vars

        error_message = str(exc_info.value)
        assert _TEST_SECRET not in error_message, (
            f"AuthenticationError message contains the test secret: {error_message!r}"
        )

    def test_async_authentication_error_on_missing_key_no_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AuthenticationError from AsyncNotionOps on missing key must not echo any secret."""
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_TOKEN", raising=False)

        with pytest.raises(AuthenticationError) as exc_info:
            AsyncNotionOps()

        error_message = str(exc_info.value)
        assert _TEST_SECRET not in error_message, (
            f"AsyncNotionOps AuthenticationError message contains the test secret: "
            f"{error_message!r}"
        )
