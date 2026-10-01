#!/usr/bin/env python3
"""CLI for both platform wrappers. Generation never invokes an API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import uuid
import xml.etree.ElementTree as ET


def parser():
    root = argparse.ArgumentParser(description="OpenAPI JSON/YAML -> Cognito M2M JMeter 5.6.3")
    commands = root.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Generate files without sending requests")
    generate.add_argument("--input", required=True, type=Path)
    generate.add_argument("--out", required=True, type=Path, help="New or empty output directory")
    generate.add_argument("--seed", type=int, help="Omit for fresh random initial data; set for reproduction")
    generate.add_argument("--data-file", type=Path, help="Operation-keyed input overrides; see generated test-data.json")
    generate.add_argument("--host", default="localhost")
    generate.add_argument("--port", type=int, default=8080)
    generate.add_argument("--protocol", choices=("http", "https"), default="http")
    generate.add_argument("--base-path", default="", help="Explicit stage/base-path prefix; servers are not used")
    selection = generate.add_mutually_exclusive_group()
    selection.add_argument("--enable-all", action="store_true", help="Enable one request per operation")
    selection.add_argument("--operation", help="Enable only this operationId (fallback: METHOD /path)")
    generate.add_argument("--sample", action="store_true", help="Label reports as demonstration data, not a supplied real definition")
    run = commands.add_parser("run", help="Explicitly execute an existing JMX with JMeter 5.6.3")
    run.add_argument("--scenario", required=True, type=Path)
    run.add_argument("--jmeter-home", type=Path, default=Path(os.environ["JMETER_HOME"]) if os.environ.get("JMETER_HOME") else None)
    run.add_argument("--results", type=Path, default=Path("results"))
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    run.add_argument("--protocol", choices=("http", "https"))
    run.add_argument("--properties", type=Path, help="Optional private JMeter properties, e.g. api_key")
    run.add_argument("--timeout", type=int, default=180, help="Process deadline in seconds")
    run.add_argument("--threads", type=int)
    run.add_argument("--loops", type=int)
    run.add_argument("--ramp-seconds", type=int)
    run.add_argument("--think-time-ms", type=int)
    return root



def generate(args):
    from apikit.spec import Document, InputError, load_document, load_json
    from apikit.jmx import make_requests
    from apikit.plan import build_jmx, authorization_scopes

    doc = Document(load_document(args.input))
    operations = doc.operations()
    keys = [op["key"] for op in operations]
    if args.operation and args.operation not in keys:
        raise InputError("--operation does not match an operationId or METHOD /path.")
    first_read = next((op["key"] for op in operations if op["method"] in ("GET", "HEAD")), keys[0])
    enabled = set(keys if args.enable_all else [args.operation or first_read])
    scopes = authorization_scopes(doc, operations)
    selected_scopes = authorization_scopes(doc, [op for op in operations if op["key"] in enabled])
    requests, data = make_requests(doc, operations, args.seed, load_json(args.data_file) if args.data_file else None, args.base_path)
    jmx = build_jmx(requests, enabled, args.host, args.port, args.protocol)
    out = args.out.resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise InputError("Output must be a new or empty directory; existing files are not overwritten.")
    out.mkdir(parents=True, exist_ok=True)
    (out / "scenario.jmx").write_bytes(jmx)
    (out / "test-data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = """# Non-secret runtime properties; credentials are environment-only.
httpclient4.retrycount=0
httpclient4.request_sent_retry_enabled=false
token_reuse_ratio=0.75
token_jitter_fraction=0.05
token_safety_seconds=30
token_each_request=false
think_time_ms=0
connect_timeout_ms=5000
response_timeout_ms=30000
"""
    (out / "jmeter.properties").write_text(config, encoding="ascii")
    manifest = {
        "format": 1, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": args.input.name, "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "openapi": doc.data["openapi"], "jmeter": "5.6.3", "seed": args.seed,
        "sample": args.sample,
        "randomization": "generation-time; fixed for each run of this JMX",
        "target": {"protocol": args.protocol, "host": args.host, "port": args.port, "base_path": args.base_path},
        "enabled_operations": [key for key in keys if key in enabled],
        "required_scopes_selected": selected_scopes, "required_scopes_all": scopes,
        "operations": [{"key": op["key"], "method": op["method"], "path": op["path"],
                        "enabled": op["key"] in enabled} for op in operations],
        "warnings": doc.warnings,
        "notes": ["All operations are included; default enables one GET/HEAD, or first operation if absent.",
                  "Token issuance is an extra HTTP request.",
                  "Business IDs, dependencies and response semantics need real test data."]
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated: {out}\nOperations: {len(keys)}; enabled: {len(enabled)}")
    print("Required scopes: " + (" ".join(selected_scopes) or "(set actual custom scopes)"))
    return 0


def run(args):
    if args.jmeter_home is None:
        raise ValueError("Set --jmeter-home or JMETER_HOME to the JMeter 5.6.3 installation.")
    jar = args.jmeter_home.resolve() / "bin" / "ApacheJMeter.jar"
    if not jar.is_file() or not args.scenario.is_file():
        raise ValueError("JMeter jar or scenario file does not exist.")
    java_home = os.environ.get("JAVA_HOME")
    java = str(Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")) if java_home else shutil.which("java")
    if not java:
        raise ValueError("Java is not on PATH. Install a supported JDK and set PATH.")
    for key in ("threads", "loops"):
        value = getattr(args, key)
        if value is not None and value < 1:
            raise ValueError(f"--{key} must be positive.")
    for key in ("ramp_seconds", "think_time_ms"):
        value = getattr(args, key)
        if value is not None and value < 0:
            raise ValueError(f"--{key} must not be negative.")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive.")
    if args.port is not None and not 1 <= args.port <= 65535:
        raise ValueError("--port must be 1..65535.")
    if args.host and not re.fullmatch(r"[A-Za-z0-9.:-]+", args.host):
        raise ValueError("--host must contain only a hostname/IP.")
    results = args.results.resolve()
    results.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:12]
    jtl = results / (prefix + ".jtl")
    log = results / (prefix + ".log")
    base = [java, "-Djava.awt.headless=true", "-Xms128m", "-Xmx512m", "-jar", str(jar)]
    version = subprocess.run(base + ["-v", "-j", str(log)], capture_output=True, text=True, errors="replace", timeout=30, cwd=results, check=False)
    if version.returncode or not re.search(r"\b5\.6\.3\b", version.stdout):
        raise ValueError("The runner requires exactly Apache JMeter 5.6.3; version check failed.")
    command = base + ["-n", "-t", str(args.scenario.resolve()), "-j", str(log)]
    config = args.scenario.resolve().with_name("jmeter.properties")
    if config.is_file():
        command += ["-q", str(config)]
    if args.properties:
        if not args.properties.is_file():
            raise ValueError("--properties file does not exist.")
        command += ["-q", str(args.properties.resolve())]
    # The embedded XML writer owns JTL output. Adding -l would register a second
    # writer, so only result_file is supplied here.
    command += ["-Jresult_file=" + str(jtl), "-Jhttpclient4.retrycount=0", "-Jhttpclient4.request_sent_retry_enabled=false"]
    for name in ("host", "port", "protocol", "threads", "loops", "ramp_seconds", "think_time_ms"):
        value = getattr(args, name)
        if value is not None:
            command += ["-J" + name + "=" + str(value)]
    print(f"JTL: {jtl}\nLog: {log}", flush=True)
    completed = subprocess.run(command, cwd=results, timeout=args.timeout, check=False)
    if completed.returncode:
        print(f"JMeter process failed with code {completed.returncode}.", file=sys.stderr)
        return 1
    if not jtl.is_file():
        print("JMeter produced no JTL. Use a generated scenario with its XML writer.", file=sys.stderr)
        return 1
    try:
        count = failures = api_count = 0
        for _, element in ET.iterparse(jtl, events=("end",)):
            if element.tag in ("httpSample", "sample"):
                count += 1
                api_count += element.get("lb", "").startswith("API ")
                failures += element.get("s") != "true"
                element.clear()
    except ET.ParseError:
        print("JTL is incomplete or invalid XML; execution is not successful.", file=sys.stderr)
        return 1
    print(f"Result samples: {count}; API samples: {api_count}; failures: {failures}")
    return 0 if api_count > 0 and failures == 0 else 1


def main(argv=None):
    if sys.version_info < (3, 11):
        print("Python 3.11 or later is required.", file=sys.stderr)
        return 2
    args = parser().parse_args(argv)
    try:
        return generate(args) if args.command == "generate" else run(args)
    except ImportError as exc:
        print(f"Missing dependency: {exc.name}. Install requirements.lock.txt in your virtual environment.", file=sys.stderr)
        return 2
    except (ValueError, OSError, RecursionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("ERROR: JMeter process exceeded its deadline and was stopped. Check partial JTL/log files.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
