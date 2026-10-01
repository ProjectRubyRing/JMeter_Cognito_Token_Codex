"""Build a self-contained JMX with embedded Groovy and native JMeter components."""
import xml.etree.ElementTree as ET
from pathlib import Path
import re
from .jmx import prop, node, arguments
from .spec import InputError

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"


def script(parent, filename, title, kind="JSR223Sampler"):
    element, tree = node(parent, kind, "TestBeanGUI", title)
    for key, value in (("scriptLanguage", "groovy"), ("parameters", ""), ("filename", ""),
                       ("cacheKey", "true"), ("script", (RUNTIME / filename).read_text(encoding="utf-8"))):
        prop(element, key, value)
    return element, tree


def headers(parent, values):
    manager, _ = node(parent, "HeaderManager", "HeaderPanel", "Headers")
    collection = ET.SubElement(manager, "collectionProp", name="HeaderManager.headers")
    for key, value in values.items():
        item = ET.SubElement(collection, "elementProp", name="", elementType="Header")
        prop(item, "Header.name", key)
        prop(item, "Header.value", value)


def http(parent, title, method, path, body=None):
    sampler, tree = node(parent, "HTTPSamplerProxy", "HttpTestSampleGui", title)
    arguments(sampler, [("", body)] if body is not None else [])
    prop(sampler, "HTTPSampler.path", path)
    prop(sampler, "HTTPSampler.method", method)
    prop(sampler, "HTTPSampler.postBodyRaw", body is not None, "boolProp")
    for key, value in (("follow_redirects", False), ("auto_redirects", False),
                       ("use_keepalive", True), ("DO_MULTIPART_POST", False), ("image_parser", False)):
        prop(sampler, "HTTPSampler." + key, value, "boolProp")
    return sampler, tree


def authorization_scopes(doc, operations):
    schemes = doc.data.get("components", {}).get("securitySchemes", {})
    scope_set = set()
    for op in operations:
        if not op["security"]:
            continue
        for option in op["security"]:
            scopes, compatible = [], True
            for name, required in option.items():
                item = doc.resolve(schemes.get(name, {}))
                kind = item.get("type")
                if item.get("x-amazon-apigateway-authtype") == "awsSigv4":
                    compatible = False
                elif kind == "oauth2" and "clientCredentials" in item.get("flows", {}):
                    scopes.extend(required)
                elif kind == "http" and item.get("scheme", "").lower() == "bearer":
                    pass
                elif kind == "apiKey" and item.get("in") == "header" and item.get("name", "").lower() == "x-api-key":
                    pass
                elif kind == "apiKey" and item.get("in") == "header" and item.get("name", "").lower() == "authorization" and item.get("x-amazon-apigateway-authtype") == "cognito_user_pools":
                    scopes.extend(required)
                else:
                    compatible = False
            if compatible:
                scope_set.update(scopes)
                break
        else:
            raise InputError(f"{op['key']}: security has no M2M bearer + X-API-KEY compatible alternative. Basic, SigV4, cookies and user-login grants need a different scenario.")
    return sorted(scope_set)


def build_jmx(requests, enabled_keys, host="localhost", port=8080, protocol="http"):
    if not re.fullmatch(r"[A-Za-z0-9.:\[\]-]+", host) or not host.strip(".:-[]"):
        raise InputError("Invalid API host; do not include a scheme/path.")
    if not 1 <= port <= 65535 or protocol not in ("http", "https"):
        raise InputError("Invalid port/protocol.")
    root = ET.Element("jmeterTestPlan", version="1.2", properties="5.0", jmeter="5.6.3")
    top = ET.SubElement(root, "hashTree")
    plan, plan_tree = node(top, "TestPlan", "TestPlanGui", "Cognito M2M + OpenAPI smoke test")
    prop(plan, "TestPlan.comments", "One thread, one loop, one API operation enabled. OAuth is an additional request. Set COGNITO_TOKEN_URL, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET, COGNITO_SCOPE in the JMeter process environment.")
    prop(plan, "TestPlan.functional_mode", False, "boolProp")
    prop(plan, "TestPlan.serialize_threadgroups", True, "boolProp")
    variables = ET.SubElement(plan, "elementProp", name="TestPlan.user_defined_variables", elementType="Arguments",
                              guiclass="ArgumentsPanel", testclass="Arguments", testname="User Defined Variables", enabled="true")
    collection = ET.SubElement(variables, "collectionProp", name="Arguments.arguments")
    for key, value in (("host", host), ("port", port), ("protocol", protocol), ("api_key", "XXXXXXXXXXXX")):
        item = ET.SubElement(collection, "elementProp", name=key, elementType="Argument")
        prop(item, "Argument.name", key)
        prop(item, "Argument.value", "${__P(" + key + "," + str(value) + ")}")
        prop(item, "Argument.metadata", "=")
    group, group_tree = node(plan_tree, "ThreadGroup", "ThreadGroupGui", "Smoke: 1 thread x 1 iteration")
    prop(group, "ThreadGroup.on_sample_error", "stopthread")
    loop = ET.SubElement(group, "elementProp", name="ThreadGroup.main_controller", elementType="LoopController",
                         guiclass="LoopControlPanel", testclass="LoopController", testname="Loop Controller", enabled="true")
    prop(loop, "LoopController.continue_forever", False, "boolProp")
    prop(loop, "LoopController.loops", "${__P(loops,1)}")
    prop(group, "ThreadGroup.num_threads", "${__P(threads,1)}")
    prop(group, "ThreadGroup.ramp_time", "${__P(ramp_seconds,1)}")
    prop(group, "ThreadGroup.scheduler", False, "boolProp")
    prop(group, "ThreadGroup.same_user_on_next_iteration", True, "boolProp")
    defaults, _ = node(group_tree, "ConfigTestElement", "HttpDefaultsGui", "HTTP defaults")
    arguments(defaults, [])
    for key, value in (("domain", "${host}"), ("port", "${port}"), ("protocol", "${protocol}"),
                       ("contentEncoding", "UTF-8"), ("connect_timeout", "${__P(connect_timeout_ms,5000)}"),
                       ("response_timeout", "${__P(response_timeout_ms,30000)}"), ("implementation", "HttpClient4")):
        prop(defaults, "HTTPSampler." + key, value)
    for request in requests:
        _, branch = node(group_tree, "GenericController", "LogicControllerGui",
                         request["method"] + " " + request["key"], request["key"] in enabled_keys)
        script(branch, "prepare.groovy", "Prepare / check token (no result on success)")
        condition, condition_tree = node(branch, "IfController", "IfControllerPanel", "Acquire when token is absent or renewal time reached")
        prop(condition, "IfController.condition", "${token_needed}")
        prop(condition, "IfController.evaluateAll", False, "boolProp")
        prop(condition, "IfController.useExpression", True, "boolProp")
        sampler, token_tree = http(condition_tree, "AUTH Cognito /oauth2/token", "POST", "${token_path}")
        for key in ("host", "port", "protocol"):
            prop(sampler, "HTTPSampler." + ("domain" if key == "host" else key), "${token_" + key + "}")
        args = sampler.find("elementProp[@name='HTTPsampler.Arguments']/collectionProp")
        for key, value in (("grant_type", "client_credentials"), ("scope", "${cognito_scope}")):
            item = ET.SubElement(args, "elementProp", name=key, elementType="HTTPArgument")
            prop(item, "HTTPArgument.always_encode", True, "boolProp")
            prop(item, "Argument.name", key)
            prop(item, "Argument.value", value)
            prop(item, "Argument.metadata", "=")
            prop(item, "HTTPArgument.use_equals", True, "boolProp")
        headers(token_tree, {"Authorization": "${cognito_basic}", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        extractor, _ = node(token_tree, "JSONPostProcessor", "JSONPostProcessorGui", "JSON Extractor: token / lifetime / type")
        for name, value in (("referenceNames", "raw_access_token;raw_expires_in;raw_token_type"),
                            ("jsonPathExprs", "$.access_token;$.expires_in;$.token_type"),
                            ("match_numbers", "1;1;1"),
                            ("defaultValues", "__MISSING__;__MISSING__;__MISSING__")):
            prop(extractor, "JSONPostProcessor." + name, value)
        prop(extractor, "JSONPostProcessor.compute_concat", False, "boolProp")
        script(token_tree, "token_result.groovy", "Validate / cache / redact OAuth", "JSR223PostProcessor")
        sampler, api_tree = http(branch, "API " + request["method"] + " " + request["key"], request["method"], request["path"], request["body"])
        wire_headers = {key: value for key, value in request["headers"].items() if key.lower() not in ("authorization", "x-api-key")}
        wire_headers.update({"X-API-KEY": "${api_key}", "Authorization": "Bearer ${access_token}"})
        headers(api_tree, wire_headers)
        script(api_tree, "api_result.groovy", "Clear outgoing headers (no replay)", "JSR223PostProcessor")
        assertion, _ = node(api_tree, "ResponseAssertion", "AssertionGui", "Expected HTTP status from OpenAPI")
        strings = ET.SubElement(assertion, "collectionProp", name="Asserion.test_strings")
        prop(strings, "0", request["status_pattern"])
        prop(assertion, "Assertion.test_field", "Assertion.response_code")
        prop(assertion, "Assertion.assume_success", False, "boolProp")
        prop(assertion, "Assertion.test_type", 1, "intProp")
    writer, _ = node(plan_tree, "ResultCollector", "SimpleDataWriter", "JTL XML: timings/status - open in View Results Tree")
    prop(writer, "ResultCollector.error_logging", False, "boolProp")
    obj = ET.SubElement(writer, "objProp")
    ET.SubElement(obj, "name").text = "saveConfig"
    value = ET.SubElement(obj, "value", {"class": "SampleSaveConfiguration"})
    fields = {"time": True, "latency": True, "timestamp": True, "success": True, "label": True,
              "code": True, "message": True, "threadName": True, "dataType": True, "encoding": True,
              "assertions": True, "subresults": True, "responseData": False, "samplerData": False,
              "xml": True, "fieldNames": True, "responseHeaders": False, "requestHeaders": False,
              "responseDataOnError": False, "saveAssertionResultsFailureMessage": True,
              "assertionsResultsToSave": 0, "bytes": True, "sentBytes": True, "threadCounts": True,
              "idleTime": True, "connectTime": True}
    for key, setting in fields.items():
        ET.SubElement(value, key).text = str(setting).lower()
    prop(writer, "filename", "${__P(result_file,results.jtl)}")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).replace(b"\r", b"&#13;") + b"\n"
