"""Tests for the generic notion_ops.templates.PageTemplate engine (ao-cycle-2).

Pure/DB-agnostic: a fake client (no network) exposes ``pages.create(...)``
returning an object with ``.id``, and ``api.blocks.children.append(...)``.
"""

from unittest.mock import MagicMock

import pytest

from notion_ops.models.properties import SelectProperty, TitleProperty
from notion_ops.templates import PageTemplate


@pytest.fixture
def fake_client():
    """A NotionOps-like fake exposing pages.create + api.blocks.children.append."""
    client = MagicMock()

    page = MagicMock()
    page.id = "abc-123-def-456"
    client.pages.create.return_value = page

    client.api.blocks.children.append.return_value = {}
    return client


class TestPageTemplateInstantiate:
    def test_pagetemplate_creates_page_with_properties_and_body(self, fake_client):
        """T-01: page created in the given db with merged props + title; body batched."""
        template = PageTemplate(
            database_id="my-db-id",
            title_property="Name",
            static_properties={"Type": SelectProperty(value="Doc")},
        )

        result = template.instantiate(
            fake_client,
            title="Hello",
            properties={"Status": SelectProperty(value="Draft")},
            body_markdown="## Heading\n\nSome body text.",
        )

        # Page created in the given database.
        fake_client.pages.create.assert_called_once()
        _, kwargs = fake_client.pages.create.call_args
        assert kwargs["parent_id"] == "my-db-id"
        assert kwargs["parent_type"] == "database"

        props = kwargs["properties"]
        # Static property preserved.
        assert isinstance(props["Type"], SelectProperty)
        assert props["Type"].value == "Doc"
        # Per-call property merged in.
        assert isinstance(props["Status"], SelectProperty)
        assert props["Status"].value == "Draft"
        # Title property set on the configured title_property.
        assert isinstance(props["Name"], TitleProperty)
        assert props["Name"].value == "Hello"

        # Body was appended (at least once). The publisher normalizes the
        # parent id (extract_notion_id strips dashes), so the append targets
        # the dashless page id.
        assert fake_client.api.blocks.children.append.called
        append_kwargs = fake_client.api.blocks.children.append.call_args.kwargs
        assert append_kwargs["block_id"] == "abc123def456"
        assert isinstance(append_kwargs["children"], list)
        assert len(append_kwargs["children"]) >= 1

        # Result shape.
        assert result["page_id"] == "abc-123-def-456"
        assert result["id"] == result["page_id"]
        assert result["url"] == "https://notion.so/abc123def456"
        assert result["title"] == "Hello"
        assert result["content_error"] is None

    def test_pagetemplate_per_call_property_overrides_static(self, fake_client):
        """Per-call properties win over static_properties key-by-key."""
        template = PageTemplate(
            database_id="db",
            static_properties={"Type": SelectProperty(value="Doc")},
        )
        template.instantiate(
            fake_client,
            title="T",
            properties={"Type": SelectProperty(value="Spec")},
        )
        props = fake_client.pages.create.call_args.kwargs["properties"]
        assert props["Type"].value == "Spec"

    def test_pagetemplate_content_error_nonfatal(self, fake_client):
        """T-02: content_error non-fatal when append raises; page_id still returned."""
        fake_client.api.blocks.children.append.side_effect = Exception(
            "503 Service Temporarily Unavailable"
        )

        template = PageTemplate(database_id="db")
        result = template.instantiate(
            fake_client,
            title="Fails on append",
            body_markdown="## Hi\n\ncontent",
        )

        # Page id still present; error captured non-fatally.
        assert result["page_id"] == "abc-123-def-456"
        assert result["content_error"] is not None
        assert "503" in result["content_error"]

    def test_pagetemplate_variable_substitution(self, fake_client):
        """T-03: {var} placeholders in the body are substituted from variables."""
        template = PageTemplate(
            database_id="db",
            body_markdown="Hello {name}, welcome to {place}.",
        )
        template.instantiate(
            fake_client,
            title="T",
            variables={"name": "Ada", "place": "Notion"},
        )

        # Inspect the appended block content for the substituted text.
        children = fake_client.api.blocks.children.append.call_args.kwargs["children"]
        rendered = str(children)
        assert "Ada" in rendered
        assert "Notion" in rendered
        assert "{name}" not in rendered
        assert "{place}" not in rendered

    def test_pagetemplate_unknown_variable_left_intact(self, fake_client):
        """Unknown {placeholders} are left verbatim (safe substitution)."""
        template = PageTemplate(
            database_id="db",
            body_markdown="Known {known} unknown {missing}",
        )
        template.instantiate(
            fake_client,
            title="T",
            variables={"known": "yes"},
        )
        children = fake_client.api.blocks.children.append.call_args.kwargs["children"]
        rendered = str(children)
        assert "yes" in rendered
        assert "{missing}" in rendered

    def test_pagetemplate_body_override_wins(self, fake_client):
        """Per-call body_markdown overrides the template default."""
        template = PageTemplate(database_id="db", body_markdown="default body")
        template.instantiate(fake_client, title="T", body_markdown="override body")
        children = fake_client.api.blocks.children.append.call_args.kwargs["children"]
        rendered = str(children)
        assert "override body" in rendered
        assert "default body" not in rendered

    def test_pagetemplate_empty_body_skips_append(self, fake_client):
        """No body -> no block append attempted; success result returned."""
        template = PageTemplate(database_id="db")
        result = template.instantiate(fake_client, title="T")
        fake_client.api.blocks.children.append.assert_not_called()
        assert result["content_error"] is None
        assert result["page_id"] == "abc-123-def-456"


class TestPageTemplateISS017:
    """ISS-017: PageTemplate routes body publishing through the limit-aware
    publish_block_tree instead of an ad-hoc flat batcher."""

    def test_body_routed_through_publish_block_tree(self, monkeypatch):
        """Delegation binding: instantiate() calls publish_block_tree with the
        page id and the converted blocks, and does NOT hand-roll appends."""
        import notion_ops.templates as templates_mod
        from notion_ops.utils.markdown import markdown_to_blocks
        from notion_ops.utils.publish import PublishResult

        client = MagicMock()
        page = MagicMock()
        page.id = "abc-123-def-456"
        client.pages.create.return_value = page

        captured: dict = {}

        def _spy(c, parent_id, blocks, **kwargs):
            captured["parent_id"] = parent_id
            captured["blocks"] = blocks
            return PublishResult(request_count=1, top_level_block_ids=[])

        monkeypatch.setattr(templates_mod, "publish_block_tree", _spy)

        body = "## Heading\n\nSome body text."
        result = PageTemplate(database_id="db").instantiate(
            client, title="T", body_markdown=body
        )

        assert captured["parent_id"] == "abc-123-def-456"
        assert captured["blocks"] == markdown_to_blocks(body)
        # The template must not bypass the publisher with raw appends.
        client.api.blocks.children.append.assert_not_called()
        assert result["content_error"] is None

    def test_large_table_split_across_requests(self):
        """Behavioral: a >100-row table is split into multiple limit-aware
        appends. The old flat batcher emitted it as a single over-limit request
        (Notion caps a request at 100 children)."""
        client = MagicMock()
        page = MagicMock()
        page.id = "abc-123-def-456"
        client.pages.create.return_value = page

        def _append(block_id, children):
            # Echo an id per appended child so deferred follow-ups can resolve.
            return {"results": [{"id": f"{block_id}-{i}"} for i in range(len(children))]}

        client.api.blocks.children.append.side_effect = _append

        body = "| H1 | H2 |\n| --- | --- |\n" + "".join(
            f"| r{i}a | r{i}b |\n" for i in range(150)
        )
        result = PageTemplate(database_id="db").instantiate(
            client, title="T", body_markdown=body
        )

        assert result["content_error"] is None
        # Table split: first rows inline with the table, the rest deferred.
        assert client.api.blocks.children.append.call_count >= 2


def test_publisher_promoted_to_top_level_exports():
    """audit F2: the publisher is the headline API — importable from the package
    root and listed in __all__."""
    import notion_ops

    for name in ("publish_block_tree", "publish_markdown", "PublishResult"):
        assert hasattr(notion_ops, name), f"{name} not importable from notion_ops"
        assert name in notion_ops.__all__, f"{name} missing from __all__"
