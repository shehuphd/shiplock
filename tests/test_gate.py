"""The gate workflow's shell orchestration, run against stub agent CLIs.

The audit job's step scripts are extracted from gate.yml itself (so these
tests exercise the committed scripts, not a copy), the GitHub expression
placeholders are substituted with test values, and the scripts run under bash
with stub `claude`/`codex`/`npm` binaries on PATH. Failing cases first.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / ".github" / "workflows" / "gate.yml"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="the gate's scripts need bash and jq",
)

PRIMARY_BARE_KEY = "sk-Ant-MiXeD123"
FALLBACK_BARE_KEY = "sk-OpenAI-CaSe456"

STEP_RUNNERS = "Read the key providers and install their runners"
STEP_AUDIT = "Run the semantic audit"
STEP_VERDICT = "Act on the verdict"


def _step_scripts() -> dict[str, str]:
    doc = yaml.safe_load(GATE.read_text())
    return {
        step["name"]: step["run"]
        for step in doc["jobs"]["audit"]["steps"]
        if step.get("run")
    }


def _substitute(script: str, *, model: str, fb_model: str, outputs: dict[str, str]) -> str:
    """Replace ${{ ... }} expressions the way the workflow runtime would."""

    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        inputs = {
            "inputs.audit-model": model,
            "inputs.audit-fallback-model": fb_model,
            "inputs.audit-permission-mode": "dontAsk",
        }
        if expr in inputs:
            return inputs[expr]
        step_output = re.fullmatch(r"steps\.runners\.outputs\.([\w-]+)", expr)
        if step_output:
            return outputs.get(step_output.group(1), "")
        return ""

    return re.sub(r"\$\{\{(.*?)\}\}", repl, script)


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text("#!/bin/bash\n" + body)
    stub.chmod(0o755)


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    """A work dir with stub binaries and a step runner bound to it."""
    bin_dir = tmp_path / "bin"
    work = tmp_path / "work"
    bin_dir.mkdir()
    work.mkdir()

    _write_stub(bin_dir, "npm", 'echo "stub npm $*" >&2\n')
    _write_stub(bin_dir, "gh", 'echo "stub gh $*" >&2\n')
    _write_stub(bin_dir, "shiplock", 'echo "checklist ending in a verdict line"\n')
    # The claude stub asserts the bare key arrived case-preserved, settles two
    # questions into the progress log, then dies the way an API failure does.
    _write_stub(
        bin_dir,
        "claude",
        f'[ "$ANTHROPIC_API_KEY" = "{PRIMARY_BARE_KEY}" ] || {{ echo "wrong key" >&2; exit 99; }}\n'
        'printf "Q1 settled\\nQ2 settled\\n" > audit-progress.md\n'
        'echo "stub claude: dying mid-run" >&2\n'
        "exit 1\n",
    )
    # The codex stub asserts the resume preamble and the progress log reached
    # it, then finishes the audit with a PASS verdict and a usage event.
    _write_stub(
        bin_dir,
        "codex",
        f'[ "$CODEX_API_KEY" = "{FALLBACK_BARE_KEY}" ] || {{ echo "wrong key" >&2; exit 99; }}\n'
        'last=""\n'
        'args=("$@")\n'
        'for ((i = 0; i < ${#args[@]}; i++)); do\n'
        '  [ "${args[$i]}" = "--output-last-message" ] && last="${args[$((i + 1))]}"\n'
        "done\n"
        "prompt=$(cat)\n"
        'grep -q "interrupted mid-way" <<<"$prompt" || { echo "no resume preamble" >&2; exit 98; }\n'
        '[ -f audit-progress.md ] || { echo "no progress log" >&2; exit 97; }\n'
        'printf "Continued from the log.\\nAUDIT: PASS\\n" > "$last"\n'
        "echo '{\"type\":\"turn.completed\",\"usage\":"
        "{\"input_tokens\":50,\"output_tokens\":900,\"cached_input_tokens\":40000}}'\n",
    )

    scripts = _step_scripts()
    gh_output = work / "gh_output"
    gh_summary = work / "gh_summary"
    gh_output.touch()
    gh_summary.touch()

    def run(step: str, *, primary="", fallback="", model="sonnet", fb_model=""):
        outputs = {}
        for line in gh_output.read_text().splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
        script = _substitute(scripts[step], model=model, fb_model=fb_model, outputs=outputs)
        return subprocess.run(
            ["bash", "-c", script],
            cwd=work,
            capture_output=True,
            text=True,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(tmp_path),
                "PRIMARY_KEY": primary,
                "FALLBACK_KEY": fallback,
                "GITHUB_OUTPUT": str(gh_output),
                "GITHUB_STEP_SUMMARY": str(gh_summary),
                "GITHUB_SHA": "abc1234def5678",
                "GH_TOKEN": "stub-token",
            },
        )

    run.output_file = gh_output
    run.summary_file = gh_summary
    return run


def test_bare_key_without_provider_prefix_is_rejected(gate):
    result = gate(STEP_RUNNERS, primary=PRIMARY_BARE_KEY)
    assert result.returncode != 0
    assert "must be 'provider/key'" in result.stderr


def test_unknown_provider_is_rejected_naming_the_known_ones(gate):
    result = gate(STEP_RUNNERS, primary="deepseek/aX-123ab")
    assert result.returncode != 0
    assert "provider 'deepseek'" in result.stderr
    assert "anthropic and openai" in result.stderr


def test_missing_model_is_rejected(gate):
    result = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}", model="")
    assert result.returncode != 0
    assert "audit-model" in result.stderr


def test_cross_provider_fallback_requires_its_own_model(gate):
    result = gate(
        STEP_RUNNERS,
        primary=f"anthropic/{PRIMARY_BARE_KEY}",
        fallback=f"openai/{FALLBACK_BARE_KEY}",
        fb_model="",
    )
    assert result.returncode != 0
    assert "audit-fallback-model" in result.stderr


def test_malformed_fallback_key_is_rejected_up_front(gate):
    result = gate(
        STEP_RUNNERS,
        primary=f"anthropic/{PRIMARY_BARE_KEY}",
        fallback=FALLBACK_BARE_KEY,
    )
    assert result.returncode != 0
    assert "AUDIT_FALLBACK_API_KEY" in result.stderr


def test_missing_key_skips_with_a_warning_instead_of_failing(gate):
    result = gate(STEP_RUNNERS, primary="")
    assert result.returncode == 0
    assert "::warning::" in result.stderr
    assert "skipped" in result.stderr
    assert "skip=true" in gate.output_file.read_text()


def test_provider_prefix_is_case_insensitive_and_key_case_is_preserved(gate):
    result = gate(
        STEP_RUNNERS,
        primary=f"ANTHROPIC/{PRIMARY_BARE_KEY}",
        fallback=f"OpenAI/{FALLBACK_BARE_KEY}",
        fb_model="gpt-test",
    )
    assert result.returncode == 0, result.stderr
    outputs = gate.output_file.read_text()
    assert "primary-provider=anthropic" in outputs
    assert "fallback-provider=openai" in outputs
    # Key case preservation is asserted inside the stubs during the failover
    # test below; here the lowercased providers prove only the prefix folded.


def test_interrupted_run_continues_on_the_fallback_provider(gate):
    primary = f"anthropic/{PRIMARY_BARE_KEY}"
    fallback = f"openai/{FALLBACK_BARE_KEY}"
    setup = gate(STEP_RUNNERS, primary=primary, fallback=fallback, fb_model="gpt-test")
    assert setup.returncode == 0, setup.stderr

    audit = gate(STEP_AUDIT, primary=primary, fallback=fallback, fb_model="gpt-test")
    assert audit.returncode == 0, audit.stderr
    assert "continuing on openai" in audit.stderr

    verdict = gate(STEP_VERDICT, primary=primary, fallback=fallback)
    assert verdict.returncode == 0, verdict.stderr
    assert "semantic audit passed" in verdict.stdout

    summary = gate.summary_file.read_text()
    assert "1 (interrupted, anthropic)" in summary
    assert "2 (continued, openai)" in summary
    # The interrupted attempt reported no usage; its cells fall back to n/a.
    assert "| 1 (interrupted, anthropic) | n/a |" in summary
    assert "| 2 (continued, openai) | 50 | 900 | 40000 |" in summary
