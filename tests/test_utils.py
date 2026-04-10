"""Tests for Notion Operations utility functions."""


from notion_ops.utils.rich_text import (
    extract_plain_text,
    make_rich_text,
    make_rich_text_segments,
    parse_rich_text,
    rich_text_to_markdown,
)


class TestMakeRichText:
    """Tests for rich text creation."""

    def test_basic_text(self):
        result = make_rich_text("Hello")

        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"]["content"] == "Hello"

    def test_bold_text(self):
        result = make_rich_text("Bold", bold=True)

        assert result[0]["annotations"]["bold"] is True

    def test_italic_text(self):
        result = make_rich_text("Italic", italic=True)

        assert result[0]["annotations"]["italic"] is True

    def test_code_text(self):
        result = make_rich_text("code", code=True)

        assert result[0]["annotations"]["code"] is True

    def test_colored_text(self):
        result = make_rich_text("Colored", color="blue")

        assert result[0]["annotations"]["color"] == "blue"

    def test_linked_text(self):
        result = make_rich_text("Click", link="https://example.com")

        assert result[0]["text"]["link"]["url"] == "https://example.com"

    def test_combined_formatting(self):
        result = make_rich_text(
            "Fancy",
            bold=True,
            italic=True,
            color="red",
            link="https://example.com",
        )

        assert result[0]["annotations"]["bold"] is True
        assert result[0]["annotations"]["italic"] is True
        assert result[0]["annotations"]["color"] == "red"
        assert result[0]["text"]["link"]["url"] == "https://example.com"


class TestMakeRichTextSegments:
    """Tests for combining rich text segments."""

    def test_multiple_segments(self):
        result = make_rich_text_segments(
            {"text": "Hello ", "bold": True},
            {"text": "World", "italic": True},
        )

        assert len(result) == 2
        assert result[0]["annotations"]["bold"] is True
        assert result[1]["annotations"]["italic"] is True


class TestParseRichText:
    """Tests for parsing rich text from API responses."""

    def test_parse_simple_text(self):
        rich_text = [
            {
                "type": "text",
                "text": {"content": "Hello"},
                "annotations": {
                    "bold": False,
                    "italic": False,
                    "strikethrough": False,
                    "underline": False,
                    "code": False,
                    "color": "default",
                },
                "plain_text": "Hello",
            }
        ]

        result = parse_rich_text(rich_text)

        assert len(result) == 1
        assert result[0]["text"] == "Hello"
        assert result[0]["bold"] is False

    def test_parse_formatted_text(self):
        rich_text = [
            {
                "type": "text",
                "text": {"content": "Bold", "link": {"url": "https://example.com"}},
                "annotations": {
                    "bold": True,
                    "italic": False,
                    "strikethrough": False,
                    "underline": False,
                    "code": False,
                    "color": "red",
                },
                "plain_text": "Bold",
            }
        ]

        result = parse_rich_text(rich_text)

        assert result[0]["bold"] is True
        assert result[0]["color"] == "red"
        assert result[0]["link"] == "https://example.com"


class TestExtractPlainText:
    """Tests for extracting plain text."""

    def test_extract_single_segment(self):
        rich_text = [{"plain_text": "Hello"}]

        assert extract_plain_text(rich_text) == "Hello"

    def test_extract_multiple_segments(self):
        rich_text = [{"plain_text": "Hello "}, {"plain_text": "World"}]

        assert extract_plain_text(rich_text) == "Hello World"

    def test_extract_empty(self):
        assert extract_plain_text([]) == ""


class TestRichTextToMarkdown:
    """Tests for converting rich text to markdown."""

    def test_plain_text(self):
        rich_text = [{"plain_text": "Hello", "text": {}, "annotations": {}}]

        assert rich_text_to_markdown(rich_text) == "Hello"

    def test_bold_text(self):
        rich_text = [
            {"plain_text": "Bold", "text": {}, "annotations": {"bold": True}}
        ]

        assert rich_text_to_markdown(rich_text) == "**Bold**"

    def test_italic_text(self):
        rich_text = [
            {"plain_text": "Italic", "text": {}, "annotations": {"italic": True}}
        ]

        assert rich_text_to_markdown(rich_text) == "*Italic*"

    def test_code_text(self):
        rich_text = [
            {"plain_text": "code", "text": {}, "annotations": {"code": True}}
        ]

        assert rich_text_to_markdown(rich_text) == "`code`"

    def test_strikethrough_text(self):
        rich_text = [
            {"plain_text": "strike", "text": {}, "annotations": {"strikethrough": True}}
        ]

        assert rich_text_to_markdown(rich_text) == "~~strike~~"

    def test_linked_text(self):
        rich_text = [
            {
                "plain_text": "Link",
                "text": {"link": {"url": "https://example.com"}},
                "annotations": {},
            }
        ]

        assert rich_text_to_markdown(rich_text) == "[Link](https://example.com)"

    def test_combined_formatting(self):
        rich_text = [
            {"plain_text": "Bold", "text": {}, "annotations": {"bold": True}},
            {"plain_text": " and ", "text": {}, "annotations": {}},
            {"plain_text": "italic", "text": {}, "annotations": {"italic": True}},
        ]

        assert rich_text_to_markdown(rich_text) == "**Bold** and *italic*"
