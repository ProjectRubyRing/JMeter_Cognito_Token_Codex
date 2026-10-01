import json
from pathlib import Path
import sys
import unittest
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apikit.spec import Document, InputError, Samples, load_document, load_json
from apikit.jmx import make_requests, serialize_parameter, set_header
from apikit.plan import build_jmx, authorization_scopes
from cognito_jmeter import main


def minimal(schema=None, version="3.0.3"):
    operation = {"operationId": "send", "responses": {"200": {"description": "ok"}}}
    if schema is not None:
        operation["requestBody"] = {"content": {"application/json": {"schema": schema}}}
    return {"openapi": version, "info": {"title": "test", "version": "1"}, "paths": {"/test": {"post": operation}}}


def work():
    path = ROOT / ".work" / ("unit-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    return path


class SpecTests(unittest.TestCase):
    def test_json_yaml_equivalent(self):
        self.assertEqual(load_document(ROOT / "examples/openapi.sample.yaml"), load_document(ROOT / "examples/openapi.sample.json"))

    def test_supported_versions(self):
        for version in ("3.0.2", "3.0.3", "3.0.4", "3.1.0", "3.1.1"):
            self.assertEqual(1, len(Document(minimal(version=version)).operations()))
        for version in ("2.0", "3.2.0", "garbage"):
            with self.assertRaises(InputError):
                Document(minimal(version=version))

    def test_bad_document(self):
        data = minimal()
        del data["info"]
        with self.assertRaises(InputError):
            Document(data)

    def test_local_refs_and_external_ref_rejection(self):
        for version in ("3.0.3", "3.1.0"):
            for ref in ("https://example.com/s.json", "other.yaml#/A", "file:///secret", "#/missing"):
                with self.assertRaises(InputError):
                    Document(minimal({"$ref": ref}, version))
        doc = Document(load_document(ROOT / "examples/openapi.sample.yaml"))
        self.assertEqual("object", doc.resolve({"$ref": "#/components/schemas/Item"})["type"])

    def test_yaml_duplicates_alias_and_dates(self):
        path = work() / "bad.yaml"
        for text in ("a: 1\na: 2\n", "a: &a [1]\nb: *a\n"):
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(InputError):
                load_document(path)
        path.write_text("on: 2026-10-01\nactive: true\n", encoding="utf-8")
        self.assertEqual({"on": "2026-10-01", "active": True}, load_document(path))

    def test_json_duplicate_and_nonfinite(self):
        path = work() / "bad.json"
        for text in ('{"a":1,"a":2}', '{"a":NaN}'):
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(InputError):
                load_json(path)

    def test_any_is_not_silently_omitted(self):
        data = minimal()
        data["paths"]["/test"]["x-amazon-apigateway-any-method"] = {}
        with self.assertRaises(InputError):
            Document(data).operations()


class DataTests(unittest.TestCase):
    def test_generated_constraints_many_seeds(self):
        schemas = [
            {"type": "integer", "minimum": 1, "maximum": 19, "multipleOf": 3},
            {"type": "number", "minimum": 0.1, "maximum": 1, "multipleOf": 0.1},
            {"type": "string", "pattern": "^[A-Z]{2}[0-9]{3}$"},
            {"type": "array", "minItems": 2, "maxItems": 3, "uniqueItems": True, "items": {"type": "string", "enum": ["a", "b", "c"]}},
            {"allOf": [{"type": "integer", "minimum": 2}, {"type": "integer", "maximum": 4}]},
            {"oneOf": [{"type": "integer"}, {"type": "boolean"}]},
        ]
        for schema in schemas:
            doc = Document(minimal(schema))
            for seed in range(12):
                doc.check_value(schema, Samples(doc, seed).make(schema), "test")

    def test_31_nullable_const_exclusive_ref_sibling(self):
        for schema in ({"type": ["integer", "null"], "exclusiveMinimum": 9, "exclusiveMaximum": 12},
                       {"const": "fixed"}, {"type": "null"}):
            doc = Document(minimal(schema, "3.1.0"))
            doc.check_value(schema, Samples(doc, 1).make(schema), "test")
        data = minimal({"$ref": "#/components/schemas/N", "maximum": 12}, "3.1.0")
        data["components"] = {"schemas": {"N": {"type": "integer", "minimum": 10}}}
        doc = Document(data)
        self.assertIn(Samples(doc, 5).make(data["paths"]["/test"]["post"]["requestBody"]["content"]["application/json"]["schema"]), range(10, 13))

    def test_readonly_and_seed(self):
        schema = {"type": "object", "required": ["id", "name"], "properties": {"id": {"type": "integer", "readOnly": True}, "name": {"type": "string"}}}
        for version in ("3.0.3", "3.1.0"):
            doc = Document(minimal(schema, version))
            a = Samples(doc, 42).make(schema)
            self.assertNotIn("id", a)
            self.assertEqual(a, Samples(doc, 42).make(schema))
            self.assertNotEqual(a, Samples(doc, 43).make(schema))

    def test_invalid_override_and_impossible_data(self):
        schema = {"type": "integer", "minimum": 1, "maximum": 2}
        doc = Document(minimal(schema))
        with self.assertRaises(InputError):
            make_requests(doc, doc.operations(), overrides={"send": {"body": "not-number"}})
        schema = {"type": "array", "minItems": 101, "items": {"type": "integer"}}
        with self.assertRaises(InputError):
            Samples(Document(minimal(schema)), 1).make(schema)

    def test_31_boolean_properties_and_tuple_items(self):
        schema = {"type": "object", "required": ["free"], "properties": {"free": True}}
        doc = Document(minimal(schema, "3.1.0"))
        self.assertIn("free", Samples(doc, 1).make(schema))
        schema = {"type": "array", "minItems": 2, "maxItems": 2,
                  "prefixItems": [{"type": "integer"}, {"const": "ok"}], "items": False}
        doc = Document(minimal(schema, "3.1.0"))
        value = Samples(doc, 1).make(schema)
        self.assertEqual("ok", value[1])
        doc.check_value(schema, value, "tuple")

    def test_formats(self):
        for fmt in ("date", "date-time", "email", "uuid", "ipv4", "ipv6", "uri", "byte"):
            schema = {"type": "string", "format": fmt}
            doc = Document(minimal(schema))
            doc.check_value(schema, Samples(doc, 2).make(schema), fmt)


class RequestTests(unittest.TestCase):
    def test_serialization(self):
        self.assertEqual([("tags", "a"), ("tags", "b")], serialize_parameter({"name": "tags", "in": "query"}, ["a", "b"]))
        self.assertEqual("a%2Fb%20c", serialize_parameter({"name": "id", "in": "path"}, "a/b c"))
        self.assertEqual([("f%5Bx%5D", "2")], serialize_parameter({"name": "f", "in": "query", "style": "deepObject", "explode": True}, {"x": 2}))

    def test_post_query_and_form(self):
        doc = Document(load_document(ROOT / "examples/openapi.sample.yaml"))
        requests, _ = make_requests(doc, doc.operations(), seed=42)
        req = next(r for r in requests if r["key"] == "searchItems")
        self.assertIn("trace", parse_qs(urlsplit(req["path"]).query))
        self.assertIn("keyword", parse_qs(req["body"]))

    def test_injection_and_secret_override(self):
        with self.assertRaises(InputError):
            set_header({}, "X-Test", "a\r\nOther: b")
        doc = Document(minimal({"type": "string"}))
        for values in ({"body": "${__groovy(1)}"}, {"header": {"Authorization": "secret"}}):
            with self.assertRaises(InputError):
                make_requests(doc, doc.operations(), overrides={"send": values})

    def test_unsupported_content(self):
        data = minimal({"type": "string"})
        data["paths"]["/test"]["post"]["requestBody"]["content"] = {"application/xml": {"schema": {"type": "string"}}}
        doc = Document(data)
        with self.assertRaises(InputError):
            make_requests(doc, doc.operations())

    def test_security_scope_and_unsupported(self):
        doc = Document(load_document(ROOT / "examples/openapi.sample.yaml"))
        self.assertEqual(["demo-api/read", "demo-api/write"], authorization_scopes(doc, doc.operations()))
        data = minimal()
        data["components"] = {"securitySchemes": {"basic": {"type": "http", "scheme": "basic"}}}
        data["security"] = [{"basic": []}]
        doc = Document(data)
        with self.assertRaises(InputError):
            authorization_scopes(doc, doc.operations())


class PlanTests(unittest.TestCase):
    def test_hash_trees_defaults_and_secret_absence(self):
        doc = Document(load_document(ROOT / "examples/openapi.sample.yaml"))
        requests, _ = make_requests(doc, doc.operations(), seed=42)
        raw = build_jmx(requests, {"listItems"})
        root = ET.fromstring(raw)
        for tree in root.iter("hashTree"):
            children = list(tree)
            self.assertEqual(0, len(children) % 2)
            for index in range(1, len(children), 2):
                self.assertEqual("hashTree", children[index].tag)
        branches = root.findall(".//GenericController")
        self.assertEqual(1, sum(b.get("enabled") == "true" for b in branches))
        self.assertEqual(5, len(root.findall(".//JSONPostProcessor")))
        self.assertEqual("false", root.find(".//value/responseData").text)
        self.assertIn(b"COGNITO_CLIENT_SECRET", raw)
        self.assertIn(b"localhost", raw)
        self.assertIn(b"XXXXXXXXXXXX", raw)

    def test_output_selection_and_no_overwrite(self):
        path = work() / "output"
        args = ["generate", "--input", str(ROOT / "examples/openapi.sample.yaml"), "--out", str(path), "--seed", "42"]
        self.assertEqual(0, main(args))
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(["listItems"], manifest["enabled_operations"])
        self.assertEqual(2, main(args))
        all_path = work() / "all"
        self.assertEqual(0, main(args[:args.index("--out")] + ["--out", str(all_path), "--enable-all"]))
        self.assertEqual(5, len(json.loads((all_path / "manifest.json").read_text())["enabled_operations"]))


if __name__ == "__main__":
    unittest.main()
