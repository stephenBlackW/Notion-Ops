"""Rich text utilities for Notion Operations library."""

from typing import Any


def make_rich_text(
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
    underline: bool = False,
    code: bool = False,
    color: str = "default",
    link: str | None = None,
) -> list[dict[str, Any]]:
    """
    Create a rich text array from plain text with optional formatting.

    Args:
        text: The plain text content
        bold: Apply bold formatting
        italic: Apply italic formatting
        strikethrough: Apply strikethrough formatting
        underline: Apply underline formatting
        code: Apply code (monospace) formatting
        color: Text color (default, gray, brown, orange, yellow, green,
               blue, purple, pink, red, or *_background variants)
        link: Optional URL to make the text a link

    Returns:
        Rich text array suitable for Notion API

    Example:
        rich_text = make_rich_text("Hello", bold=True, color="blue")
        rich_text = make_rich_text("Click here", link="https://example.com")
    """
    text_obj: dict[str, Any] = {"content": text}

    if link:
        text_obj["link"] = {"url": link}

    rt: dict[str, Any] = {
        "type": "text",
        "text": text_obj,
        "annotations": {
            "bold": bold,
            "italic": italic,
            "strikethrough": strikethrough,
            "underline": underline,
            "code": code,
            "color": color,
        },
    }

    return [rt]


def make_rich_text_segments(*segments: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Combine multiple rich text segments into a single array.

    Args:
        *segments: Rich text segment dictionaries with 'text' and optional formatting

    Returns:
        Combined rich text array

    Example:
        rich_text = make_rich_text_segments(
            {"text": "Hello ", "bold": True},
            {"text": "World", "italic": True, "color": "red"},
            {"text": "!", "link": "https://example.com"}
        )
    """
    result: list[dict[str, Any]] = []

    for segment in segments:
        text = segment.get("text", "")
        rt = make_rich_text(
            text,
            bold=segment.get("bold", False),
            italic=segment.get("italic", False),
            strikethrough=segment.get("strikethrough", False),
            underline=segment.get("underline", False),
            code=segment.get("code", False),
            color=segment.get("color", "default"),
            link=segment.get("link"),
        )
        result.extend(rt)

    return result


def parse_rich_text(rich_text: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Parse Notion rich text array into a simplified format.

    Args:
        rich_text: Rich text array from Notion API

    Returns:
        List of parsed text segments with content and formatting

    Example:
        parsed = parse_rich_text(page_property["rich_text"])
        for segment in parsed:
            print(f"{segment['text']} (bold={segment['bold']})")
    """
    segments: list[dict[str, Any]] = []

    for rt in rich_text:
        rt_type = rt.get("type", "text")

        if rt_type == "text":
            text_obj = rt.get("text", {})
            annotations = rt.get("annotations", {})

            segment: dict[str, Any] = {
                "text": text_obj.get("content", ""),
                "bold": annotations.get("bold", False),
                "italic": annotations.get("italic", False),
                "strikethrough": annotations.get("strikethrough", False),
                "underline": annotations.get("underline", False),
                "code": annotations.get("code", False),
                "color": annotations.get("color", "default"),
            }

            link = text_obj.get("link")
            if link:
                segment["link"] = link.get("url")

            segments.append(segment)

        elif rt_type == "mention":
            mention = rt.get("mention", {})
            mention_type = mention.get("type")

            segment = {
                "text": rt.get("plain_text", ""),
                "mention_type": mention_type,
                "mention": mention.get(mention_type),
            }
            segments.append(segment)

        elif rt_type == "equation":
            equation = rt.get("equation", {})
            segment = {
                "text": rt.get("plain_text", ""),
                "equation": equation.get("expression"),
            }
            segments.append(segment)

    return segments


def extract_plain_text(rich_text: list[dict[str, Any]]) -> str:
    """
    Extract plain text from a Notion rich text array.

    Args:
        rich_text: Rich text array from Notion API

    Returns:
        Plain text string with all formatting stripped

    Example:
        text = extract_plain_text(page_property["title"])
        print(f"Page title: {text}")
    """
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def text_to_rich_text(text: str) -> list[dict[str, Any]]:
    """
    Convert plain text to basic rich text format.

    This is a simple wrapper that creates unformatted rich text.

    Args:
        text: Plain text string

    Returns:
        Rich text array
    """
    return [{"type": "text", "text": {"content": text}}]


def rich_text_to_markdown(rich_text: list[dict[str, Any]]) -> str:
    """
    Convert Notion rich text to markdown format.

    Args:
        rich_text: Rich text array from Notion API

    Returns:
        Markdown formatted string

    Example:
        md = rich_text_to_markdown(block["paragraph"]["rich_text"])
        print(md)  # "**bold** and *italic* text"
    """
    parts: list[str] = []

    for rt in rich_text:
        text = rt.get("plain_text", "")
        annotations = rt.get("annotations", {})
        text_obj = rt.get("text", {})

        # Apply formatting
        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"

        # Handle links
        link = text_obj.get("link")
        if link:
            url = link.get("url", "")
            text = f"[{text}]({url})"

        parts.append(text)

    return "".join(parts)
