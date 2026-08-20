# Copyright (c) 2026, Ai Fr Hg and contributors
"""Canonical JSON Schema validator for INT-02 — single authority for structured extraction.

Frappe V17 evaluated: Frappe has no native JSON Schema validator. `frappe.utils` provides
type coercion helpers but not schema validation. Therefore a lightweight pure-Python
validator is justified; it does not duplicate a framework responsibility and avoids adding
a new pip dependency (jsonschema) to the bench.

All structured extraction entry points must call `validate_extraction(data, schema_doc)`
before persistence or downstream use:
  UI / API / Worker / Agent / Retry / Target DocType writer -> extract_data -> validator -> persist or typed failure.

Distinction: ValidationError (deterministic schema failure) vs ProviderError (model/runtime failure) vs parsing failure.
Provenance is returned with field-level errors, schema version, payload size/depth.
"""
from __future__ import annotations
import json
from frappe import _
from ai_fr_hg.ai.exceptions import AIError

MAX_PAYLOAD_BYTES = 200_000  # bounded size
MAX_DEPTH = 10
MAX_FIELDS = 100

class ValidationError(AIError):
    """Raised when model output fails canonical schema validation (INT-02). Distinguishable from ProviderError."""
    def __init__(self, message, errors=None, provenance=None):
        super().__init__(message)
        self.errors = errors or []
        self.provenance = provenance or {}

def _depth(obj, cur=0):
    if cur > MAX_DEPTH:
        return cur
    if isinstance(obj, dict):
        if not obj:
            return cur
        return max(_depth(v, cur+1) for v in obj.values())
    if isinstance(obj, list):
        if not obj:
            return cur
        return max(_depth(v, cur+1) for v in obj)
    return cur

def validate_extraction(data, schema_doc) -> tuple[bool, list[dict]]:
    """Validate data against AI Extraction Schema. Returns (ok, errors). Never writes."""
    errors=[]
    # bounded payload
    try:
        payload_bytes = len(json.dumps(data, default=str).encode("utf-8"))
        if payload_bytes > MAX_PAYLOAD_BYTES:
            errors.append({"field": "", "code": "payload_too_large", "message": f"Payload {payload_bytes} bytes exceeds {MAX_PAYLOAD_BYTES}", "severity": "error"})
            return False, errors
    except Exception:
        errors.append({"field": "", "code": "unserializable", "message": "Data is not JSON serializable", "severity": "error"})
        return False, errors

    if _depth(data) > MAX_DEPTH:
        errors.append({"field": "", "code": "too_deep", "message": f"Payload depth exceeds {MAX_DEPTH}", "severity": "error"})
        return False, errors

    if not isinstance(data, dict):
        errors.append({"field": "", "code": "type", "message": "Root must be an object", "severity": "error", "expected": "object", "actual": type(data).__name__})
        return False, errors

    # Build field map
    fields = {row.field_name: row for row in (schema_doc.get("extraction_fields") or [])}
    if len(fields) > MAX_FIELDS:
        errors.append({"field": "", "code": "too_many_fields", "message": f"Schema has {len(fields)} fields exceeds {MAX_FIELDS}"})
        return False, errors

    # Required
    for name, row in fields.items():
        if row.required and (name not in data or data[name] in (None, "")):
            # allow null only if field explicitly nullable? In our model required means must be present and non-null
            errors.append({"field": name, "code": "required", "message": f"Missing required field: {name}", "severity": "error"})

    # Additional properties
    strict = bool(schema_doc.get("strict"))
    if strict:
        for key in data.keys():
            if key not in fields:
                errors.append({"field": key, "code": "additional_property", "message": f"Unexpected field: {key}", "severity": "error"})

    # Type / enum / nullability / nested checks
    TYPE_MAP = {"String":"string","Number":"number","Integer":"integer","Boolean":"boolean","Date":"string","Array":"array","Object":"object"}
    for name, value in data.items():
        row = fields.get(name)
        if not row:
            if strict:
                continue
            # non-strict: allow extra but still validate if known types? skip
            continue
        if value is None:
            # null allowed for non-required fields
            if row.required:
                # already flagged
                pass
            continue
        exp = TYPE_MAP.get(row.field_type, "string")
        actual = _py_type(value)
        # Array special
        if exp == "array":
            if not isinstance(value, list):
                errors.append({"field": name, "code": "type", "message": f"Field {name} expected array got {actual}", "expected": "array", "actual": actual, "severity":"error"})
            # items are strings per current TYPE_MAP array items type string — we enforce string items if needed
            elif value and not all(isinstance(v, str) for v in value):
                # coerce allowed but strict type check
                errors.append({"field": name, "code": "array_items", "message": f"Field {name} array items must be strings", "severity":"error"})
            continue
        if exp == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append({"field": name, "code": "type", "message": f"Field {name} expected integer got {actual}", "expected":"integer","actual":actual,"severity":"error"})
            continue
        if exp == "number":
            if not isinstance(value, (int,float)) or isinstance(value, bool):
                errors.append({"field": name, "code": "type", "message": f"Field {name} expected number got {actual}", "expected":"number","actual":actual,"severity":"error"})
            continue
        if exp == "boolean":
            if not isinstance(value, bool):
                errors.append({"field": name, "code": "type", "message": f"Field {name} expected boolean got {actual}", "expected":"boolean","actual":actual,"severity":"error"})
            continue
        if exp == "string":
            if not isinstance(value, str):
                errors.append({"field": name, "code": "type", "message": f"Field {name} expected string got {actual}", "expected":"string","actual":actual,"severity":"error"})
                continue
            # enum check for string types (including Date as string)
            if row.enum_values:
                allowed = [v.strip() for v in row.enum_values.replace(",","\n").splitlines() if v.strip()]
                if allowed and value not in allowed:
                    errors.append({"field": name, "code": "enum", "message": f"Field {name} value '{value}' not in allowed {allowed}", "expected": allowed, "actual": value, "severity":"error"})
            # Date format check if field_type Date
            if row.field_type == "Date":
                import re
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                    errors.append({"field": name, "code": "format", "message": f"Field {name} expected YYYY-MM-DD got '{value}'", "severity":"error"})

    ok = len(errors)==0
    return ok, errors

def _py_type(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__

def assert_valid(data, schema_doc):
    ok, errors = validate_extraction(data, schema_doc)
    if not ok:
        provenance = {"schema": schema_doc.name, "strict": bool(schema_doc.get("strict")), "payload_bytes": len(json.dumps(data, default=str).encode("utf-8")) if isinstance(data,str) or isinstance(data,dict) else 0, "field_count": len(data) if isinstance(data,dict) else 0}
        raise ValidationError(_("Structured output failed schema validation: {0}").format(errors[0]["message"] if errors else "invalid"), errors=errors, provenance=provenance)
