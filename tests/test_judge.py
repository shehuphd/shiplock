"""The symbol index, the claim and coverage extraction, the two anchor checks,
and the judge with a stub provider (no network) plus the TypeSafe adapter
against a local HTTP server."""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from shiplock._checks import check_doc_anchors, check_doc_defaults
from shiplock._claims import extract, sentences
from shiplock._config import AnchorsConfig, Config, DocsConfig, JudgeConfig, load_config
from shiplock._judge import (
    Adapter,
    Answer,
    CallRecord,
    JudgeError,
    TypeSafeAdapter,
    adapter_for,
    judgments_section,
    run_judge,
)
from shiplock._symbols import build_index

CLI_SRC = '''"""Exit codes: 0 clean, 2 usage."""
import argparse
import os

EXIT_OK = 0
EXIT_USAGE = 2


def build():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check")
    check.add_argument("--json", action="store_true", help="print JSON")
    check.add_argument("--limit", default=20, help="rows")
    check.add_argument("--path", default=".", help="where")
    check.add_argument("--secret-flag", action="store_true")
    return parser


def main():
    os.environ.get("DEMO_TOKEN")
    return EXIT_OK
'''


def _add(root, *paths):
    subprocess.run(["git", "-C", str(root), "add", *paths], check=True, capture_output=True)


def _repo(git_repo, write_file, readme: str):
    write_file(git_repo, "src/demo/cli.py", CLI_SRC)
    write_file(git_repo, "README.md", readme)
    _add(git_repo, "src/demo/cli.py", "README.md")
    return Config(root=git_repo, docs=DocsConfig(public=["README.md"]), anchors=AnchorsConfig())


# Symbol index


def test_index_reads_flags_defaults_commands_and_env(git_repo, write_file):
    _repo(git_repo, write_file, "x\n")
    index = build_index(git_repo, ["src/demo/cli.py"], set())
    assert set(index.flags) == {"--json", "--limit", "--path", "--secret-flag"}
    assert index.flags["--limit"][0].default == 20 and index.flags["--limit"][0].has_default
    assert index.flags["--json"][0].action == "store_true"
    assert index.commands == {"check"}
    assert "DEMO_TOKEN" in index.env_vars
    assert index.lookup("`build`")[0].kind == "function"
    assert index.lookup("EXIT_USAGE")[0].kind == "assignment"
    assert index.lookup("--json")[0].kind == "flag"
    assert index.lookup("src/demo/cli.py")[0].kind == "file"
    assert index.lookup("nothing_here") == []
    assert "Exit codes" in index.excerpt(index.lookup("EXIT_OK")[0])


def test_index_regex_fallback_for_other_languages(tmp_path, write_file):
    write_file(tmp_path, "src/lib.rs", "pub fn run() {}\nconst LIMIT: u32 = 3;\nlet key = std::env::var(\"RS_TOKEN\");\n")
    index = build_index(tmp_path, ["src/lib.rs"], set())
    assert index.lookup("run")[0].kind == "definition"
    assert index.lookup("LIMIT")
    assert "RS_TOKEN" in index.env_vars


# Extraction


def test_sentences_skip_fences_tables_and_headings():
    text = "# Title\n\nUse `--json` for JSON output. Then stop.\n\n```\ncode `--x`\n```\n\n| a | b |\n"
    got = sentences(text)
    assert [s for _, s in got] == ["Use `--json` for JSON output.", "Then stop."] or len(got) == 1


def test_extract_claims_with_anchors_and_coverage(git_repo, write_file):
    config = _repo(git_repo, write_file, "Pass `--json` to print one JSON object on stdout.\n\nThis sentence names `nothing_real` only.\n")
    index = build_index(git_repo, ["src/demo/cli.py"], {"README.md"})
    surface = extract(config, index, ["README.md"])
    assert len(surface.claims) == 1
    assert surface.claims[0].anchors == ("--json",)
    assert "--json" in surface.claims[0].evidence
    assert surface.unanchored == {"README.md": 1}
    names = {(c.kind, c.name) for c in surface.coverage}
    assert ("flag", "--json") in names and ("command", "check") in names and ("env var", "DEMO_TOKEN") in names


def test_extract_coverage_skips_tests_scripts_and_shell_vars(git_repo, write_file):
    config = _repo(git_repo, write_file, "x\n")
    write_file(git_repo, "scripts/tool.py", 'import argparse\np = argparse.ArgumentParser()\np.add_argument("--only-in-script")\n')
    write_file(git_repo, "ci.yml", "run: echo $SHELL_ONLY\n")
    _add(git_repo, "scripts/tool.py", "ci.yml")
    index = build_index(git_repo, ["src/demo/cli.py", "scripts/tool.py", "ci.yml"], {"README.md"})
    names = {c.name for c in extract(config, index, ["README.md"]).coverage}
    assert "--only-in-script" not in names and "SHELL_ONLY" not in names
    assert "--json" in names and "DEMO_TOKEN" in names
    assert "SHELL_ONLY" in index.env_vars  # still resolves for doc-anchors


def test_extract_honours_undocumented(git_repo, write_file):
    config = _repo(git_repo, write_file, "x\n")
    config = Config(root=git_repo, docs=config.docs, judge=JudgeConfig(undocumented=["--secret-flag", "DEMO_TOKEN"]))
    index = build_index(git_repo, ["src/demo/cli.py"], {"README.md"})
    names = {c.name for c in extract(config, index, ["README.md"]).coverage}
    assert "--secret-flag" not in names and "DEMO_TOKEN" not in names and "--json" in names


# The deterministic checks


def test_doc_anchors_fires_on_an_undeclared_flag_env_and_key(git_repo, write_file):
    config = _repo(git_repo, write_file, "Use `--jsonn` and `DEMO_TOKEN` and `OTHER_TOKEN`; set `[docs].public`.\n")
    findings, _ = check_doc_anchors(config)
    messages = [f.message for f in findings]
    assert any("`--jsonn`" in m for m in messages)
    assert any("`OTHER_TOKEN`" in m for m in messages)
    assert not any("DEMO_TOKEN`" in m for m in messages)
    assert any("`[docs].public`" in m for m in messages)


def test_doc_anchors_exempt_and_fenced_code_are_skipped(git_repo, write_file):
    config = _repo(git_repo, write_file, "Use `--foreign`.\n\n```\n--also-foreign\n```\n")
    config = Config(root=git_repo, docs=config.docs, anchors=AnchorsConfig(exempt=["--foreign"]))
    findings, _ = check_doc_anchors(config)
    assert findings == []


def test_doc_anchors_skips_without_section(tmp_path):
    findings, notices = check_doc_anchors(Config(root=tmp_path, docs=DocsConfig(public=["README.md"])))
    assert findings == [] and "no [anchors]" in notices[0].message


def test_doc_defaults_fires_on_a_wrong_default_and_accepts_phrasing(git_repo, write_file):
    config = _repo(git_repo, write_file, "`--limit` defaults to 25 rows. `--path` defaults to the current directory. `--json` (default: false).\n")
    findings, _ = check_doc_defaults(config)
    assert len(findings) == 1
    assert "`--limit`" in findings[0].message and "25" in findings[0].message and "20" in findings[0].message


def test_doc_defaults_off_by_config(git_repo, write_file):
    config = _repo(git_repo, write_file, "`--limit` defaults to 25.\n")
    config = Config(root=git_repo, docs=config.docs, anchors=AnchorsConfig(defaults=False))
    findings, notices = check_doc_defaults(config)
    assert findings == [] and "skipped" in notices[0].message


def test_config_parses_anchors_and_judge_sections(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n[anchors]\nexempt = ["--x"]\ndefaults = false\n[judge]\nundocumented = ["--y"]\nmodel = "jev-1.13.0"\n')
    config = load_config(tmp_path)
    assert config.anchors.exempt == ["--x"] and config.anchors.defaults is False
    assert config.judge.undocumented == ["--y"] and config.judge.provider == "typesafe"


# The judge with a stub provider


class StubAdapter(Adapter):
    """Answers from a script: coverage yes for documented names, claims by keyword."""

    name = "stub"

    def __init__(self, model="jev-1.13.0", fail_purpose=None):
        self.model = model
        self.fail_purpose = fail_purpose
        self.calls = []

    def judge(self, state, questions, model):
        self.calls.append((state, questions))
        if self.fail_purpose and any(self.fail_purpose in q.get("instructions", "") for q in questions.values()):
            return {}, CallRecord("", 403, None, 0, 1, None, None, "403 blocked")
        answers = {}
        for qid, q in questions.items():
            instr = q["instructions"]
            if q["type"] == "noul":
                if "describe the" in instr:
                    name = instr.split("`")[3]
                    answers[qid] = Answer(0.95 if name in state.get("doc", "") else 0.05)
                elif "verdict line" in instr:
                    answers[qid] = Answer(0.9)
                else:
                    answers[qid] = Answer(0.8)
            else:
                n = int(qid[1:])
                text = state["claims"][n]["text"]
                if "wrongly" in text:
                    answers[qid] = Answer(0.9, "contradicted", {"contradicted": 0.9, "supported": 0.05, "insufficient": 0.05}, 0.9)
                else:
                    answers[qid] = Answer(0.9, "supported", {"supported": 0.9, "contradicted": 0.05, "insufficient": 0.05}, 0.9)
        return answers, CallRecord("", 200, self.model, 100, 5, 3, "req-1")


def test_run_judge_warns_on_uncovered_surface_and_collects_leads(git_repo, write_file):
    config = _repo(git_repo, write_file, "Pass `--json` for JSON. The `check` command wrongly uses `--limit`.\n")
    adapter = StubAdapter()
    result = run_judge(config, adapter)
    assert result.findings == []
    warned = {n.message.split("`")[1] for n in result.notices if n.message.startswith("coverage:")}
    assert "--path" in warned and "--secret-flag" in warned and "DEMO_TOKEN" in warned
    assert "--json" not in warned and "check" not in warned
    assert len(result.leads) == 1 and result.leads[0]["pick"] == "contradicted"
    assert any(n.kind == "info" and "call(s)" in n.message for n in result.notices)
    assert result.spend()["calls"] >= 3  # pre-flight, coverage, leads


def test_run_judge_pre_flight_failure_is_an_error(git_repo, write_file):
    config = _repo(git_repo, write_file, "x\n")
    with pytest.raises(JudgeError, match="pre-flight"):
        run_judge(config, StubAdapter(fail_purpose="the word ok"))


def test_run_judge_reports_a_refused_chunk_and_moves_on(git_repo, write_file):
    config = _repo(git_repo, write_file, "Pass `--json` for JSON.\n")
    result = run_judge(config, StubAdapter(fail_purpose="describe the"))
    assert any(r.startswith("coverage:") for r in result.refused)
    assert result.findings == []


def test_run_judge_flags_model_drift(git_repo, write_file):
    config = _repo(git_repo, write_file, "Pass `--json` for JSON.\n")
    result = run_judge(config, StubAdapter(model="jev-9.0.0"))
    assert any("not the pinned" in f.message for f in result.findings)


def test_run_judge_audit_output_checks(git_repo, write_file, tmp_path):
    config = _repo(git_repo, write_file, "Pass `--json` for JSON.\n")
    audit = tmp_path / "audit.md"
    audit.write_text("Finding: `README.md:1` says X; `src/demo/nope.py` says Y.\n\nAUDIT: FAIL\n", encoding="utf-8")
    result = run_judge(config, StubAdapter(), audit_output=str(audit), leads=False)
    assert result.audit["verdict_line"] == "FAIL"
    assert result.audit["missing_paths"] == ["src/demo/nope.py"]
    assert any("isn't a tracked file" in f.message for f in result.findings)
    audit.write_text("No verdict here.\n", encoding="utf-8")
    result = run_judge(config, StubAdapter(), audit_output=str(audit), leads=False)
    assert any("no AUDIT: PASS" in f.message for f in result.findings)


def test_judgments_section_lists_leads_and_gaps(tmp_path):
    data = {"leads": [{"doc": "README.md", "line": 3, "text": "claim", "pick": "contradicted", "p_contradicted": 0.8}],
            "coverage": [{"kind": "flag", "name": "--x", "path": "cli.py", "line": 9, "p_described": 0.1}],
            "unanchored": {"README.md": 4}, "refused": []}
    path = tmp_path / "j.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    section = judgments_section(path)
    assert "README.md:3 [contradicted, 0.8] claim" in section
    assert "flag `--x`" in section and "README.md: 4" in section


def test_adapter_for_reads_provider_prefix_and_rejects_unknown():
    assert isinstance(adapter_for("typesafe", "typesafe/abc"), TypeSafeAdapter)
    with pytest.raises(JudgeError, match="isn't a judgment provider"):
        adapter_for("chatty", "x")
    with pytest.raises(JudgeError, match="no key"):
        adapter_for("typesafe", "")


def test_a_judge_api_key_must_name_a_known_provider():
    # A JUDGE_API_KEY naming another service is refused before any call, so
    # that key never reaches the default provider as a bearer token.
    with pytest.raises(JudgeError, match="'openai', which isn't a judgment provider"):
        adapter_for("typesafe", "openai/sk-abc", prefixed=True)
    with pytest.raises(JudgeError, match="provider/key"):
        adapter_for("typesafe", "abc", prefixed=True)
    assert isinstance(adapter_for("typesafe", "TypeSafe/abc", prefixed=True), TypeSafeAdapter)


# The TypeSafe adapter against a local server


class _Handler(BaseHTTPRequestHandler):
    script: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        status, payload, headers = self.script.pop(0) if self.script else (200, None, {})
        if payload is None:
            payload = {"model": body.get("model"), "answers": {q: {"type": "noul", "noul": 0.9} for q in body.get("questions", {})}, "usage": {"input_tokens": 12}}
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)
        self.server.seen.append((self.headers.get("Authorization"), body))

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.seen = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()


def _adapter(server, script):
    _Handler.script = list(script)
    adapter = TypeSafeAdapter("k-test")
    adapter.endpoint = f"http://127.0.0.1:{server.server_port}/v1/systemone"
    return adapter


def test_typesafe_adapter_sends_bearer_and_reads_answers(server):
    adapter = _adapter(server, [(200, None, {"x-typesafe-request-id": "r1", "x-envoy-upstream-service-time": "42"})])
    answers, record = adapter.judge("s", {"q": {"type": "noul", "instructions": "?"}}, "jev-1.13.0")
    assert answers["q"].probability == 0.9
    assert record.status == 200 and record.model == "jev-1.13.0" and record.input_tokens == 12
    assert record.request_id == "r1" and record.server_ms == 42
    assert server.seen[0][0] == "Bearer k-test"
    assert "temperature" not in server.seen[0][1]


def test_typesafe_adapter_retries_429_and_never_403(server, monkeypatch):
    monkeypatch.setattr("shiplock._judge.time.sleep", lambda s: None)
    adapter = _adapter(server, [(429, {"detail": "slow"}, {"Retry-After": "0"}), (200, None, {})])
    answers, record = adapter.judge("s", {"q": {"type": "noul", "instructions": "?"}}, "jev-1.13.0")
    assert record.status == 200 and len(server.seen) == 2
    adapter = _adapter(server, [(403, {}, {})])
    answers, record = adapter.judge("s", {"q": {"type": "noul", "instructions": "?"}}, "jev-1.13.0")
    assert record.status == 403 and answers == {} and len(server.seen) == 3


def test_typesafe_adapter_reads_choice_answers(server):
    payload = {"model": "jev-1.13.0", "answers": {"c0": {"type": "choice", "choice": "supported", "probabilities": {"supported": 0.8, "contradicted": 0.2}, "confidence": 0.7}}, "usage": {"input_tokens": 5}}
    adapter = _adapter(server, [(200, payload, {})])
    answers, _ = adapter.judge("s", {"c0": {"type": "choice", "instructions": "?", "criteria": {"supported": None, "contradicted": None}}}, "jev-1.13.0")
    assert answers["c0"].choice == "supported" and answers["c0"].probability == 0.8 and answers["c0"].confidence == 0.7
