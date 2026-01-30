"""Tests for Notion Operations models."""

from datetime import datetime

import pytest

from notion_ops.models.block import Block, Blocks, BlockType
from notion_ops.models.filters import Filter, Sort
from notion_ops.models.page import Page, PageCreate
from notion_ops.models.properties import (
    CheckboxProperty,
    DateProperty,
    MultiSelectProperty,
    NumberProperty,
    PropertyType,
    SelectProperty,
    TitleProperty,
    parse_property_value,
)


class TestPropertyValues:
    """Tests for property value models."""

    def test_title_property_to_api_format(self):
        prop = TitleProperty(value="Test Title")
        result = prop.to_api_format()

        assert result == {"title": [{"type": "text", "text": {"content": "Test Title"}}]}

    def test_number_property_to_api_format(self):
        prop = NumberProperty(value=42)
        result = prop.to_api_format()

        assert result == {"number": 42}

    def test_select_property_to_api_format(self):
        prop = SelectProperty(value="Option A")
        result = prop.to_api_format()

        assert result == {"select": {"name": "Option A"}}

    def test_multi_select_property_to_api_format(self):
        prop = MultiSelectProperty(value=["Tag1", "Tag2"])
        result = prop.to_api_format()

        assert result == {"multi_select": [{"name": "Tag1"}, {"name": "Tag2"}]}

    def test_checkbox_property_to_api_format(self):
        prop = CheckboxProperty(value=True)
        result = prop.to_api_format()

        assert result == {"checkbox": True}

    def test_date_property_to_api_format(self):
        dt = datetime(2025, 1, 15, 12, 0, 0)
        prop = DateProperty(value=dt)
        result = prop.to_api_format()

        assert result == {"date": {"start": "2025-01-15T12:00:00"}}

    def test_date_property_with_end(self):
        start = datetime(2025, 1, 15)
        end = datetime(2025, 1, 20)
        prop = DateProperty(value=start, end=end)
        result = prop.to_api_format()

        assert "end" in result["date"]


class TestParsePropertyValue:
    """Tests for parsing property values from API responses."""

    def test_parse_title(self):
        data = {"type": "title", "title": [{"plain_text": "Test"}]}
        assert parse_property_value(data) == "Test"

    def test_parse_number(self):
        data = {"type": "number", "number": 42}
        assert parse_property_value(data) == 42

    def test_parse_select(self):
        data = {"type": "select", "select": {"name": "Option"}}
        assert parse_property_value(data) == "Option"

    def test_parse_checkbox(self):
        data = {"type": "checkbox", "checkbox": True}
        assert parse_property_value(data) is True

    def test_parse_multi_select(self):
        data = {"type": "multi_select", "multi_select": [{"name": "A"}, {"name": "B"}]}
        assert parse_property_value(data) == ["A", "B"]


class TestBlocks:
    """Tests for block creation."""

    def test_paragraph_block(self):
        block = Blocks.paragraph("Hello world")

        assert block.type == BlockType.PARAGRAPH
        assert block.content["rich_text"][0]["text"]["content"] == "Hello world"

    def test_heading_1_block(self):
        block = Blocks.heading_1("Title")

        assert block.type == BlockType.HEADING_1
        assert block.content["rich_text"][0]["text"]["content"] == "Title"

    def test_todo_block(self):
        block = Blocks.todo("Task", checked=True)

        assert block.type == BlockType.TO_DO
        assert block.content["checked"] is True

    def test_code_block(self):
        block = Blocks.code("print('hello')", language="python")

        assert block.type == BlockType.CODE
        assert block.content["language"] == "python"

    def test_callout_block(self):
        block = Blocks.callout("Note", emoji="📝")

        assert block.type == BlockType.CALLOUT
        assert block.content["icon"]["emoji"] == "📝"

    def test_divider_block(self):
        block = Blocks.divider()

        assert block.type == BlockType.DIVIDER

    def test_block_to_api_format(self):
        block = Blocks.paragraph("Test")
        result = block.to_api_format()

        assert result["type"] == "paragraph"
        assert "paragraph" in result


class TestFilters:
    """Tests for query filter builders."""

    def test_text_filter_equals(self):
        result = Filter.title("Name").equals("Test")

        assert result == {"property": "Name", "title": {"equals": "Test"}}

    def test_text_filter_contains(self):
        result = Filter.rich_text("Description").contains("keyword")

        assert result == {"property": "Description", "rich_text": {"contains": "keyword"}}

    def test_number_filter_greater_than(self):
        result = Filter.number("Amount").greater_than(100)

        assert result == {"property": "Amount", "number": {"greater_than": 100}}

    def test_select_filter_equals(self):
        result = Filter.select("Status").equals("Active")

        assert result == {"property": "Status", "select": {"equals": "Active"}}

    def test_checkbox_filter_equals(self):
        result = Filter.checkbox("Done").equals(True)

        assert result == {"property": "Done", "checkbox": {"equals": True}}

    def test_and_filter(self):
        result = Filter.and_(
            Filter.select("Status").equals("Active"),
            Filter.checkbox("Done").equals(False),
        )

        assert "and" in result
        assert len(result["and"]) == 2

    def test_or_filter(self):
        result = Filter.or_(
            Filter.select("Status").equals("Active"),
            Filter.select("Status").equals("Pending"),
        )

        assert "or" in result
        assert len(result["or"]) == 2


class TestSort:
    """Tests for sort specifications."""

    def test_ascending_sort(self):
        result = Sort.ascending("Name")

        assert result == {"property": "Name", "direction": "ascending"}

    def test_descending_sort(self):
        result = Sort.descending("Created")

        assert result == {"property": "Created", "direction": "descending"}

    def test_created_time_sort(self):
        result = Sort.created_time_descending()

        assert result == {"timestamp": "created_time", "direction": "descending"}


class TestPageCreate:
    """Tests for page creation schema."""

    def test_page_create_to_api_format(self):
        page_create = PageCreate(
            parent_id="db123",
            parent_type="data_source",
            properties={
                "Name": TitleProperty(value="Test Page"),
                "Count": NumberProperty(value=5),
            },
        )

        result = page_create.to_api_format()

        assert result["parent"] == {"data_source_id": "db123"}
        assert "Name" in result["properties"]
        assert "Count" in result["properties"]

    def test_page_create_with_icon(self):
        page_create = PageCreate(
            parent_id="db123",
            parent_type="database",
            properties={"Name": TitleProperty(value="Test")},
            icon="🚀",
        )

        result = page_create.to_api_format()

        assert result["icon"] == {"type": "emoji", "emoji": "🚀"}


class TestPage:
    """Tests for Page model."""

    def test_page_from_api_response(self):
        api_response = {
            "id": "page-123",
            "created_time": "2025-01-15T12:00:00.000Z",
            "last_edited_time": "2025-01-15T12:00:00.000Z",
            "created_by": {"id": "user-1"},
            "last_edited_by": {"id": "user-1"},
            "parent": {"database_id": "db-123"},
            "archived": False,
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Test Page"}]},
            },
            "url": "https://notion.so/test",
        }

        page = Page.from_api_response(api_response)

        assert page.id == "page-123"
        assert page.get_title() == "Test Page"
        assert page.get_parent_type() == "database"

    def test_page_get_property(self):
        page = Page(
            id="page-123",
            created_time=datetime.now(),
            last_edited_time=datetime.now(),
            created_by="user-1",
            last_edited_by="user-1",
            parent={"database_id": "db-123"},
            properties={
                "Status": {"type": "select", "select": {"name": "Active"}},
            },
            url="https://notion.so/test",
        )

        assert page.get_property("Status") == "Active"
        assert page.get_property("NonExistent") is None
