"""Reproducing test for ISS-003 — the Database model doesn't expose schema
properties, forcing callers to drop to client._notion.databases.retrieve().
After the fix, Database.from_api_response parses `properties` and exposes
`.properties` plus get_property_names()/get_property_type().
"""

from __future__ import annotations

from notion_ops.models.database import Database
from notion_ops.models.properties import PropertyType


def _api_db() -> dict:
    return {
        "id": "db-1",
        "title": [{"plain_text": "My DB"}],
        "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": "2026-01-01T00:00:00.000Z",
        "properties": {
            "Name": {"type": "title", "title": {}},
            "Status": {"type": "select", "select": {"options": []}},
        },
    }


def test_iss_003_database_exposes_properties():
    db = Database.from_api_response(_api_db())
    assert "Name" in db.properties
    assert db.properties["Name"].type == PropertyType("title")


def test_iss_003_database_property_accessors():
    db = Database.from_api_response(_api_db())
    assert set(db.get_property_names()) == {"Name", "Status"}
    assert db.get_property_type("Status") == PropertyType("select")
    assert db.get_property_type("Missing") is None


def test_iss_003_database_without_properties_defaults_empty():
    data = _api_db()
    del data["properties"]
    db = Database.from_api_response(data)
    assert db.properties == {}
    assert db.get_property_names() == []
