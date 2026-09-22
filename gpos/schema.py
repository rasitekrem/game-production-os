"""Production JSON Schema validator (draft 2020-12 subset), standard library only.

GPOS schemas use a small, fixed keyword subset. This validator implements exactly that
subset and refuses anything else at load time (UnsupportedSchemaKeyword), so a schema
change can never silently weaken validation. `format: date-time` is assertive and checks
real RFC 3339 date-times.

This module is production code. It does not import the Phase-1 test helper
tests/schema_lite.py; parity between the two (and with the `jsonschema` package, when
installed) is asserted by tests/test_production_validator.py.
"""

import datetime
import re

from .errors import UnsupportedSchemaKeyword

ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "$comment", "title", "description", "default", "examples", "$defs"})
ASSERTION_KEYWORDS = frozenset({
    "$ref", "type", "enum", "const", "required", "properties", "additionalProperties",
    "items", "minItems", "maxItems", "uniqueItems", "contains", "minLength", "pattern",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "format",
})
SUPPORTED_KEYWORDS = ANNOTATION_KEYWORDS | ASSERTION_KEYWORDS
SUPPORTED_FORMATS = frozenset({"date-time"})
SUPPORTED_TYPES = frozenset({"object", "array", "string", "boolean", "integer", "number", "null"})

_SUBSCHEMA_KEYWORDS = ("items", "contains", "not", "if", "then", "else", "additionalProperties")
_SCHEMA_MAP_KEYWORDS = ("properties", "$defs")
_SCHEMA_LIST_KEYWORDS = ("allOf", "anyOf", "oneOf")

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?([Zz]|[+-](\d{2}):(\d{2}))$"
)


def is_rfc3339_datetime(value):
    """True for an RFC 3339 date-time with a real calendar date and valid clock values.

    The frozen GPOS contract (RFC 3339 §5.6, as the Phase-1 oracle implements it): 'T' or 't'
    separator, explicit offset 'Z', 'z' or +hh:mm, calendar-valid date, hour <= 23, minute <= 59,
    second <= 59 (no leap second), offset hour <= 23 and minute <= 59.

    Known external-checker divergence: the rfc3339-validator package, called directly, rejects
    lower-case 't'/'z'. GPOS follows its contract, not that library. (`jsonschema` upper-cases
    date-times before calling it, so the jsonschema cross-check agrees with the contract.)
    """
    if not isinstance(value, str):
        return False
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


def _json_equal(a, b):
    """JSON equality: booleans are never numbers; containers compare structurally."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    return a == b


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
    return value is None  # "null"; other names are rejected at load time


def _pointer_escape(key):
    return str(key).replace("~", "~0").replace("/", "~1")


class SchemaError:
    """One schema violation: JSON pointer into the instance, failing keyword, message."""

    __slots__ = ("path", "keyword", "message")

    def __init__(self, path, keyword, message):
        self.path, self.keyword, self.message = path, keyword, message

    def as_tuple(self):
        return (self.path, self.keyword, self.message)

    def __repr__(self):
        return f"SchemaError({self.path!r}, {self.keyword!r}, {self.message!r})"


class SchemaValidator:
    """Validator for one GPOS schema. Construction fails loudly on any unsupported keyword."""

    def __init__(self, schema, name="schema"):
        self.name = name
        self.root = schema
        self._check_schema(schema, "#")

    # ------------------------------------------------------------ load-time keyword check

    def _check_schema(self, node, where):
        if isinstance(node, bool):
            return
        if not isinstance(node, dict):
            raise UnsupportedSchemaKeyword(f"{self.name}: {where} is not a schema object")
        for key, sub in node.items():
            if key not in SUPPORTED_KEYWORDS:
                raise UnsupportedSchemaKeyword(f"{self.name}: unsupported keyword {key!r} at {where}")
            if key == "format" and sub not in SUPPORTED_FORMATS:
                raise UnsupportedSchemaKeyword(f"{self.name}: unsupported format {sub!r} at {where}")
            if key == "type":
                for t in (sub if isinstance(sub, list) else [sub]):
                    if t not in SUPPORTED_TYPES:
                        raise UnsupportedSchemaKeyword(f"{self.name}: unsupported type {t!r} at {where}")
            if key == "$ref":
                self._resolve(sub)
            if key in _SCHEMA_MAP_KEYWORDS:
                for child_name, child in sub.items():
                    self._check_schema(child, f"{where}/{key}/{child_name}")
            elif key in _SCHEMA_LIST_KEYWORDS:
                for i, child in enumerate(sub):
                    self._check_schema(child, f"{where}/{key}/{i}")
            elif key in _SUBSCHEMA_KEYWORDS:
                self._check_schema(sub, f"{where}/{key}")

    def _resolve(self, ref):
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise UnsupportedSchemaKeyword(f"{self.name}: non-local $ref {ref!r}")
        node = self.root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                raise UnsupportedSchemaKeyword(f"{self.name}: unresolvable $ref {ref!r}")
            node = node[part]
        return node

    # ------------------------------------------------------------ validation

    def errors(self, instance):
        """All violations, deterministically ordered by (path, keyword, message)."""
        out = []
        self._validate(instance, self.root, "", out)
        unique = {e.as_tuple(): e for e in out}
        return [unique[k] for k in sorted(unique)]

    def is_valid(self, instance):
        out = []
        self._validate(instance, self.root, "", out)
        return not out

    def _passes(self, inst, schema, path):
        out = []
        self._validate(inst, schema, path, out)
        return not out

    def _validate(self, inst, schema, path, out):
        if schema is True:
            return
        if schema is False:
            out.append(SchemaError(path or "/", "false", "no value is allowed here"))
            return

        def err(keyword, message):
            out.append(SchemaError(path or "/", keyword, message))

        if "$ref" in schema:
            self._validate(inst, self._resolve(schema["$ref"]), path, out)
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_is_type(inst, t) for t in names):
                err("type", f"expected {schema['type']}")
                return
        if "enum" in schema and not any(_json_equal(inst, v) for v in schema["enum"]):
            err("enum", f"{inst!r} is not one of the allowed values")
        if "const" in schema and not _json_equal(inst, schema["const"]):
            err("const", f"{inst!r} must be {schema['const']!r}")

        if isinstance(inst, str):
            if "minLength" in schema and len(inst) < schema["minLength"]:
                err("minLength", f"shorter than {schema['minLength']} characters")
            if "pattern" in schema and not re.search(schema["pattern"], inst):
                err("pattern", f"{inst!r} does not match {schema['pattern']}")
            if schema.get("format") == "date-time" and not is_rfc3339_datetime(inst):
                err("format", f"{inst!r} is not an RFC 3339 date-time")

        if isinstance(inst, dict):
            for key in schema.get("required", []):
                if key not in inst:
                    err("required", f"missing required property {key!r}")
            props = schema.get("properties", {})
            for key in sorted(inst):
                value = inst[key]
                child = f"{path}/{_pointer_escape(key)}"
                if key in props:
                    self._validate(value, props[key], child, out)
                elif "additionalProperties" in schema:
                    extra = schema["additionalProperties"]
                    if extra is False:
                        err("additionalProperties", f"unexpected property {key!r}")
                    elif isinstance(extra, dict):
                        self._validate(value, extra, child, out)

        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                err("minItems", f"fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                err("maxItems", f"more than {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                for i, item in enumerate(inst):
                    if any(_json_equal(item, prev) for prev in inst[:i]):
                        err("uniqueItems", f"duplicate item {item!r}")
            if "items" in schema:
                for i, item in enumerate(inst):
                    self._validate(item, schema["items"], f"{path}/{i}", out)
            if "contains" in schema and not any(self._passes(item, schema["contains"], path) for item in inst):
                err("contains", "no item matches the required 'contains' schema")

        for sub in schema.get("allOf", []):
            self._validate(inst, sub, path, out)
        if "anyOf" in schema and not any(self._passes(inst, s, path) for s in schema["anyOf"]):
            err("anyOf", "matches none of the allowed alternatives")
        if "oneOf" in schema:
            n = sum(1 for s in schema["oneOf"] if self._passes(inst, s, path))
            if n != 1:
                err("oneOf", f"must match exactly one alternative; matches {n}")
        if "not" in schema and self._passes(inst, schema["not"], path):
            err("not", "matches a forbidden schema")
        if "if" in schema:
            if self._passes(inst, schema["if"], path):
                if "then" in schema:
                    self._validate(inst, schema["then"], path, out)
            elif "else" in schema:
                self._validate(inst, schema["else"], path, out)
