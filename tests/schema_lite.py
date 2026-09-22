"""Minimal JSON Schema (draft 2020-12 subset) validator, standard library only.

GPOS schemas deliberately use a small keyword subset so the framework can be
validated without third-party dependencies. Any keyword outside the subset
raises UnsupportedKeyword instead of being silently ignored, so a schema change
cannot quietly weaken validation.

If the optional `jsonschema` package is installed, validate_framework.py also
cross-checks every result against it, with its date-time format checker on.

`format` is assertive here: "date-time" values must be RFC 3339 date-times
(e.g. 2026-01-01T12:00:00Z). Any other format name raises UnsupportedKeyword.
"""

import datetime
import re

ANNOTATIONS = {"$schema", "$id", "$comment", "title", "description", "default", "examples", "$defs"}
SUPPORTED = ANNOTATIONS | {
    "$ref", "type", "enum", "const", "required", "properties", "additionalProperties",
    "items", "minItems", "maxItems", "uniqueItems", "contains", "minLength", "pattern",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "format",
}
SUPPORTED_FORMATS = {"date-time"}

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?([Zz]|[+-](\d{2}):(\d{2}))$"
)


def is_rfc3339_datetime(value):
    """RFC 3339 date-time with a real calendar date and valid clock values.

    Deliberately matches the reference checker (rfc3339-validator): 'T' separator, explicit
    offset required, leap second :60 rejected.
    """
    m = _RFC3339.match(value)
    if not m:
        return False
    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    try:
        datetime.date(year, month, day)
    except ValueError:
        return False
    if hour > 23 or minute > 59 or second > 59:
        return False
    if m.group(9) is not None and (int(m.group(9)) > 23 or int(m.group(10)) > 59):
        return False
    return True


class UnsupportedKeyword(Exception):
    pass


def _is_type(value, name):
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "null":
        return value is None
    raise UnsupportedKeyword(f"type {name!r}")


def _equal(a, b):
    # JSON equality: booleans are not numbers.
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


class Validator:
    def __init__(self, schema):
        self.root = schema
        self._check_keywords(schema)

    def _check_keywords(self, node):
        """Reject any schema keyword this validator does not implement."""
        if not isinstance(node, dict):
            return
        for key, sub in node.items():
            if key not in SUPPORTED:
                raise UnsupportedKeyword(key)
            if key == "format" and sub not in SUPPORTED_FORMATS:
                raise UnsupportedKeyword(f"format {sub!r}")
            if key in ("properties", "$defs"):
                for child in sub.values():
                    self._check_keywords(child)
            elif key in ("allOf", "anyOf", "oneOf"):
                for child in sub:
                    self._check_keywords(child)
            elif key in ("items", "contains", "not", "if", "then", "else", "additionalProperties"):
                self._check_keywords(sub)

    def _resolve(self, ref):
        if not ref.startswith("#/"):
            raise UnsupportedKeyword(f"non-local $ref {ref!r}")
        node = self.root
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def errors(self, instance):
        out = []
        self._validate(instance, self.root, "", out)
        return out

    def is_valid(self, instance):
        return not self.errors(instance)

    def _validate(self, inst, schema, path, out):
        if schema is True:
            return
        if schema is False:
            out.append((path, "false", "schema is false"))
            return
        err = lambda kw, msg: out.append((path or "/", kw, msg))

        if "$ref" in schema:
            self._validate(inst, self._resolve(schema["$ref"]), path, out)
        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_is_type(inst, t) for t in types):
                err("type", f"expected {schema['type']}")
                return
        if "enum" in schema and not any(_equal(inst, v) for v in schema["enum"]):
            err("enum", f"{inst!r} not in enum")
        if "const" in schema and not _equal(inst, schema["const"]):
            err("const", f"{inst!r} != {schema['const']!r}")

        if isinstance(inst, str):
            if "minLength" in schema and len(inst) < schema["minLength"]:
                err("minLength", "string too short")
            if "pattern" in schema and not re.search(schema["pattern"], inst):
                err("pattern", f"{inst!r} does not match {schema['pattern']}")
            if schema.get("format") == "date-time" and not is_rfc3339_datetime(inst):
                err("format", f"{inst!r} is not an RFC 3339 date-time")

        if isinstance(inst, dict):
            for key in schema.get("required", []):
                if key not in inst:
                    err("required", f"missing {key!r}")
            props = schema.get("properties", {})
            for key, value in inst.items():
                if key in props:
                    self._validate(value, props[key], f"{path}/{key}", out)
                elif "additionalProperties" in schema:
                    ap = schema["additionalProperties"]
                    if ap is False:
                        err("additionalProperties", f"unexpected {key!r}")
                    elif isinstance(ap, dict):
                        self._validate(value, ap, f"{path}/{key}", out)

        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                err("minItems", f"fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                err("maxItems", f"more than {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                seen = []
                for item in inst:
                    if any(_equal(item, s) for s in seen):
                        err("uniqueItems", f"duplicate {item!r}")
                    seen.append(item)
            if "items" in schema:
                for i, item in enumerate(inst):
                    self._validate(item, schema["items"], f"{path}/{i}", out)
            if "contains" in schema:
                if not any(not self._sub_errors(item, schema["contains"], path) for item in inst):
                    err("contains", "no item matches 'contains'")

        for sub in schema.get("allOf", []):
            self._validate(inst, sub, path, out)
        if "anyOf" in schema:
            if not any(not self._sub_errors(inst, s, path) for s in schema["anyOf"]):
                err("anyOf", "no subschema matches")
        if "oneOf" in schema:
            n = sum(1 for s in schema["oneOf"] if not self._sub_errors(inst, s, path))
            if n != 1:
                err("oneOf", f"{n} subschemas match")
        if "not" in schema and not self._sub_errors(inst, schema["not"], path):
            err("not", "matches forbidden subschema")
        if "if" in schema:
            if not self._sub_errors(inst, schema["if"], path):
                if "then" in schema:
                    self._validate(inst, schema["then"], path, out)
            elif "else" in schema:
                self._validate(inst, schema["else"], path, out)

    def _sub_errors(self, inst, schema, path):
        out = []
        self._validate(inst, schema, path, out)
        return out
