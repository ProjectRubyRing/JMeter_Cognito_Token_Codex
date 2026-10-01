"""Loopback-only integration tests executed by the actual JMeter 5.6.3 engine."""
import argparse
import base64
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apikit.spec import Document, load_document
from apikit.jmx import make_requests
from apikit.plan import build_jmx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jmeter-home", required=True, type=Path)
    parser.add_argument("--only", help="Run one named case for diagnosis")
    args = parser.parse_args()
    work = ROOT / ".work" / ("integration-" + uuid.uuid4().hex[:10])
    work.mkdir(parents=True)
    state = {"case": "", "mode": "ok", "tokens": [], "calls": [], "ttl": 3600, "errors": []}
    client, secret = "integration-fake-client", "INTEGRATION_FAKE_SECRET_42"
    basic = "Basic " + base64.b64encode((client + ":" + secret).encode()).decode()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, obj):
            payload = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD" and status != 204:
                self.wfile.write(payload)

        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
            path = urlsplit(self.path).path
            with lock:
                if path == "/oauth2/token":
                    form = parse_qs(body)
                    if self.command != "POST" or self.headers.get("Authorization") != basic:
                        state["errors"].append("OAuth method/basic authentication mismatch")
                    if form.get("grant_type") != ["client_credentials"] or form.get("scope") != ["demo-api/read demo-api/write"]:
                        state["errors"].append("OAuth form encoding mismatch")
                    if self.headers.get("X-API-KEY"):
                        state["errors"].append("API key leaked to token endpoint")
                    token = "MOCK_ACCESS_" + str(len(state["tokens"]) + 1) + ".payload.signature"
                    state["tokens"].append(token)
                    mode = state["mode"]
                    if mode == "malformed_json":
                        payload = b'not-json'
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                        return
                    if mode in ("oauth400", "oauth429"):
                        self.reply(400 if mode == "oauth400" else 429, {"error": "invalid_client", "detail": secret})
                        return
                    result = {"access_token": token, "expires_in": state["ttl"], "token_type": "Bearer"}
                    if mode == "missing_token":
                        del result["access_token"]
                    if mode == "missing_expiry":
                        del result["expires_in"]
                    if mode == "wrong_type":
                        result["token_type"] = "notBearer"
                    if mode == "zero_expiry":
                        result["expires_in"] = 0
                    self.reply(200, result)
                    return
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer ") or auth[7:] not in state["tokens"]:
                    state["errors"].append("API missing an issued bearer token")
                if self.headers.get("X-API-KEY") != "XXXXXXXXXXXX":
                    state["errors"].append("API key missing or duplicated")
                state["calls"].append({"method": self.command, "path": self.path, "body": body, "token": auth[7:]})
                if state["mode"] in ("api401", "api403"):
                    self.reply(int(state["mode"][3:]), {"error": "denied"})
                else:
                    self.reply(201 if self.command == "POST" and path == "/items" else 204 if self.command == "DELETE" else 200, {"ok": True})

        do_GET = handle_request
        do_POST = handle_request
        do_DELETE = handle_request
        do_HEAD = handle_request
        do_PUT = handle_request
        do_PATCH = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    doc = Document(load_document(ROOT / "examples/openapi.sample.yaml"))
    requests, _ = make_requests(doc, doc.operations(), seed=42)
    reports = []
    # name, mode, loops, threads, think_ms, ttl, token_count, api_count, exit, all_ops, extra_properties
    cases = [
        ("default_one", "ok", 1, 1, 0, 3600, 1, 1, 0, False, ""),
        ("powershell_options", "ok", 2, 2, 0, 3600, 2, 4, 0, False, ""),
        ("reuse_three", "ok", 3, 1, 0, 3600, 1, 3, 0, False, ""),
        ("renew_before_expiry", "ok", 3, 1, 3100, 4, 3, 3, 0, False, "token_jitter_fraction=0\n"),
        ("one_token_per_thread", "ok", 2, 3, 0, 3600, 3, 6, 0, False, ""),
        ("all_five", "ok", 1, 1, 0, 3600, 1, 5, 0, True, ""),
        ("explicit_each_request", "ok", 3, 1, 0, 3600, 3, 3, 0, False, "token_each_request=true\n"),
        ("oauth400", "oauth400", 3, 1, 0, 3600, 1, 0, 1, False, ""),
        ("oauth429_no_retry", "oauth429", 3, 1, 0, 3600, 1, 0, 1, False, ""),
        ("malformed_json", "malformed_json", 3, 1, 0, 3600, 1, 0, 1, False, ""),
        ("missing_token", "missing_token", 3, 1, 0, 3600, 1, 0, 1, False, ""),
        ("missing_expiry", "missing_expiry", 3, 1, 0, 3600, 1, 0, 1, False, ""),
        ("wrong_type", "wrong_type", 3, 1, 0, 3600, 1, 0, 1, False, ""),
        ("zero_expiry", "zero_expiry", 3, 1, 0, 3600, 1, 0, 1, False, ""),
        ("api401_no_replay", "api401", 3, 1, 0, 3600, 1, 1, 1, False, ""),
        ("api403_no_replay", "api403", 3, 1, 0, 3600, 1, 1, 1, False, ""),
        ("missing_secret", "missing_secret", 3, 1, 0, 3600, 0, 0, 1, False, ""),
    ]
    try:
        for name, mode, loops, threads, think, ttl, token_count, api_count, exit_code, all_ops, extra in cases:
            if args.only and args.only != name:
                continue
            state.update(case=name, mode=mode, ttl=ttl, tokens=[], calls=[], errors=[])
            directory = work / name
            directory.mkdir()
            selected = {r["key"] for r in requests} if all_ops else {"listItems"}
            scenario = directory / "scenario.jmx"
            scenario.write_bytes(build_jmx(requests, selected, "127.0.0.1", port))
            (directory / "jmeter.properties").write_text("allow_http_token_loopback=true\n" + extra, encoding="ascii")
            env = os.environ.copy()
            env.update(COGNITO_TOKEN_URL=f"http://127.0.0.1:{port}/oauth2/token", COGNITO_CLIENT_ID=client,
                       COGNITO_CLIENT_SECRET=secret, COGNITO_SCOPE="demo-api/read demo-api/write", API_KEY="XXXXXXXXXXXX")
            if mode == "missing_secret":
                env.pop("COGNITO_CLIENT_SECRET")
            command = [sys.executable, str(ROOT / "cognito_jmeter.py"), "run", "--scenario", str(scenario),
                       "--jmeter-home", str(args.jmeter_home.resolve()), "--results", str(directory),
                       "--loops", str(loops), "--threads", str(threads), "--think-time-ms", str(think)]
            if name in ("default_one", "powershell_options") and os.name == "nt":
                command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File",
                           str(ROOT / "scripts/Run-JMeter.ps1"), "-Scenario", str(scenario),
                           "-JMeterHome", str(args.jmeter_home.resolve()), "-ResultsDirectory", str(directory),
                           "-PythonExecutable", sys.executable, "-Threads", str(threads), "-Loops", str(loops),
                           "-RampSeconds", "1", "-ThinkTimeMs", str(think)]
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, errors="replace", timeout=120)
            assert result.returncode == exit_code, f"{name}: exit {result.returncode}, expected {exit_code}\n{result.stdout}\n{result.stderr}"
            assert len(state["tokens"]) == token_count, (name, "tokens", len(state["tokens"]), token_count)
            assert len(state["calls"]) == api_count, (name, "API calls", len(state["calls"]), api_count)
            assert not state["errors"], (name, state["errors"])
            if all_ops:
                for observed, expected in zip(state["calls"], requests):
                    assert observed["method"] == expected["method"], (name, "method")
                    assert observed["path"] == expected["path"], (name, "path")
                    assert observed["body"] == (expected["body"] or ""), (name, "body")
            if name == "one_token_per_thread":
                assert sorted(Counter(c["token"] for c in state["calls"]).values()) == [2, 2, 2]
            jtl = next(directory.glob("*.jtl"))
            samples = list(ET.parse(jtl).getroot())
            assert len(samples) == token_count + api_count + (1 if mode == "missing_secret" else 0), (name, "result count")
            for artifact in list(directory.glob("*.jtl")) + list(directory.glob("*.log")) + [scenario]:
                text = artifact.read_text(encoding="utf-8", errors="replace")
                for sensitive in [secret, basic] + state["tokens"]:
                    assert sensitive not in text, (name, "sensitive material in", artifact.name)
            reports.append({"case": name, "passed": True, "oauth_requests": token_count, "api_requests": api_count,
                            "runner_exit": result.returncode, "jtl": str(jtl.relative_to(ROOT))})
            print(f"PASS {name}: auth={token_count}, API={api_count}, exit={exit_code}", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (work / "summary.json").write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence: {work}")


if __name__ == "__main__":
    main()
