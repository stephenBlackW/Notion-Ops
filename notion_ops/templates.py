"""Declarative page templates for the Notion API.

The Notion API cannot instantiate native Notion *Templates*. This module fills
that gap: define a :class:`PageTemplate` once (target database, static
properties, an optional markdown body with ``{var}`` placeholders) and
instantiate it via the API as many times as you like, overriding properties and
substituting variables per call.

The engine is intentionally **generic and DB-agnostic** — it imports no config
and hardcodes no database ids. See ``cli/atoms.py`` for the reference example of
an application-specific template built on this engine.
"""

from __future__ import annotations

import logging
from typing import Any

from notion_ops.models.properties import TitleProperty
from notion_ops.utils.markdown import (
    _MAX_BLOCKS_PER_REQUEST,
    _MAX_PAYLOAD_BYTES,
    _estimate_block_size,
    markdown_to_blocks,
)

logger = logging.getLogger(__name__)


class _SafeFormatDict(dict):
    """dict subclass that leaves unknown ``{placeholders}`` intact.

    Used with :meth:`str.format_map` so a body referencing ``{unknown}`` when no
    matching variable is supplied is rendered verbatim rather than raising
    ``KeyError``.
    """

    def __missing__(self, key: str) -> str:  # noqa: D401 - simple passthrough
        return "{" + key + "}"


class PageTemplate:
    """A reusable, declarative template for creating Notion pages via the API.

    Args:
        database_id: The id of the database the instantiated pages live in.
        title_property: Name of the title property on the target database
            (``"Name"`` by default).
        static_properties: Properties applied to every instantiation. Per-call
            ``properties`` override these key-by-key.
        body_markdown: Default markdown body. May contain ``{var}`` placeholders
            resolved from ``variables`` at instantiation time.
    """

    def __init__(
        self,
        *,
        database_id: str,
        title_property: str = "Name",
        static_properties: dict[str, Any] | None = None,
        body_markdown: str | None = None,
    ) -> None:
        self.database_id = database_id
        self.title_property = title_property
        self.static_properties = static_properties or {}
        self.body_markdown = body_markdown

    def instantiate(
        self,
        client: Any,
        *,
        title: str,
        properties: dict[str, Any] | None = None,
        body_markdown: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one page from this template.

        Merges ``static_properties`` with the per-call ``properties`` (per-call
        wins), sets the title property, creates the page, then renders and
        appends the body in size-aware batches.

        The page is created first and the result dict is built immediately, so
        callers always receive the ``page_id`` even when the subsequent body
        append fails (ISS-006). A body-append failure is non-fatal: it is logged
        as a warning and recorded in ``result['content_error']``.

        Args:
            client: A NotionOps-like client exposing ``pages.create(...)`` and
                ``api.blocks.children.append(...)``.
            title: Value for the title property.
            properties: Per-call properties merged over ``static_properties``.
            body_markdown: Overrides the template's ``body_markdown`` when given.
            variables: Substituted into the body via ``{var}`` placeholders.

        Returns:
            Dict with keys ``page_id``, ``id`` (alias of ``page_id``), ``url``,
            ``title`` and ``content_error`` (``None`` on success).
        """
        # Merge static + per-call properties (per-call wins), then set title.
        merged: dict[str, Any] = dict(self.static_properties)
        if properties:
            merged.update(properties)
        merged[self.title_property] = TitleProperty(value=title)

        # Create the page.
        page = client.pages.create(
            parent_id=self.database_id,
            properties=merged,
            parent_type="database",
        )

        # Build the result immediately so callers always get the page_id even if
        # the subsequent block append fails (ISS-006).
        page_id_clean = page.id.replace("-", "")
        result: dict[str, Any] = {
            "page_id": page.id,
            "id": page.id,  # backward-compatible alias
            "url": f"https://notion.so/{page_id_clean}",
            "title": title,
            "content_error": None,
        }

        # Resolve the body: per-call override else the template default.
        body = body_markdown if body_markdown is not None else self.body_markdown
        if body and variables:
            body = body.format_map(_SafeFormatDict(variables))

        # Attempt to append content blocks — failure is non-fatal.
        if body:
            try:
                blocks = markdown_to_blocks(body)

                # Size-aware batching: respect both the block-count limit and a
                # conservative payload-size limit to avoid API rejections.
                current_batch: list[dict[str, Any]] = []
                current_size = 0

                for block in blocks:
                    block_size = _estimate_block_size(block)

                    if current_batch and (
                        len(current_batch) >= _MAX_BLOCKS_PER_REQUEST
                        or current_size + block_size > _MAX_PAYLOAD_BYTES
                    ):
                        client.api.blocks.children.append(
                            block_id=page.id,
                            children=current_batch,
                        )
                        current_batch = []
                        current_size = 0

                    current_batch.append(block)
                    current_size += block_size

                if current_batch:
                    client.api.blocks.children.append(
                        block_id=page.id,
                        children=current_batch,
                    )
            except Exception as e:
                result["content_error"] = str(e)
                logger.warning(
                    "Failed to append content blocks to page %s (%s): %s",
                    page.id,
                    title,
                    e,
                )

        return result
