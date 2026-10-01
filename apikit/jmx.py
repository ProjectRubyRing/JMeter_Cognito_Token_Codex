"""Serialize requests and construct native, plugin-free JMeter test plans."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from .spec import InputError, Samples


def scalar(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (str, int, float)):
        return str(value)
    raise InputError("Nested objects/arrays cannot be serialized as a scalar parameter.")


def safe_literal(value):
    value = str(value)
    if "${" in value:
        raise InputError("Request data contains JMeter function syntax '${'. Replace it with literal test data before generation.")
    if any(ord(c) < 32 and c not in "\t\r\n" for c in value):
        raise InputError("Request contains an XML-incompatible control character.")
    return value


def encoded(value):
    return quote(scalar(value), safe="")


def serialize_parameter(parameter, value):
    name, location = parameter["name"], parameter["in"]
    style = parameter.get("style", "simple" if location in ("path", "header") else "form")
    explode = parameter.get("explode", style == "form")
    if parameter.get("allowReserved"):
        raise InputError(f"{name}: allowReserved=true requires manual serialization and is not supported.")
    atom = scalar if location == "header" else encoded
    key = name if location == "header" else quote(name, safe="")
    is_list, is_object = isinstance(value, list), isinstance(value, dict)
    values = [atom(v) for v in value] if is_list else []
    pairs = [(atom(k), atom(v)) for k, v in value.items()] if is_object else []
    flattened = [piece for pair in pairs for piece in pair]
    if location in ("path", "header"):
        if style == "simple":
            return ",".join(k + "=" + v for k, v in pairs) if is_object and explode else ",".join(flattened if is_object else values) if is_list or is_object else atom(value)
        if location == "path" and style == "label":
            pieces = [k + "=" + v for k, v in pairs] if is_object and explode else flattened if is_object else values if is_list else [atom(value)]
            return "." + ("." if explode else ",").join(pieces)
        if location == "path" and style == "matrix":
            if is_object and explode:
                return "".join(";" + k + "=" + v for k, v in pairs)
            if is_list and explode:
                return "".join(";" + key + "=" + v for v in values)
            text = ",".join(flattened if is_object else values) if is_list or is_object else atom(value)
            return ";" + key + ("=" + text if text else "")
        raise InputError(f"{name}: unsupported {location} style '{style}'.")
    if location == "cookie":
        if is_list or is_object or style != "form":
            raise InputError(f"{name}: only scalar form cookie parameters are supported.")
        return [(key, encoded(value))]
    if style == "deepObject":
        if not is_object or not explode:
            raise InputError(f"{name}: deepObject requires a flat object and explode=true.")
        return [(key + "%5B" + k + "%5D", v) for k, v in pairs]
    if style in ("spaceDelimited", "pipeDelimited"):
        if not is_list or explode:
            raise InputError(f"{name}: {style} supports arrays with explode=false only.")
        return [(key, ("%20" if style == "spaceDelimited" else "%7C").join(values))]
    if style != "form":
        raise InputError(f"{name}: unsupported query style '{style}'.")
    if is_object:
        return pairs if explode else [(key, ",".join(flattened))]
    if is_list:
        return [(key, v) for v in values] if explode else [(key, ",".join(values))]
    return [(key, encoded(value))]


def set_header(headers, name, value):
    if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
        raise InputError(f"Invalid HTTP header name: {name!r}")
    value = safe_literal(scalar(value))
    if "\r" in value or "\n" in value:
        raise InputError(f"Header {name}: CR/LF is prohibited.")
    if any(ord(c) > 126 or ord(c) < 32 and c != "\t" for c in value):
        raise InputError(f"Header {name}: use printable ASCII values; other header encodings require manual preparation.")
    if name.lower() in ("host", "content-length", "transfer-encoding", "connection"):
        raise InputError(f"Header {name} is transport-controlled; remove it from custom headers.")
    for existing in list(headers):
        if existing.lower() == name.lower():
            del headers[existing]
    headers[name] = value


def body_text(doc, samples, content_type, media, data, key):
    base_type = content_type.split(";", 1)[0].lower()
    if base_type == "application/json" or base_type.endswith("+json"):
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")), content_type
    if base_type == "application/x-www-form-urlencoded":
        if not isinstance(data, dict):
            raise InputError(f"{key}: form request body must be an object.")
        pairs = []
        for name, value in data.items():
            if value == [] or value == {}:
                raise InputError(f"{key}/{name}: an empty form collection has no unambiguous wire representation.")
            encoding = media.get("encoding", {}).get(name, {})
            if "contentType" in encoding or encoding.get("headers"):
                doc.warn(f"{key}/{name}: encoding contentType/headers are ignored by OpenAPI for URL-encoded bodies.")
            param = dict(encoding, name=name, **{"in": "query"})
            pairs.extend(serialize_parameter(param, value))
        return "&".join(k + "=" + v for k, v in pairs), content_type
    if base_type == "multipart/form-data":
        if not isinstance(data, dict):
            raise InputError(f"{key}: multipart body must be an object.")
        boundary = "apikit_" + format(samples.rng.getrandbits(128), "032x")
        parts = []
        properties = samples.shape(media.get("schema", {})).get("properties", {})
        for name, value in data.items():
            if value == []:
                raise InputError(f"{key}/{name}: an empty multipart array would omit the field; prepare its representation manually.")
            if any(c in name for c in '\r\n"\\'):
                raise InputError(f"{key}: unsupported multipart field name.")
            schema = doc.resolve(properties.get(name, {}))
            encoding = media.get("encoding", {}).get(name, {})
            if any(k in encoding for k in ("style", "explode", "allowReserved")):
                raise InputError(f"{key}/{name}: custom multipart serialization requires manual preparation.")
            items = value if isinstance(value, list) else [value]
            if isinstance(value, list):
                schema = doc.resolve(schema.get("items", {}))
            for item in items:
                binary = schema.get("format") == "binary"
                ctype = encoding.get("contentType", "application/octet-stream" if binary else "application/json" if isinstance(item, (dict, list)) else "text/plain")
                if any(c in ctype for c in '\r\n') or "," in ctype:
                    raise InputError(f"{key}: multipart contentType must be one valid media type.")
                content = json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else scalar(item)
                if boundary in content:
                    raise InputError("Multipart boundary collision; regenerate with another seed.")
                disposition = 'Content-Disposition: form-data; name="' + name + '"'
                if binary:
                    disposition += '; filename="sample.bin"'
                lines = ["--" + boundary, disposition, "Content-Type: " + ctype]
                for hname, raw in encoding.get("headers", {}).items():
                    if hname.lower() in ("content-type", "content-disposition"):
                        continue
                    header = doc.resolve(raw)
                    hvalue = samples.make(header.get("schema", {}), key + "/multipart/" + hname)
                    checked = {}
                    set_header(checked, hname, hvalue)
                    lines.append(hname + ": " + scalar(hvalue))
                parts.append("\r\n".join(lines) + "\r\n\r\n" + content + "\r\n")
        return "".join(parts) + "--" + boundary + "--\r\n", "multipart/form-data; boundary=" + boundary
    if base_type == "text/plain" or base_type == "application/octet-stream":
        if not isinstance(data, str):
            raise InputError(f"{key}: {content_type} requires a string body.")
        return data, content_type
    raise InputError(f"{key}: unsupported body media type {content_type}. Supported: JSON, +json, text/plain, URL-encoded form, multipart, octet-stream (synthetic ASCII).")


def make_requests(doc, operations, seed=None, overrides=None, base_path=""):
    samples = Samples(doc, seed)
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise InputError("--data-file must contain an object keyed by operationId (or METHOD /path).")
    unknown = set(overrides) - {op["key"] for op in operations}
    if unknown:
        raise InputError("Unknown operation keys in --data-file: " + ", ".join(sorted(unknown)))
    if base_path and (not base_path.startswith("/") or any(c in base_path for c in "?#{}\r\n")):
        raise InputError("--base-path must be empty or an absolute path without query, fragment, or templates.")
    requests, saved = [], {}
    for op in operations:
        key, operation = op["key"], op["operation"]
        custom = overrides.get(key, {})
        if not isinstance(custom, dict) or set(custom) - {"path", "query", "header", "cookie", "body", "content_type"}:
            raise InputError(f"{key}: unknown data override fields.")
        values = {name: {} for name in ("path", "query", "header", "cookie")}
        for location in values:
            if not isinstance(custom.get(location, {}), dict):
                raise InputError(f"{key}/{location}: expected an object.")
        header_overrides = {}
        for name, value in custom.get("header", {}).items():
            if name.lower() in header_overrides:
                raise InputError(f"{key}: duplicate header overrides differing only in case.")
            header_overrides[name.lower()] = value
        handled_headers = set()
        path, query, cookies, headers = op["path"], [], [], {}
        if not path.startswith("/") or any(c in path for c in "?#\r\n"):
            raise InputError(f"{key}: path must be absolute without a query or fragment.")
        expected = {location: set() for location in values}
        for param in op["parameters"]:
            name, location = param["name"], param["in"]
            expected[location].add(name)
            if location == "header" and name.lower() in ("accept", "content-type", "authorization"):
                doc.warn(f"{key}: OpenAPI ignores parameter definitions for the {name} header. Configure media types/security or header overrides.")
                continue
            if location == "header" and name.lower() == "x-api-key":
                values[location][name] = "XXXXXXXXXXXX"
                continue
            schema = param.get("schema", {})
            content = param.get("content")
            if content:
                if len(content) != 1 or not next(iter(content)).endswith("json"):
                    raise InputError(f"{key}/{name}: parameter content supports one JSON media type only.")
                schema = next(iter(content.values())).get("schema", {})
            supplied = header_overrides if location == "header" else custom.get(location, {})
            lookup_name = name.lower() if location == "header" else name
            value = supplied[lookup_name] if lookup_name in supplied else samples.make(schema, key + "/" + location + "/" + name)
            doc.check_value(schema, value, key + "/" + location + "/" + name)
            values[location][name] = value
            wire_value = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if content else value
            rendered = serialize_parameter(param, wire_value)
            if param.get("required") and rendered == []:
                raise InputError(f"{key}/{name}: empty collection serialization would omit a required parameter.")
            if location == "path":
                path = path.replace("{" + name + "}", rendered)
            elif location == "query":
                query.extend(rendered)
            elif location == "cookie":
                cookies.extend(rendered)
            else:
                set_header(headers, name, rendered)
                handled_headers.add(name.lower())
        for location in ("path", "query", "cookie"):
            unknown = set(custom.get(location, {})) - expected[location]
            if unknown:
                raise InputError(f"{key}/{location}: unknown parameters: {', '.join(sorted(unknown))}")
        if re.search(r"[{}]", path):
            raise InputError(f"{key}: unresolved path parameter.")
        for name, value in custom.get("header", {}).items():
            if name.lower() != "x-api-key" and name.lower() not in handled_headers:
                set_header(headers, name, value)
                values["header"][name] = value
        if any(n.lower() == "authorization" for n in custom.get("header", {})):
            raise InputError(f"{key}: Authorization comes from Cognito at runtime; remove the data override.")
        if cookies:
            set_header(headers, "Cookie", "; ".join(k + "=" + v for k, v in cookies))
        # Fixed name and initial value take precedence over definition/override data.
        set_header(headers, "X-API-KEY", "XXXXXXXXXXXX")
        values["header"] = {k: v for k, v in values["header"].items() if k.lower() != "x-api-key"}
        values["header"]["X-API-KEY"] = "XXXXXXXXXXXX"
        body = None
        if "requestBody" in operation:
            if op["method"] in ("GET", "HEAD", "TRACE"):
                raise InputError(f"{key}: a {op['method']} requestBody needs manual semantics review for OpenAPI 3.0.")
            request_body = doc.resolve(operation["requestBody"])
            content = request_body["content"]
            preferred = [c for c in content if c == "application/json" or c.endswith("+json")]
            preferred += [c for c in content if c in ("application/x-www-form-urlencoded", "multipart/form-data", "text/plain", "application/octet-stream")]
            content_type = custom.get("content_type", preferred[0] if preferred else next(iter(content)))
            if content_type not in content:
                raise InputError(f"{key}: content_type is not defined in requestBody.content.")
            media = content[content_type]
            schema = media.get("schema", {})
            data = custom["body"] if "body" in custom else samples.make(schema, key + "/body")
            doc.check_value(schema, data, key + "/body")
            body, header_type = body_text(doc, samples, content_type, media, data, key)
            values.update(body=data, content_type=content_type)
            if "content-type" in header_overrides and header_overrides["content-type"] != header_type:
                raise InputError(f"{key}: Content-Type override disagrees with the serialized body. Select content_type instead; multipart boundaries are generated automatically.")
            set_header(headers, "Content-Type", header_type)
        elif "body" in custom or "content_type" in custom:
            raise InputError(f"{key}: body override provided for an operation without requestBody.")
        statuses = [status for status in operation["responses"] if re.fullmatch(r"[23](?:\d\d|XX)", status)]
        if not statuses:
            statuses = ["2XX"]
            doc.warn(f"{key}: no explicit success response; generated smoke assertion expects 2xx, not arbitrary default errors.")
        for status in statuses:
            response = doc.resolve(operation["responses"].get(status, {}))
            response_types = response.get("content", {})
            if response_types and not any(n.lower() == "accept" for n in headers):
                set_header(headers, "Accept", next(iter(response_types)))
                break
        wire_path = quote(base_path.rstrip("/") + path, safe="/%;=,:@!$&'()*+~-.")
        if query:
            wire_path += "?" + "&".join(k + "=" + v for k, v in query)
        request = {"key": key, "method": op["method"], "path": safe_literal(wire_path),
                   "headers": headers, "body": safe_literal(body) if body is not None else None,
                   "status_pattern": "^(?:" + "|".join(status.replace("XX", "[0-9]{2}") for status in statuses) + ")$",
                   "pointer": op["pointer"]}
        requests.append(request)
        saved[key] = values
    return requests, saved


def prop(parent, name, value, kind="stringProp"):
    element = ET.SubElement(parent, kind, name=name)
    element.text = str(value).lower() if isinstance(value, bool) else str(value)
    return element


def node(parent, tag, gui, name, enabled=True):
    element = ET.SubElement(parent, tag, guiclass=gui, testclass=tag, testname=name.replace("${", "$ {"), enabled=str(enabled).lower())
    return element, ET.SubElement(parent, "hashTree")


def arguments(parent, values, name="HTTPsampler.Arguments"):
    args = ET.SubElement(parent, "elementProp", name=name, elementType="Arguments")
    collection = ET.SubElement(args, "collectionProp", name="Arguments.arguments")
    for key, value in values:
        item = ET.SubElement(collection, "elementProp", name=key, elementType="HTTPArgument")
        prop(item, "HTTPArgument.always_encode", False, "boolProp")
        prop(item, "Argument.name", key)
        prop(item, "Argument.value", value)
        prop(item, "Argument.metadata", "=")
        prop(item, "HTTPArgument.use_equals", bool(key), "boolProp")
