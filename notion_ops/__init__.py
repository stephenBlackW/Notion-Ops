"""
Notion Operations Library

A high-level Python library for CRUD operations on Notion workspaces.
"""

from notion_ops.client import AsyncNotionOps, NotionOps
from notion_ops.models.block import Block, Blocks, BlockType
from notion_ops.models.filters import Filter, Sort
from notion_ops.models.properties import (
    CheckboxProperty,
    DateProperty,
    EmailProperty,
    MultiSelectProperty,
    NumberProperty,
    PeopleProperty,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    RelationProperty,
    RichTextProperty,
    SelectProperty,
    StatusProperty,
    TitleProperty,
    URLProperty,
)

__version__ = "0.1.0"

__all__ = [
    # Client
    "NotionOps",
    "AsyncNotionOps",
    # Models
    "Block",
    "Blocks",
    "BlockType",
    "Filter",
    "Sort",
    # Properties
    "PropertyType",
    "PropertyValue",
    "PropertyDefinition",
    "TitleProperty",
    "RichTextProperty",
    "NumberProperty",
    "SelectProperty",
    "MultiSelectProperty",
    "DateProperty",
    "CheckboxProperty",
    "URLProperty",
    "EmailProperty",
    "PeopleProperty",
    "RelationProperty",
    "StatusProperty",
]
