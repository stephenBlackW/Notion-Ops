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

        # Body was appended (at least once).
        assert fake_client.api.blocks.children.append.called
        append_kwargs = fake_client.api.blocks.children.append.call_args.kwargs
        assert append_kwargs["block_id"] == "abc-123-def-456"
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
