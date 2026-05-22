"""Atom page creation utilities for AgenticOS.

High-level helper for creating Atom pages in Notion with markdown content.
Separated from markdown.py for clarity — markdown.py handles block conversion,
this module handles Atom-specific page creation logic.
"""

from __future__ import annotations

import logging
from typing import Any

from notion_ops.utils.markdown import markdown_to_blocks
from notion_ops.utils.publish import publish_block_tree

logger = logging.getLogger(__name__)


def create_atom_page(
    notion_client: Any,
    title: str,
    content_markdown: str,
    *,
    atom_type: str = "Document",
    description: str | None = None,
    parent_id: str | None = None,
    topics: list[str] | None = None,
    project_id: str | None = None,
    status: str | None = None,
    meta: list[str] | None = None,
    extra_properties: dict[str, Any] | None = None,
    atoms_db_id: str = "REDACTED-PRIVATE-DB-ID",
) -> dict[str, Any]:
    """
    Create a new Atom page in Notion with markdown content.

    This is the standard way to create documented artifacts in AgenticOS.
    Uses the Notion Doc & Dev Skill patterns.

    Args:
        notion_client: NotionOps client instance
        title: Page title (Name property)
        content_markdown: Markdown content for the page body
        atom_type: Type property value (Document, Spec, Note, Skill, etc.)
        description: Optional 1-3 sentence description
        parent_id: Optional Parent relation (page that spawned this)
        topics: Optional list of topic page IDs for Topics relation
        project_id: Optional Project relation page ID
        status: Optional Status property value (e.g. "Draft", "Complete", "In Progress")
        meta: Optional Meta relation (list of page IDs for Meta/Mesa relationship)
        extra_properties: Optional dict of additional properties. Keys are property names,
            values should be property objects (e.g. SelectProperty(value="x")). These are
            merged into the properties dict and will not override explicitly set properties.
        atoms_db_id: Atoms database ID (defaults to AgenticOS Atoms)

    Returns:
        Dict with keys:
            - 'page_id': Notion page ID of the created page
            - 'id': Backward-compatible alias for 'page_id'
            - 'url': Notion URL of the created page
            - 'title': Title of the created page
            - 'content_error': None if content was appended successfully,
              or an error string if block append failed. The page still exists
              in Notion even when this is set — the caller should NOT retry
              page creation.

    Example:
        from notion_ops import NotionOps
        from notion_ops.utils.atoms import create_atom_page

        notion = NotionOps()
        result = create_atom_page(
            notion,
            title="My Design Spec",
            content_markdown=spec_md,
            atom_type="Spec",
            description="Design spec for feature X",
            parent_id="action-item-page-id",
            status="Draft",
            meta=["meta-page-id-1", "meta-page-id-2"],
        )
        print(result['url'])
    """
    from notion_ops.models.properties import (
        RelationProperty,
        RichTextProperty,
        SelectProperty,
        TitleProperty,
    )

    # Build properties
    # Start with extra_properties if provided (will be overridden by explicit properties)
    properties: dict[str, Any] = {}
    if extra_properties:
        properties.update(extra_properties)

    # Set explicit properties (these take precedence over extra_properties)
    properties['Name'] = TitleProperty(value=title)
    properties['Type'] = SelectProperty(value=atom_type)

    if description:
        properties['Description'] = RichTextProperty(value=description)

    if parent_id:
        properties['Parent'] = RelationProperty(value=[parent_id])

    if topics:
        properties['Topics'] = RelationProperty(value=topics)

    if project_id:
        properties['Project'] = RelationProperty(value=[project_id])

    if status:
        # Atoms DB defines Status as 'select', not 'status' type
        properties['Status'] = SelectProperty(value=status)

    if meta:
        properties['Meta'] = RelationProperty(value=meta)

    # Create page
    page = notion_client.pages.create(
        parent_id=atoms_db_id,
        properties=properties,
        parent_type="database"
    )

    # Page created successfully — build result immediately so callers always
    # get the page_id even if the subsequent block append fails (ISS-006).
    page_id_clean = page.id.replace('-', '')
    result: dict[str, Any] = {
        'page_id': page.id,
        'id': page.id,  # backward-compatible alias
        'url': f"https://notion.so/{page_id_clean}",
        'title': title,
        'content_error': None,
    }

    # Attempt to append content blocks — failure is non-fatal.
    # publish_block_tree() converts markdown to a nested block tree and appends
    # it in the minimum number of requests that respects Notion's per-request
    # nesting (2 levels), block-count (100), and payload-size limits — deferring
    # deeper sub-trees (e.g. nested toggles) into follow-up appends.
    if content_markdown:
        try:
            blocks = markdown_to_blocks(content_markdown)
            publish_block_tree(notion_client, page.id, blocks)
        except Exception as e:
            result['content_error'] = str(e)
            logger.warning(
                "Failed to append content blocks to page %s (%s): %s",
                page.id, title, e,
            )

    return result
