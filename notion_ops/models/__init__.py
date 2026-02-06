"""Data models for Notion Operations library."""

from notion_ops.models.block import Block, Blocks, BlockType
from notion_ops.models.database import Database, DataSource, DataSourceSchema
from notion_ops.models.filters import Filter, Sort
from notion_ops.models.page import Page, PageCreate, PageUpdate
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

__all__ = [
    # Block
    "Block",
    "Blocks",
    "BlockType",
    # Database
    "Database",
    "DataSource",
    "DataSourceSchema",
    # Filters
    "Filter",
    "Sort",
    # Page
    "Page",
    "PageCreate",
    "PageUpdate",
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
