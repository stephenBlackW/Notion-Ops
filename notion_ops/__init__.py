"""
Notion Operations Library

A high-level Python library for CRUD operations on Notion workspaces.
"""

from notion_ops.client import AsyncNotionOps, NotionOps
from notion_ops.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    NotionOpsError,
    PermissionError,
    RateLimitError,
    ValidationError,
)
from notion_ops.models.block import Block, Blocks, BlockType
from notion_ops.models.database import Database
from notion_ops.models.filters import Filter, Sort
from notion_ops.models.page import Page, PageCreate
from notion_ops.models.properties import (
    CheckboxProperty,
    DateProperty,
    EmailProperty,
    FilesProperty,
    MultiSelectProperty,
    NumberProperty,
    PeopleProperty,
    PhoneProperty,
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
from notion_ops.utils.markdown import create_atom_page

__version__ = "0.1.0"

__all__ = [
    # Client
    "NotionOps",
    "AsyncNotionOps",
    # Pages
    "Page",
    "PageCreate",
    # Databases
    "Database",
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
    "FilesProperty",
    "PhoneProperty",
    # Exceptions
    "NotionOpsError",
    "NotFoundError",
    "ValidationError",
    "AuthenticationError",
    "PermissionError",
    "RateLimitError",
    "ConflictError",
    # Utilities
    "create_atom_page",
]
