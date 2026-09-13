"""Lightweight standard-library JSON schema validator for Context Forge contract tests."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


class SchemaValidationError(Exception):
    pass


def load_schema(schema_name: str) -> dict[str, Any]:
    path = SCHEMAS_DIR / schema_name
    return json.loads(path.read_text(encoding="utf-8"))


def validate_json_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate a python data structure against basic JSON schema constructs without external deps."""
    # Resolve $ref if present
    if "$ref" in schema:
        ref_name = schema["$ref"]
        ref_schema = load_schema(ref_name)
        validate_json_schema(instance, ref_schema, path)
        return

    # Check type
    expected_type = schema.get("type")
    if expected_type:
        type_matches = False
        allowed_types = [expected_type] if isinstance(expected_type, str) else expected_type
        for t in allowed_types:
            if t == "object" and isinstance(instance, dict):
                type_matches = True
            elif t == "array" and isinstance(instance, (list, tuple)):
                type_matches = True
            elif t == "string" and isinstance(instance, str):
                type_matches = True
            elif t == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
                type_matches = True
            elif t == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool):
                type_matches = True
            elif t == "boolean" and isinstance(instance, bool):
                type_matches = True
            elif t == "null" and instance is None:
                type_matches = True

        if not type_matches:
            raise SchemaValidationError(f"Expected type {expected_type} at {path}, got {type(instance).__name__}")

    # Check enum
    if "enum" in schema:
        if instance not in schema["enum"]:
            raise SchemaValidationError(f"Value '{instance}' at {path} not in enum {schema['enum']}")

    # Check pattern
    if "pattern" in schema and isinstance(instance, str):
        if not re.search(schema["pattern"], instance):
            raise SchemaValidationError(f"Value '{instance}' at {path} does not match pattern '{schema['pattern']}'")

    # Check min/max for numbers
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"Value {instance} at {path} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"Value {instance} at {path} > maximum {schema['maximum']}")

    # Check minLength for strings
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaValidationError(f"String length {len(instance)} at {path} < minLength {schema['minLength']}")

    # Check object properties & required
    if isinstance(instance, dict) and (expected_type == "object" or "properties" in schema or "required" in schema):
        required = schema.get("required", [])
        for req in required:
            if req not in instance:
                raise SchemaValidationError(f"Missing required property '{req}' at {path}")

        props = schema.get("properties", {})
        for k, v in instance.items():
            if k in props:
                validate_json_schema(v, props[k], f"{path}.{k}")

    # Check array items
    if isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for idx, item in enumerate(instance):
            validate_json_schema(item, item_schema, f"{path}[{idx}]")
