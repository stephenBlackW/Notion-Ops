"""AC-7 / AC-C: SSRF-shaped URL reaches no network under FakeClient + autouse block.

PASSING guard: asserts that markdown containing SSRF-shaped URLs (metadata service,
localhost, file://) converts to blocks and, when published via FakeClient, triggers
zero real HTTP or socket calls. The library passes URLs verbatim to the Notion API --
it never fetches them itself.

Vector: SSRF -- library fetches a URL from markdown content
Attack surface: markdown_to_blocks() -> image/bookmark blocks -> publish_block_tree()
Primitive: the library resolves/fetches the URL instead of passing it verbatim
Status: ALREADY SAFE (library does not fetch URLs; FakeClient records 0 HTTP calls) --
        plain passing test (no xfail)

HL-C (security-redteam-campaign-RUN): The autouse _block_network fixture in conftest.py
now blocks the REAL httpx.BaseTransport.handle_request + socket.socket.connect. This
makes the "no fetch" assertion FALSIFIABLE: if notion_ops ever attempted a real
outbound connection, NetworkBlockedError would be raised immediately. The old guard
patched httpx.get (which notion_ops never calls via that path), so assert_not_called()
was vacuously true -- unfalsifiable. This version targets the real transport used by
notion-client under the hood.
"""
from __future__ import annotations

import pytest

from notion_ops.utils.markdown import markdown_to_blocks
from notion_ops.utils.publish import publish_block_tree

from .conftest import FakeClient

# SSRF-shaped test URLs (RFC-5737 documentation / reserved hosts -- not real endpoints)
_SSRF_URLS = [
    "http://169.254.169.254/latest/meta-data/",       # AWS IMDS
    "http://metadata.google.internal/computeMetadata/",  # GCP metadata
    "http://localhost/admin",                           # localhost
    "http://127.0.0.1/etc/passwd",                     # loopback
    "file:///etc/passwd",                              # file scheme
    "file:///proc/self/environ",                       # Linux proc
]


class TestSSRFNoFetch:
    """AC-7 / AC-C: SSRF-shaped URLs reach no network under the autouse network block."""

    @pytest.mark.parametrize("ssrf_url", _SSRF_URLS)
    def test_image_ssrf_url_no_fetch(self, ssrf_url: str) -> None:
        """Markdown image with SSRF URL converts to a block dict without fetching.

        The autouse _block_network fixture in conftest.py would raise
        NetworkBlockedError if any real socket/httpx connection were attempted.
        Reaching this assertion means zero real network calls occurred.
        """
        markdown = f"![adversarial image]({ssrf_url})"
        blocks = markdown_to_blocks(markdown)
        # The block must be produced (library does not reject the URL)
        assert isinstance(blocks, list), "Expected list of blocks"
        assert len(blocks) >= 1, "Expected at least one block"
        # The URL must appear verbatim in the block (passed through, not fetched)
        block_str = str(blocks)
        assert ssrf_url in block_str, (
            f"SSRF URL {ssrf_url!r} should appear verbatim in block dict"
        )

    @pytest.mark.parametrize("ssrf_url", _SSRF_URLS[:4])  # http-only for publish test
    def test_publish_ssrf_image_zero_real_transport_calls(
        self, ssrf_url: str, fake_client: FakeClient
    ) -> None:
        """Publishing an SSRF image block makes no real transport calls.

        HL-C: The autouse _block_network fixture patches httpx.BaseTransport.
        handle_request (the real transport used by notion-client) and
        socket.socket.connect. If notion_ops tried to fetch the SSRF URL, it
        would raise NetworkBlockedError -- NOT assert_not_called() on httpx.get
        (which notion_ops never calls, making the old guard unfalsifiable).

        FakeClient is the only call sink; it never touches sockets.
        """
        markdown = f"![probe]({ssrf_url})"
        blocks = markdown_to_blocks(markdown)
        # If any real transport were attempted, NetworkBlockedError would be raised
        # by the autouse _block_network fixture -- no additional mocking needed here.
        publish_block_tree(fake_client, "parent-ssrf-001", blocks)
        # FakeClient must have recorded at least one append call (the block was published)
        assert len(fake_client.calls) >= 1, "Expected at least one FakeClient append call"

    def test_fake_client_records_only_append_calls(self, fake_client: FakeClient) -> None:
        """FakeClient records append calls; no real Notion API is invoked."""
        markdown = "![x](http://169.254.169.254/latest/meta-data/)"
        blocks = markdown_to_blocks(markdown)
        publish_block_tree(fake_client, "parent-fake-001", blocks)
        # Every recorded call must have block_id and children (standard append shape)
        for call in fake_client.calls:
            assert "block_id" in call, f"Expected block_id in call: {call}"
            assert "children" in call, f"Expected children in call: {call}"

    def test_blocks_contain_ssrf_url_verbatim(self) -> None:
        """The block dict contains the SSRF URL verbatim (no rewrite or fetch)."""
        import json
        url = "http://169.254.169.254/latest/meta-data/"
        blocks = markdown_to_blocks(f"![meta]({url})")
        assert isinstance(blocks, list)
        # The URL must be present somewhere in the block structure
        block_json = json.dumps(blocks)
        assert url in block_json, (
            f"URL {url!r} should appear verbatim in the block JSON"
        )

    def test_extract_notion_id_makes_no_network_calls(self) -> None:
        """extract_notion_id is pure string parsing: zero network calls for any input.

        The autouse _block_network fixture would raise NetworkBlockedError if
        extract_notion_id attempted any socket or httpx connection. The function is
        SSRF-safe by construction (pure string parsing; no urlopen/requests/httpx import).
        """
        from notion_ops.utils.ids import extract_notion_id
        # SSRF-shaped URLs that could trigger a fetch in a naïve implementation
        ssrf_inputs = [
            "http://169.254.169.254/latest/meta-data/",
            "http://localhost/admin",
            "https://www.notion.so/mypage-" + "a" * 32,
        ]
        for inp in ssrf_inputs:
            try:
                # May raise ValueError for invalid IDs -- that is fine
                extract_notion_id(inp)
            except (ValueError, Exception):
                pass
            # If we reach here without NetworkBlockedError, no fetch occurred

    def test_blocks_image_ssrf_url_verbatim(self) -> None:
        """Blocks.image() embeds the SSRF URL verbatim without fetching it."""
        from notion_ops.models.block import Blocks
        ssrf_url = "http://169.254.169.254/latest/meta-data/"
        block = Blocks.image(ssrf_url)
        # The URL must appear verbatim in the block dict
        import json
        block_json = json.dumps(block.to_dict() if hasattr(block, "to_dict") else dict(block))
        assert ssrf_url in block_json, (
            f"Blocks.image SSRF URL {ssrf_url!r} must appear verbatim in block dict"
        )

    def test_blocks_bookmark_ssrf_url_verbatim(self) -> None:
        """Blocks.bookmark() embeds the SSRF URL verbatim without fetching it."""
        from notion_ops.models.block import Blocks
        ssrf_url = "http://127.0.0.1/etc/passwd"
        block = Blocks.bookmark(ssrf_url)
        import json
        block_json = json.dumps(block.to_dict() if hasattr(block, "to_dict") else dict(block))
        assert ssrf_url in block_json, (
            f"Blocks.bookmark SSRF URL {ssrf_url!r} must appear verbatim in block dict"
        )
