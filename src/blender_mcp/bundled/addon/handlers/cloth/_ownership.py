"""Shared ownership-tagging helpers for cloth handlers."""

from __future__ import annotations

import contextlib
import json
import uuid

from .inspection_and_setup import _MCP_SCHEMA_VERSION, _OWNERSHIP_PREFIX


def _tag_owned_component(obj, modifier, role, simulation_id=None, source_mapping=None):
    component_id = uuid.uuid4().hex
    simulation_id = simulation_id or component_id
    property_name = f"{_OWNERSHIP_PREFIX}_component_{component_id}"
    record = {
        "owned": True,
        "simulation_id": simulation_id,
        "role": role,
        "modifier": modifier.name,
        "schema_version": _MCP_SCHEMA_VERSION,
    }
    if source_mapping is not None:
        record["source_mapping"] = source_mapping
    obj[property_name] = json.dumps(record, sort_keys=True)
    return {"object_property": property_name, **record}


def _tag_owned_object(obj, role, simulation_id, source_mapping=None):
    component_id = uuid.uuid4().hex
    property_name = f"{_OWNERSHIP_PREFIX}_component_{component_id}"
    record = {
        "owned": True,
        "simulation_id": simulation_id,
        "role": role,
        "object": obj.name,
        "schema_version": _MCP_SCHEMA_VERSION,
    }
    if source_mapping is not None:
        record["source_mapping"] = source_mapping
    obj[property_name] = json.dumps(record, sort_keys=True)
    return {"object_property": property_name, **record}


def _tag_owned_membership(obj, collection, simulation_id=None):
    simulation_id = simulation_id or uuid.uuid4().hex
    property_name = f"{_OWNERSHIP_PREFIX}_component_{simulation_id}"
    record = {
        "owned": True,
        "simulation_id": simulation_id,
        "role": "collision_membership",
        "collection": collection.name,
        "schema_version": _MCP_SCHEMA_VERSION,
    }
    obj[property_name] = json.dumps(record, sort_keys=True)
    return {"object_property": property_name, **record}


def _remove_custom_property(obj, property_name):
    if property_name in obj:
        del obj[property_name]


def _owned_component_records(obj):
    records = []
    for key, value in obj.items():
        if not key.startswith(f"{_OWNERSHIP_PREFIX}_component_"):
            continue
        with contextlib.suppress(TypeError, json.JSONDecodeError):
            records.append({"object_property": key, **json.loads(value)})
    return records


def _remove_owned_component_record(obj, role, modifier_name):
    for record in _owned_component_records(obj):
        if record.get("role") == role and record.get("modifier") == modifier_name:
            del obj[record["object_property"]]
            return record
    return None


def _owned_membership_record(obj, collection_name):
    return next(
        (
            record
            for record in _owned_component_records(obj)
            if record.get("role") == "collision_membership" and record.get("collection") == collection_name
        ),
        None,
    )
