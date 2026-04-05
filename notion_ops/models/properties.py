"""Property type definitions for Notion pages and databases."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class PropertyType(str, Enum):
    """Notion property types."""

    TITLE = "title"
    RICH_TEXT = "rich_text"
    NUMBER = "number"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    DATE = "date"
    PEOPLE = "people"
    FILES = "files"
    CHECKBOX = "checkbox"
    URL = "url"
    EMAIL = "email"
    PHONE_NUMBER = "phone_number"
    FORMULA = "formula"
    RELATION = "relation"
    ROLLUP = "rollup"
    CREATED_TIME = "created_time"
    CREATED_BY = "created_by"
    LAST_EDITED_TIME = "last_edited_time"
    LAST_EDITED_BY = "last_edited_by"
    STATUS = "status"
    UNIQUE_ID = "unique_id"
    VERIFICATION = "verification"


class PropertyValue(BaseModel):
    """Base class for property values."""

    type: PropertyType
    value: Any

    def to_api_format(self) -> dict[str, Any]:
        """Convert to Notion API format."""
        raise NotImplementedError("Subclasses must implement to_api_format")


class TitleProperty(PropertyValue):
    """Title property value."""

    type: PropertyType = PropertyType.TITLE
    value: str

    def to_api_format(self) -> dict[str, Any]:
        return {"title": [{"type": "text", "text": {"content": self.value}}]}


class RichTextProperty(PropertyValue):
    """Rich text property value."""

    type: PropertyType = PropertyType.RICH_TEXT
    value: str

    def to_api_format(self) -> dict[str, Any]:
        return {"rich_text": [{"type": "text", "text": {"content": self.value}}]}


class NumberProperty(PropertyValue):
    """Number property value."""

    type: PropertyType = PropertyType.NUMBER
    value: float | int

    def to_api_format(self) -> dict[str, Any]:
        return {"number": self.value}


class SelectProperty(PropertyValue):
    """Select property value."""

    type: PropertyType = PropertyType.SELECT
    value: str  # Option name

    def to_api_format(self) -> dict[str, Any]:
        return {"select": {"name": self.value}}


class MultiSelectProperty(PropertyValue):
    """Multi-select property value."""

    type: PropertyType = PropertyType.MULTI_SELECT
    value: list[str]  # List of option names

    def to_api_format(self) -> dict[str, Any]:
        return {"multi_select": [{"name": name} for name in self.value]}


class DateProperty(PropertyValue):
    """Date property value."""

    type: PropertyType = PropertyType.DATE
    value: datetime | str | None
    end: datetime | str | None = None

    def to_api_format(self) -> dict[str, Any]:
        if self.value is None:
            return {"date": None}

        start = self.value.isoformat() if isinstance(self.value, datetime) else self.value
        date_obj: dict[str, Any] = {"start": start}

        if self.end is not None:
            date_obj["end"] = self.end.isoformat() if isinstance(self.end, datetime) else self.end

        return {"date": date_obj}


class CheckboxProperty(PropertyValue):
    """Checkbox property value."""

    type: PropertyType = PropertyType.CHECKBOX
    value: bool

    def to_api_format(self) -> dict[str, Any]:
        return {"checkbox": self.value}


class URLProperty(PropertyValue):
    """URL property value."""

    type: PropertyType = PropertyType.URL
    value: str

    def to_api_format(self) -> dict[str, Any]:
        return {"url": self.value}


class EmailProperty(PropertyValue):
    """Email property value."""

    type: PropertyType = PropertyType.EMAIL
    value: str

    def to_api_format(self) -> dict[str, Any]:
        return {"email": self.value}


class PhoneProperty(PropertyValue):
    """Phone number property value."""

    type: PropertyType = PropertyType.PHONE_NUMBER
    value: str

    def to_api_format(self) -> dict[str, Any]:
        return {"phone_number": self.value}


class PeopleProperty(PropertyValue):
    """People property value."""

    type: PropertyType = PropertyType.PEOPLE
    value: list[str]  # List of user IDs

    def to_api_format(self) -> dict[str, Any]:
        return {"people": [{"id": user_id} for user_id in self.value]}


class RelationProperty(PropertyValue):
    """Relation property value."""

    type: PropertyType = PropertyType.RELATION
    value: list[str]  # List of page IDs

    def to_api_format(self) -> dict[str, Any]:
        return {"relation": [{"id": page_id} for page_id in self.value]}


class StatusProperty(PropertyValue):
    """Status property value."""

    type: PropertyType = PropertyType.STATUS
    value: str  # Status option name

    def to_api_format(self) -> dict[str, Any]:
        return {"status": {"name": self.value}}


class FilesProperty(PropertyValue):
    """Files property value."""

    type: PropertyType = PropertyType.FILES
    value: list[dict[str, Any]]  # List of file objects

    def to_api_format(self) -> dict[str, Any]:
        return {"files": self.value}


class PropertyDefinition(BaseModel):
    """Defines a property in a database/data source schema."""

    name: str
    type: PropertyType
    options: dict[str, Any] | None = None  # For select/multi-select/status

    def to_api_format(self) -> dict[str, Any]:
        """Convert to Notion API format for schema definition."""
        result: dict[str, Any] = {self.type.value: {}}

        if self.options and self.type in (
            PropertyType.SELECT,
            PropertyType.MULTI_SELECT,
            PropertyType.STATUS,
        ):
            result[self.type.value] = self.options

        return result


def parse_property_value(property_data: dict[str, Any]) -> Any:
    """Parse a property value from Notion API response format."""
    prop_type = property_data.get("type")

    if prop_type == "title":
        title_arr = property_data.get("title", [])
        return "".join(t.get("plain_text", "") for t in title_arr)

    elif prop_type == "rich_text":
        text_arr = property_data.get("rich_text", [])
        return "".join(t.get("plain_text", "") for t in text_arr)

    elif prop_type == "number":
        return property_data.get("number")

    elif prop_type == "select":
        select = property_data.get("select")
        return select.get("name") if select else None

    elif prop_type == "multi_select":
        return [opt.get("name") for opt in property_data.get("multi_select", [])]

    elif prop_type == "date":
        date = property_data.get("date")
        return date if date else None

    elif prop_type == "checkbox":
        return property_data.get("checkbox", False)

    elif prop_type == "url":
        return property_data.get("url")

    elif prop_type == "email":
        return property_data.get("email")

    elif prop_type == "phone_number":
        return property_data.get("phone_number")

    elif prop_type == "people":
        return [p.get("id") for p in property_data.get("people", [])]

    elif prop_type == "relation":
        return [r.get("id") for r in property_data.get("relation", [])]

    elif prop_type == "status":
        status = property_data.get("status")
        return status.get("name") if status else None

    elif prop_type == "files":
        return property_data.get("files", [])

    elif prop_type == "formula":
        formula = property_data.get("formula", {})
        formula_type = formula.get("type")
        return formula.get(formula_type) if formula_type else None

    elif prop_type == "rollup":
        rollup = property_data.get("rollup", {})
        rollup_type = rollup.get("type")
        return rollup.get(rollup_type) if rollup_type else None

    elif prop_type == "created_time":
        return property_data.get("created_time")

    elif prop_type == "last_edited_time":
        return property_data.get("last_edited_time")

    elif prop_type == "created_by":
        return property_data.get("created_by", {}).get("id")

    elif prop_type == "last_edited_by":
        return property_data.get("last_edited_by", {}).get("id")

    elif prop_type == "unique_id":
        unique_id = property_data.get("unique_id", {})
        prefix = unique_id.get("prefix", "")
        number = unique_id.get("number", 0)
        return f"{prefix}-{number}" if prefix else str(number)

    return property_data
