# notion-ops

A high-level Python library for CRUD operations on Notion workspaces.

## Installation

```bash
pip install notion-ops
```

## Quick Start

```python
from notion_ops import NotionOps, Blocks, Filter, Sort
from notion_ops.models import TitleProperty, SelectProperty, CheckboxProperty

# Initialize client (uses NOTION_API_KEY env var)
client = NotionOps()

# Create a page
page = client.pages.create(
    parent_id="database_id",
    properties={
        "Name": TitleProperty(value="New Task"),
        "Status": SelectProperty(value="In Progress"),
    },
    children=[
        Blocks.heading_1("Overview"),
        Blocks.paragraph("Task description here."),
    ]
)

# Query a database
results = client.data_sources.query(
    "database_id",
    filter=Filter.and_(
        Filter.select("Status").equals("Active"),
        Filter.checkbox("Archived").equals(False)
    ),
    sorts=[Sort.descending("Created")]
)

for page in results.pages:
    print(page.get_title())
```

## Features

- **Full CRUD Operations**: Create, Read, Update, Delete for pages, databases, and blocks
- **Type-Safe Models**: Pydantic models for all Notion objects
- **Query Builders**: Fluent API for building filters and sorts
- **Block Builders**: Easy creation of all Notion block types
- **Pagination Helpers**: Automatic handling of paginated results
- **Rich Text Utilities**: Convert between plain text, rich text, and markdown
- **Limit-Aware Publishing**: Publish Markdown or arbitrarily-nested block trees in the minimum number of requests, automatically respecting Notion's 2-level inline-nesting cap, the 100-children-per-request limit, >100-row table splitting, and payload-size limits — no manual batching

## API Reference

### Client

```python
from notion_ops import NotionOps

# Initialize with environment variable
client = NotionOps()

# Or with explicit token
client = NotionOps(auth="secret_xxx")

# Access operations
client.pages      # Page CRUD
client.databases  # Database CRUD
client.data_sources  # Data source queries
client.blocks     # Block CRUD
client.users      # User operations
```

### Pages

```python
# Create
page = client.pages.create(
    parent_id="db_id",
    properties={"Name": TitleProperty(value="Title")},
)

# Read
page = client.pages.get("page_id")
print(page.get_title())
print(page.get_property("Status"))

# Update
page = client.pages.update(
    "page_id",
    properties={"Status": SelectProperty(value="Done")}
)

# Archive/Delete
client.pages.archive("page_id")
```

### Databases & Data Sources

```python
# Create database
db = client.databases.create(
    parent_id="page_id",
    title="Tasks",
    schema={
        "Name": PropertyDefinition(name="Name", type=PropertyType.TITLE),
        "Status": PropertyDefinition(
            name="Status",
            type=PropertyType.STATUS,
            options={"options": [{"name": "Done", "color": "green"}]}
        ),
    }
)

# Query with filters
results = client.data_sources.query(
    "data_source_id",
    filter=Filter.select("Status").equals("Active"),
    sorts=[Sort.descending("Created")]
)

# Iterate all pages
for page in client.data_sources.query_all("data_source_id"):
    print(page.get_title())
```

### Blocks

```python
# Get page content
blocks = client.blocks.get_children(page_id, recursive=True)

# Append content
client.blocks.append(
    page_id,
    [
        Blocks.heading_2("Section"),
        Blocks.paragraph("Content here."),
        Blocks.bulleted_list("Item 1"),
        Blocks.code("print('hello')", language="python"),
        Blocks.callout("Note!", emoji="💡"),
    ]
)

# Update block
client.blocks.update(block_id, content={"rich_text": [...]})

# Delete block
client.blocks.delete(block_id)
```

### Filters

```python
from notion_ops import Filter

# Simple filters
Filter.title("Name").contains("test")
Filter.select("Status").equals("Active")
Filter.checkbox("Done").equals(True)
Filter.number("Count").greater_than(10)
Filter.date("Due").before(datetime.now())

# Compound filters
Filter.and_(
    Filter.select("Status").equals("Active"),
    Filter.checkbox("Archived").equals(False)
)

Filter.or_(
    Filter.select("Status").equals("Done"),
    Filter.select("Status").equals("Cancelled")
)
```

### Block Builders

```python
from notion_ops import Blocks

Blocks.paragraph("Text content")
Blocks.heading_1("Title")
Blocks.heading_2("Subtitle")
Blocks.heading_3("Section")
Blocks.bulleted_list("Item")
Blocks.numbered_list("Step")
Blocks.todo("Task", checked=False)
Blocks.toggle("Expandable")
Blocks.code("code", language="python")
Blocks.quote("Quote text")
Blocks.callout("Note", emoji="💡")
Blocks.divider()
Blocks.image("https://...")
Blocks.bookmark("https://...")
```

### Publishing Markdown & nested content

`blocks.children.append` accepts at most two levels of nesting and 100 children
per request, and large tables or deeply-nested content must be split across
follow-up requests. `publish_block_tree` / `publish_markdown` plan and execute
the minimum sequence of appends that respects every one of those limits for you.

```python
from notion_ops import NotionOps, publish_markdown, publish_block_tree, markdown_to_blocks

client = NotionOps()

# Publish a Markdown document (tables, nested lists, code, toggles, ...) under a page.
result = publish_markdown(client, "page_id", "# Report\n\n| a | b |\n| - | - |\n...")
print(result.request_count, result.top_level_block_ids)

# Or publish an already-built nested block tree.
blocks = markdown_to_blocks(some_markdown)
publish_block_tree(client, "page_id", blocks)

# Idempotent republish: re-running refreshes the page instead of duplicating it.
# Clears the existing top-level children, then publishes the new tree — exactly
# what you want when re-publishing a report/atom. Content is idempotent; block
# ids are not stable (cleared blocks are archived + recreated), and the clear +
# publish are not a single transaction.
from notion_ops import republish_markdown
result = republish_markdown(client, "page_id", "# Report (v2)\n\nupdated body")
print(result.deleted_count, result.request_count)
```

`PageTemplate` publishes its body through the same path, so templated pages get
the same limit-handling automatically.

## Property Types

```python
from notion_ops.models import (
    TitleProperty,
    RichTextProperty,
    NumberProperty,
    SelectProperty,
    MultiSelectProperty,
    DateProperty,
    CheckboxProperty,
    URLProperty,
    EmailProperty,
    PeopleProperty,
    RelationProperty,
    StatusProperty,
)

# Usage
properties = {
    "Name": TitleProperty(value="Task Name"),
    "Description": RichTextProperty(value="Details..."),
    "Priority": NumberProperty(value=1),
    "Status": SelectProperty(value="In Progress"),
    "Tags": MultiSelectProperty(value=["urgent", "feature"]),
    "Due Date": DateProperty(value=datetime(2025, 2, 1)),
    "Completed": CheckboxProperty(value=False),
    "Website": URLProperty(value="https://example.com"),
    "Email": EmailProperty(value="user@example.com"),
    "Assignees": PeopleProperty(value=["user_id_1"]),
    "Related": RelationProperty(value=["page_id_1"]),
}
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy notion_ops

# Linting
ruff check notion_ops
```

## License

MIT
