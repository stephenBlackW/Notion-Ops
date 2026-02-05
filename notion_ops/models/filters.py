"""Query filter and sort builders for Notion Operations library."""

from datetime import datetime
from typing import Any


class PropertyFilter:
    """Builder for property filters."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def title(self) -> "TextFilter":
        """Create a title filter."""
        return TextFilter(self.property_name, "title")

    def rich_text(self) -> "TextFilter":
        """Create a rich text filter."""
        return TextFilter(self.property_name, "rich_text")

    def number(self) -> "NumberFilter":
        """Create a number filter."""
        return NumberFilter(self.property_name)

    def select(self) -> "SelectFilter":
        """Create a select filter."""
        return SelectFilter(self.property_name)

    def multi_select(self) -> "MultiSelectFilter":
        """Create a multi-select filter."""
        return MultiSelectFilter(self.property_name)

    def date(self) -> "DateFilter":
        """Create a date filter."""
        return DateFilter(self.property_name)

    def checkbox(self) -> "CheckboxFilter":
        """Create a checkbox filter."""
        return CheckboxFilter(self.property_name)

    def status(self) -> "StatusFilter":
        """Create a status filter."""
        return StatusFilter(self.property_name)

    def people(self) -> "PeopleFilter":
        """Create a people filter."""
        return PeopleFilter(self.property_name)

    def relation(self) -> "RelationFilter":
        """Create a relation filter."""
        return RelationFilter(self.property_name)

    def files(self) -> "FilesFilter":
        """Create a files filter."""
        return FilesFilter(self.property_name)


class TextFilter:
    """Filter for text properties (title, rich_text)."""

    def __init__(self, property_name: str, prop_type: str = "rich_text"):
        self.property_name = property_name
        self.prop_type = prop_type

    def _build(self, condition: str, value: Any = None) -> dict[str, Any]:
        filter_obj: dict[str, Any] = {"property": self.property_name, self.prop_type: {}}
        if value is not None:
            filter_obj[self.prop_type][condition] = value
        else:
            filter_obj[self.prop_type][condition] = True
        return filter_obj

    def equals(self, value: str) -> dict[str, Any]:
        return self._build("equals", value)

    def does_not_equal(self, value: str) -> dict[str, Any]:
        return self._build("does_not_equal", value)

    def contains(self, value: str) -> dict[str, Any]:
        return self._build("contains", value)

    def does_not_contain(self, value: str) -> dict[str, Any]:
        return self._build("does_not_contain", value)

    def starts_with(self, value: str) -> dict[str, Any]:
        return self._build("starts_with", value)

    def ends_with(self, value: str) -> dict[str, Any]:
        return self._build("ends_with", value)

    def is_empty(self) -> dict[str, Any]:
        return self._build("is_empty")

    def is_not_empty(self) -> dict[str, Any]:
        return self._build("is_not_empty")


class NumberFilter:
    """Filter for number properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def _build(self, condition: str, value: Any = None) -> dict[str, Any]:
        filter_obj: dict[str, Any] = {"property": self.property_name, "number": {}}
        if value is not None:
            filter_obj["number"][condition] = value
        else:
            filter_obj["number"][condition] = True
        return filter_obj

    def equals(self, value: float | int) -> dict[str, Any]:
        return self._build("equals", value)

    def does_not_equal(self, value: float | int) -> dict[str, Any]:
        return self._build("does_not_equal", value)

    def greater_than(self, value: float | int) -> dict[str, Any]:
        return self._build("greater_than", value)

    def greater_than_or_equal(self, value: float | int) -> dict[str, Any]:
        return self._build("greater_than_or_equal_to", value)

    def less_than(self, value: float | int) -> dict[str, Any]:
        return self._build("less_than", value)

    def less_than_or_equal(self, value: float | int) -> dict[str, Any]:
        return self._build("less_than_or_equal_to", value)

    def is_empty(self) -> dict[str, Any]:
        return self._build("is_empty")

    def is_not_empty(self) -> dict[str, Any]:
        return self._build("is_not_empty")


class SelectFilter:
    """Filter for select properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def _build(self, condition: str, value: Any = None) -> dict[str, Any]:
        filter_obj: dict[str, Any] = {"property": self.property_name, "select": {}}
        if value is not None:
            filter_obj["select"][condition] = value
        else:
            filter_obj["select"][condition] = True
        return filter_obj

    def equals(self, value: str) -> dict[str, Any]:
        return self._build("equals", value)

    def does_not_equal(self, value: str) -> dict[str, Any]:
        return self._build("does_not_equal", value)

    def is_empty(self) -> dict[str, Any]:
        return self._build("is_empty")

    def is_not_empty(self) -> dict[str, Any]:
        return self._build("is_not_empty")


class StatusFilter:
    """Filter for status properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def _build(self, condition: str, value: Any = None) -> dict[str, Any]:
        filter_obj: dict[str, Any] = {"property": self.property_name, "status": {}}
        if value is not None:
            filter_obj["status"][condition] = value
        else:
            filter_obj["status"][condition] = True
        return filter_obj

    def equals(self, value: str) -> dict[str, Any]:
        return self._build("equals", value)

    def does_not_equal(self, value: str) -> dict[str, Any]:
        return self._build("does_not_equal", value)

    def is_empty(self) -> dict[str, Any]:
        return self._build("is_empty")

    def is_not_empty(self) -> dict[str, Any]:
        return self._build("is_not_empty")


class MultiSelectFilter:
    """Filter for multi-select properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def _build(self, condition: str, value: Any = None) -> dict[str, Any]:
        filter_obj: dict[str, Any] = {"property": self.property_name, "multi_select": {}}
        if value is not None:
            filter_obj["multi_select"][condition] = value
        else:
            filter_obj["multi_select"][condition] = True
        return filter_obj

    def contains(self, value: str) -> dict[str, Any]:
        return self._build("contains", value)

    def does_not_contain(self, value: str) -> dict[str, Any]:
        return self._build("does_not_contain", value)

    def is_empty(self) -> dict[str, Any]:
        return self._build("is_empty")

    def is_not_empty(self) -> dict[str, Any]:
        return self._build("is_not_empty")


class DateFilter:
    """Filter for date properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def _build(self, condition: str, value: Any = None) -> dict[str, Any]:
        filter_obj: dict[str, Any] = {"property": self.property_name, "date": {}}
        if value is not None:
            if isinstance(value, datetime):
                value = value.isoformat()
            filter_obj["date"][condition] = value
        else:
            filter_obj["date"][condition] = {}
        return filter_obj

    def equals(self, value: datetime | str) -> dict[str, Any]:
        return self._build("equals", value)

    def before(self, value: datetime | str) -> dict[str, Any]:
        return self._build("before", value)

    def after(self, value: datetime | str) -> dict[str, Any]:
        return self._build("after", value)

    def on_or_before(self, value: datetime | str) -> dict[str, Any]:
        return self._build("on_or_before", value)

    def on_or_after(self, value: datetime | str) -> dict[str, Any]:
        return self._build("on_or_after", value)

    def past_week(self) -> dict[str, Any]:
        return self._build("past_week")

    def past_month(self) -> dict[str, Any]:
        return self._build("past_month")

    def past_year(self) -> dict[str, Any]:
        return self._build("past_year")

    def this_week(self) -> dict[str, Any]:
        return self._build("this_week")

    def next_week(self) -> dict[str, Any]:
        return self._build("next_week")

    def next_month(self) -> dict[str, Any]:
        return self._build("next_month")

    def next_year(self) -> dict[str, Any]:
        return self._build("next_year")

    def is_empty(self) -> dict[str, Any]:
        return self._build("is_empty", True)

    def is_not_empty(self) -> dict[str, Any]:
        return self._build("is_not_empty", True)


class CheckboxFilter:
    """Filter for checkbox properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def equals(self, value: bool) -> dict[str, Any]:
        return {"property": self.property_name, "checkbox": {"equals": value}}


class PeopleFilter:
    """Filter for people properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def _build(self, condition: str, value: Any = None) -> dict[str, Any]:
        filter_obj: dict[str, Any] = {"property": self.property_name, "people": {}}
        if value is not None:
            filter_obj["people"][condition] = value
        else:
            filter_obj["people"][condition] = True
        return filter_obj

    def contains(self, user_id: str) -> dict[str, Any]:
        return self._build("contains", user_id)

    def does_not_contain(self, user_id: str) -> dict[str, Any]:
        return self._build("does_not_contain", user_id)

    def is_empty(self) -> dict[str, Any]:
        return self._build("is_empty")

    def is_not_empty(self) -> dict[str, Any]:
        return self._build("is_not_empty")


class RelationFilter:
    """Filter for relation properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def _build(self, condition: str, value: Any = None) -> dict[str, Any]:
        filter_obj: dict[str, Any] = {"property": self.property_name, "relation": {}}
        if value is not None:
            filter_obj["relation"][condition] = value
        else:
            filter_obj["relation"][condition] = True
        return filter_obj

    def contains(self, page_id: str) -> dict[str, Any]:
        return self._build("contains", page_id)

    def does_not_contain(self, page_id: str) -> dict[str, Any]:
        return self._build("does_not_contain", page_id)

    def is_empty(self) -> dict[str, Any]:
        return self._build("is_empty")

    def is_not_empty(self) -> dict[str, Any]:
        return self._build("is_not_empty")


class FilesFilter:
    """Filter for files properties."""

    def __init__(self, property_name: str):
        self.property_name = property_name

    def is_empty(self) -> dict[str, Any]:
        return {"property": self.property_name, "files": {"is_empty": True}}

    def is_not_empty(self) -> dict[str, Any]:
        return {"property": self.property_name, "files": {"is_not_empty": True}}


class Filter:
    """Builder for Notion query filters."""

    @staticmethod
    def and_(*filters: dict[str, Any]) -> dict[str, Any]:
        """Combine filters with AND logic."""
        return {"and": list(filters)}

    @staticmethod
    def or_(*filters: dict[str, Any]) -> dict[str, Any]:
        """Combine filters with OR logic."""
        return {"or": list(filters)}

    @staticmethod
    def property(name: str) -> PropertyFilter:
        """Start a property filter."""
        return PropertyFilter(name)

    # Convenience shortcuts
    @staticmethod
    def title(name: str = "Name") -> TextFilter:
        """Create a title filter."""
        return PropertyFilter(name).title()

    @staticmethod
    def rich_text(name: str) -> TextFilter:
        """Create a rich text filter."""
        return PropertyFilter(name).rich_text()

    @staticmethod
    def number(name: str) -> NumberFilter:
        """Create a number filter."""
        return PropertyFilter(name).number()

    @staticmethod
    def select(name: str) -> SelectFilter:
        """Create a select filter."""
        return PropertyFilter(name).select()

    @staticmethod
    def multi_select(name: str) -> MultiSelectFilter:
        """Create a multi-select filter."""
        return PropertyFilter(name).multi_select()

    @staticmethod
    def date(name: str) -> DateFilter:
        """Create a date filter."""
        return PropertyFilter(name).date()

    @staticmethod
    def checkbox(name: str) -> CheckboxFilter:
        """Create a checkbox filter."""
        return PropertyFilter(name).checkbox()

    @staticmethod
    def status(name: str) -> StatusFilter:
        """Create a status filter."""
        return PropertyFilter(name).status()

    @staticmethod
    def people(name: str) -> PeopleFilter:
        """Create a people filter."""
        return PropertyFilter(name).people()

    @staticmethod
    def relation(name: str) -> RelationFilter:
        """Create a relation filter."""
        return PropertyFilter(name).relation()

    @staticmethod
    def files(name: str) -> FilesFilter:
        """Create a files filter."""
        return PropertyFilter(name).files()


class Sort:
    """Sort specification for queries."""

    @staticmethod
    def ascending(property_name: str) -> dict[str, str]:
        """Sort by property in ascending order."""
        return {"property": property_name, "direction": "ascending"}

    @staticmethod
    def descending(property_name: str) -> dict[str, str]:
        """Sort by property in descending order."""
        return {"property": property_name, "direction": "descending"}

    @staticmethod
    def created_time_ascending() -> dict[str, str]:
        """Sort by created time in ascending order."""
        return {"timestamp": "created_time", "direction": "ascending"}

    @staticmethod
    def created_time_descending() -> dict[str, str]:
        """Sort by created time in descending order."""
        return {"timestamp": "created_time", "direction": "descending"}

    @staticmethod
    def last_edited_ascending() -> dict[str, str]:
        """Sort by last edited time in ascending order."""
        return {"timestamp": "last_edited_time", "direction": "ascending"}

    @staticmethod
    def last_edited_descending() -> dict[str, str]:
        """Sort by last edited time in descending order."""
        return {"timestamp": "last_edited_time", "direction": "descending"}
