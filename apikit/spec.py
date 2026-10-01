"""Local-only OpenAPI loading and constraint-checked request data generation."""

from __future__ import annotations

import base64
import copy
import json
import random
import re
import string
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from urllib.parse import unquote

from openapi_schema_validator import OAS30WriteValidator, OAS31Validator
from openapi_spec_validator import OpenAPIV30SpecValidator, OpenAPIV31SpecValidator
from jsonschema import FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4, DRAFT202012
from jsonschema.validators import extend
import yaml
import rstr


METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class InputError(ValueError):
    """An actionable input or unsupported-feature error (CLI exit 2)."""


def pointer_token(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def walk(value, path="#"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + "/" + pointer_token(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + "/" + str(index))


def reference_nodes(value, path="#", context="object"):
    """Visit specification references, not '$ref' inside example business data."""
    if context == "literal":
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from reference_nodes(child, path + "/" + str(index), context)
    elif isinstance(value, dict):
        if context in ("map", "schema_map", "components"):
            for key, child in value.items():
                child_context = "schema" if context == "schema_map" else "object"
                if context == "components":
                    child_context = "schema_map" if key == "schemas" else "map"
                yield from reference_nodes(child, path + "/" + pointer_token(key), child_context)
            return
        if "$ref" in value:
            yield path, value
        for key, child in value.items():
            if key.startswith("x-") or key in ("example", "examples" if context == "schema" else "__unused__", "const", "default", "enum", "value", "security"):
                child_context = "literal"
            elif key == "components":
                child_context = "components"
            elif key in ("properties", "$defs", "patternProperties", "dependentSchemas"):
                child_context = "schema_map"
            elif key in ("schema", "items", "additionalProperties", "allOf", "oneOf", "anyOf", "not", "prefixItems", "contains", "if", "then", "else", "unevaluatedProperties"):
                child_context = "schema"
            elif key in ("paths", "responses", "content", "headers", "examples", "links", "callbacks", "encoding", "variables", "mapping"):
                child_context = "map"
            else:
                child_context = "object"
            yield from reference_nodes(child, path + "/" + pointer_token(key), child_context)


def load_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InputError(f"Duplicate JSON key: {key!r}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise InputError(f"Non-JSON numeric constant: {value}")

    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            return json.load(stream, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"Cannot read UTF-8 JSON {path}: {exc}") from exc


class Document:
    def __init__(self, data):
        if not isinstance(data, dict) or not re.fullmatch(r"3\.[01]\.\d+", str(data.get("openapi", ""))):
            raise InputError("Supported OpenAPI versions: 3.0.x and 3.1.x (not Swagger 2.0 / OpenAPI 3.2).")
        self.is31 = data["openapi"].startswith("3.1.")
        if self.is31:
            # Explicitly bound dialect support. No implicit remote resolution.
            for path, value in walk(data):
                if isinstance(value, dict) and any(k in value for k in ("$id", "$dynamicRef", "$anchor", "$dynamicAnchor")):
                    raise InputError(f"{path}: custom schema bases/dynamic references are not supported; bundle to local pointers.")
                if isinstance(value, dict) and "$schema" in value and value["$schema"] not in (
                    "https://json-schema.org/draft/2020-12/schema", "https://spec.openapis.org/oas/3.1/dialect/base"):
                    raise InputError(f"{path}: unsupported JSON Schema dialect.")
            if data.get("jsonSchemaDialect", "https://spec.openapis.org/oas/3.1/dialect/base") not in (
                "https://spec.openapis.org/oas/3.1/dialect/base", "https://json-schema.org/draft/2020-12/schema"):
                raise InputError("Unsupported jsonSchemaDialect.")

        self.data = data
        self.warnings = []
        # Reject retrieval before handing input to third-party validators. No network
        # or arbitrary local file is ever resolved from an input definition.
        checked = set()
        pending = list(reference_nodes(data))
        while pending:
            path, value = pending.pop()
            ref = value["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                raise InputError(f"{path}/$ref: only in-document #/... references are supported; bundle external references first.")
            target = self.lookup(ref)
            if ref not in checked:
                checked.add(ref)
                pending.extend(reference_nodes(target, ref))
            if len(value) > 1 and not self.is31:
                self.warn(f"{path}: OpenAPI 3.0 Reference Object ignores siblings of $ref.")
        try:
            error = next((OpenAPIV31SpecValidator if self.is31 else OpenAPIV30SpecValidator)(data).iter_errors(), None)
        except (ValueError, KeyError, RecursionError) as exc:
            raise InputError(f"OpenAPI validation could not complete ({type(exc).__name__}).") from exc
        if error is not None:
            location = "#/" + "/".join(pointer_token(p) for p in error.absolute_path)
            # Validator messages can include entire input values, including secrets.
            raise InputError(f"OpenAPI validation failed at {location}; rule={error.validator}. Check the required field/type/constraint at this location.")
        root = dict(data, **{"$id": "urn:apikit:document"})
        registry = Registry().with_resource("urn:apikit:document", Resource.from_contents(root, default_specification=DRAFT202012 if self.is31 else DRAFT4))
        formats = FormatChecker()
        base_validator = OAS31Validator if self.is31 else OAS30WriteValidator
        formats.checkers = dict(base_validator.FORMAT_CHECKER.checkers)
        # Version 0.6.3's binary checker expects Python bytes. Our JSON data file
        # represents raw upload bytes as a string, encoded as UTF-8 on the wire.
        # Keep all schema constraints; adapt only that representation boundary.
        formats.checkers["binary"] = (lambda value: isinstance(value, (str, bytes)), ())
        if self.is31:
            def write_required(validator, required, instance, schema):
                properties = schema.get("properties", {})
                active = []
                for key in required:
                    child = self.resolve(properties.get(key, {}))
                    if not isinstance(child, dict) or not child.get("readOnly", False):
                        active.append(key)
                yield from OAS31Validator.VALIDATORS["required"](validator, active, instance, schema)
            base_validator = extend(OAS31Validator, {"required": write_required})
        self.validator = base_validator(root, registry=registry, format_checker=formats)

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def lookup(self, ref):
        value = self.data
        try:
            for token in unquote(ref[2:]).split("/"):
                key = token.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise InputError(f"Unresolved local reference: {ref}") from exc
        return value

    def resolve(self, obj):
        seen = set()
        while isinstance(obj, dict) and "$ref" in obj:
            ref = obj["$ref"]
            if ref in seen:
                raise InputError(f"Reference-only cycle: {ref}")
            seen.add(ref)
            target = self.lookup(ref)
            siblings = {k: v for k, v in obj.items() if k not in ("$ref", "summary", "description")}
            if self.is31 and siblings:
                return {"allOf": [target, siblings]}
            obj = target
        return obj

    def valid(self, schema, value):
        return self.validator.evolve(schema=schema).is_valid(value)

    def check_value(self, schema, value, location):
        error = next(self.validator.evolve(schema=schema).iter_errors(value), None)
        if error:
            suffix = "/".join(pointer_token(p) for p in error.absolute_path)
            raise InputError(f"{location}/{suffix}: data violates schema rule '{error.validator}'.")

    def operations(self):
        result = []
        seen = set()
        for path, raw_item in self.data.get("paths", {}).items():
            if path.startswith("x-"):
                continue
            item = self.resolve(raw_item)
            if "x-amazon-apigateway-any-method" in item:
                raise InputError(f"{path}: ANY is not a concrete HTTP method. Define explicit methods before generation.")
            for method, operation in item.items():
                if method not in METHODS:
                    continue
                key = operation.get("operationId", method.upper() + " " + path)
                if key in seen:
                    raise InputError(f"Duplicate operation identifier: {key}")
                seen.add(key)
                parameters = {}
                for raw in item.get("parameters", []) + operation.get("parameters", []):
                    param = self.resolve(raw)
                    identity = param["name"].lower() if param["in"] == "header" else param["name"]
                    parameters[(param["in"], identity)] = param
                result.append({"key": key, "method": method.upper(), "path": path,
                               "operation": operation, "parameters": list(parameters.values()),
                               "security": operation.get("security", self.data.get("security", [])),
                               "servers": operation.get("servers", item.get("servers", self.data.get("servers", []))),
                               "pointer": "#/paths/" + pointer_token(path) + "/" + method})
                if operation.get("callbacks"):
                    self.warn(f"{key}: callbacks describe server-to-client calls; no receiving mock is generated.")
        if not result:
            raise InputError("No concrete HTTP operations are defined in paths.")
        return result


class Samples:
    """Bounded candidate generation, followed by an independent OAS validator."""

    def __init__(self, document, seed=None):
        self.doc = document
        self.rng = random.Random(seed)
        self.regex = rstr.Rstr(self.rng)

    def make(self, schema, location="schema", depth=0):
        last_error = None
        for _ in range(80):
            try:
                value = self.candidate(schema, depth)
                if self.doc.valid(schema, value):
                    return value
            except InputError as exc:
                last_error = exc
        detail = f" {last_error}" if last_error else ""
        raise InputError(f"{location}: could not generate conforming data after 80 attempts.{detail} Supply a validated value with --data-file, or simplify contradictory/unsupported constraints.")

    def merge(self, left, right):
        """Candidate aid only; all original allOf constraints are still validated."""
        result = copy.deepcopy(left)
        for key, value in right.items():
            if key == "properties":
                target = result.setdefault(key, {})
                for name, schema in value.items():
                    target[name] = {"allOf": [target[name], schema]} if name in target else schema
            elif key == "required":
                result[key] = list(dict.fromkeys(result.get(key, []) + value))
            elif key in ("minimum", "minLength", "minItems", "minProperties"):
                result[key] = max(result.get(key, value), value)
            elif key in ("maximum", "maxLength", "maxItems", "maxProperties"):
                result[key] = min(result.get(key, value), value)
            elif key == "enum" and key in result:
                result[key] = [item for item in result[key] if item in value]
            elif key == "items" and key in result:
                result[key] = {"allOf": [result[key], value]}
            elif key == "type" and key in result and result[key] != value:
                if result[key] in ("integer", "number") and value in ("integer", "number"):
                    result[key] = "integer"
                else:
                    raise InputError("Incompatible allOf types.")
            else:
                result[key] = value
        return result

    def shape(self, schema, depth=0):
        if depth > 16:
            raise InputError("Schema composition exceeds depth 16.")
        schema = self.doc.resolve(schema)
        if schema is True:
            return {}
        if schema is False:
            raise InputError("False schema admits no data.")
        result = {k: v for k, v in schema.items() if k not in ("allOf", "oneOf", "anyOf")}
        for child in schema.get("allOf", []):
            result = self.merge(result, self.shape(child, depth + 1))
        for keyword in ("oneOf", "anyOf"):
            if keyword in schema:
                result = self.merge(result, self.shape(self.rng.choice(schema[keyword]), depth + 1))
        return result

    def candidate(self, raw, depth):
        if depth > 12:
            schema = self.doc.resolve(raw)
            if isinstance(schema, dict) and (schema.get("nullable") or "null" in schema.get("type", [])):
                return None
            raise InputError("Required recursive schema exceeds depth 12.")
        schema = self.shape(raw)
        if "const" in schema:
            return copy.deepcopy(schema["const"])
        if "enum" in schema:
            values = schema["enum"]
            if not values:
                raise InputError("Empty enum intersection.")
            return copy.deepcopy(self.rng.choice(values))
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = self.rng.choice([k for k in kind if k != "null"] or ["null"])
        if kind == "null":
            return None
        if not kind:
            kind = "object" if "properties" in schema or "additionalProperties" in schema else "array" if "items" in schema else "string"
        if kind == "object":
            result = {}
            required = set(schema.get("required", []))
            props = schema.get("properties", {})
            maximum = schema.get("maxProperties", 100)
            for key in sorted(props, key=lambda p: (p not in required, p)):
                prop = self.doc.resolve(props[key])
                if isinstance(prop, dict) and prop.get("readOnly"):
                    continue
                if key not in required and (depth >= 5 or len(result) >= maximum):
                    continue
                result[key] = self.candidate(props[key], depth + 1)
            missing = required - props.keys()
            additional = schema.get("additionalProperties", {})
            if additional is False and missing:
                raise InputError("Required properties prohibited by additionalProperties.")
            for key in sorted(missing):
                result[key] = self.candidate(additional if isinstance(additional, dict) else {}, depth + 1)
            minimum = schema.get("minProperties", 0)
            if minimum > 100:
                raise InputError("minProperties exceeds generation limit 100.")
            while len(result) < minimum:
                if additional is False:
                    raise InputError("minProperties cannot be satisfied.")
                key = "extra_" + str(self.rng.randrange(1000000))
                result[key] = self.candidate(additional if isinstance(additional, dict) else {}, depth + 1)
            return result
        if kind == "array":
            minimum = schema.get("minItems", 0)
            maximum = schema.get("maxItems", max(minimum, 3))
            if minimum > 100 or minimum > maximum:
                raise InputError("Invalid/too large array bounds (generation limit 100).")
            count = self.rng.randint(max(minimum, min(1, maximum)), min(maximum, max(minimum, 3)))
            result = []
            for index in range(count):
                for attempt in range(80):
                    prefix = schema.get("prefixItems", [])
                    item = self.candidate(prefix[index] if index < len(prefix) else schema.get("items", {}), depth + 1)
                    if not schema.get("uniqueItems") or item not in result:
                        result.append(item)
                        break
                else:
                    raise InputError("Cannot satisfy uniqueItems.")
            return result
        if kind == "boolean":
            return bool(self.rng.getrandbits(1))
        if kind in ("integer", "number"):
            return self.number(schema, kind)
        if kind == "string":
            return self.text(schema)
        raise InputError(f"Unsupported schema type: {kind}")

    def number(self, schema, kind):
        schema = dict(schema)
        if self.doc.is31:
            for bound, exclusive in (("minimum", "exclusiveMinimum"), ("maximum", "exclusiveMaximum")):
                if exclusive in schema:
                    current = schema.get(bound)
                    value = schema[exclusive]
                    strict = current is None or (value >= current if bound == "minimum" else value <= current)
                    schema[bound] = value if strict else current
                    schema[exclusive] = strict
        lo = Decimal(str(schema.get("minimum", min(0, schema.get("maximum", 100) - 100))))
        hi = Decimal(str(schema.get("maximum", max(100, schema.get("minimum", 0) + 100))))
        if schema.get("format") in ("int32", "int64"):
            bits = int(schema["format"][3:])
            lo, hi = max(lo, Decimal(-(2 ** (bits - 1)))), min(hi, Decimal(2 ** (bits - 1) - 1))
        step = Decimal(str(schema.get("multipleOf", 1 if kind == "integer" else "0.01")))
        if step <= 0:
            raise InputError("multipleOf must be positive.")
        if kind == "integer" and step != step.to_integral_value():
            # A reduced rational a/b has integer multiples at multiples of a.
            from fractions import Fraction
            step = Decimal(Fraction(step).numerator)
        lower = int((lo / step).to_integral_value(rounding=ROUND_CEILING))
        upper = int((hi / step).to_integral_value(rounding=ROUND_FLOOR))
        if schema.get("exclusiveMinimum") and Decimal(lower) * step <= lo:
            lower += 1
        if schema.get("exclusiveMaximum") and Decimal(upper) * step >= hi:
            upper -= 1
        if lower > upper:
            # Narrow number intervals without multipleOf need a finer step.
            if kind == "number" and "multipleOf" not in schema and lo < hi:
                return float(lo + (hi - lo) * Decimal(self.rng.randrange(1, 1000)) / 1000)
            raise InputError("No value satisfies the numeric bounds.")
        value = Decimal(self.rng.randint(lower, upper)) * step
        return int(value) if kind == "integer" else float(value)

    def text(self, schema):
        minimum = schema.get("minLength", 0)
        maximum = schema.get("maxLength", max(minimum, 16))
        if minimum > 4096 or minimum > maximum:
            raise InputError("String bounds cannot be generated (limit 4096).")
        fmt = schema.get("format", "")
        token = "".join(self.rng.choices(string.ascii_letters + string.digits, k=12))
        day = date(2020, 1, 1) + timedelta(days=self.rng.randrange(3650))
        formats = {
            "date": day.isoformat(),
            "date-time": datetime(day.year, day.month, day.day, self.rng.randrange(24), self.rng.randrange(60), self.rng.randrange(60), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
            "uuid": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
            "email": token.lower() + "@example.com",
            "hostname": token.lower() + ".example.com",
            "ipv4": "192.0.2." + str(self.rng.randint(1, 254)),
            "ipv6": "2001:db8::" + format(self.rng.randrange(1, 65536), "x"),
            "uri": "https://example.com/" + token,
            "url": "https://example.com/" + token,
            "byte": base64.b64encode(token.encode()).decode(),
        }
        if "pattern" in schema:
            pattern = schema["pattern"]
            bounds = re.findall(r"\{(\d+)(?:,(\d*))?\}", pattern)
            if len(pattern) > 1024 or any(int(n) > 4096 for pair in bounds for n in pair if n):
                raise InputError("Regex exceeds generation limit.")
            try:
                value = self.regex.xeger(pattern)
            except (ValueError, KeyError, re.error, OverflowError) as exc:
                raise InputError("Unsupported regular expression; supply data explicitly.") from exc
            if len(value) > 4096:
                raise InputError("Generated regex text exceeds 4096 characters.")
            return value
        if fmt in formats:
            return formats[fmt]
        if fmt not in ("", "password", "binary"):
            self.doc.warn(f"Custom format '{fmt}': only the known schema constraints are checked; application semantics require real data.")
        count = self.rng.randint(max(minimum, min(8, maximum)), min(maximum, max(minimum, 16)))
        return "".join(self.rng.choices(string.ascii_letters + string.digits, k=count))


def load_document(path):
    """Read UTF-8 JSON/YAML without external retrieval or YAML alias expansion."""
    path = Path(path)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise InputError("Input exceeds the 5 MiB document limit.")
    if path.suffix.lower() not in (".yaml", ".yml"):
        return load_json(path)

    class Loader(yaml.SafeLoader):
        pass

    Loader.yaml_implicit_resolvers = {
        key: [(tag, regexp) for tag, regexp in rules if tag not in
              ("tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:bool")]
        for key, rules in Loader.yaml_implicit_resolvers.items()
    }
    Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), list("tTfF"))

    def mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if type(key) is int:
                key = str(key)
            if not isinstance(key, str):
                raise InputError("YAML mapping keys must be strings (HTTP integer status keys are accepted).")
            if key in result:
                raise InputError("Duplicate YAML mapping key.")
            result[key] = loader.construct_object(value_node)
        return result

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        text = path.read_text(encoding="utf-8-sig")
        for event in yaml.parse(text):
            if isinstance(event, yaml.events.AliasEvent):
                raise InputError("YAML aliases are unsupported; expand aliases before generation.")
        data = yaml.load(text, Loader=Loader)
        # JSON serialization also rejects non-finite YAML numbers and unsupported tags.
        return json.loads(json.dumps(data, allow_nan=False))
    except (yaml.YAMLError, UnicodeError, TypeError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("Invalid UTF-8 YAML/JSON-compatible data.") from exc
