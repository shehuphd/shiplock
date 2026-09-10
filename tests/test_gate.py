"""The gate workflow's shell orchestration, run against stub agent CLIs.

The audit job's step scripts are extracted from gate.yml itself (so these
tests exercise the committed scripts, not a copy), the GitHub expression
placeholders are substituted with test values, and the scripts run under bash
with stub `claude`/`codex`/`npm` binaries on PATH. Failing cases first.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
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


def _registry_providers() -> list[str]:
    """The provider column of the gate's adapter registry (the ADAPTERS table).

    Parsed from the runners step so a test can assert against whatever set the
    registry carries, without restating it (and its count) in the test.
    """
    block = re.search(r"ADAPTERS='(.*?)'", _step_scripts()[STEP_RUNNERS], re.DOTALL)
    assert block, "no ADAPTERS registry found in the runners step"
    return [
        line.strip().split("|", 1)[0]
        for line in block.group(1).splitlines()
        if line.strip()
    ]


def _substitute(
    script: str, *, model: str, fb_model: str, effort: str, outputs: dict[str, str]
) -> str:
    """Replace ${{ ... }} expressions the way the workflow runtime would."""

    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        inputs = {
            "inputs.audit-model": model,
            "inputs.audit-fallback-model": fb_model,
            "inputs.audit-permission-mode": "dontAsk",
            "inputs.audit-effort": effort,
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
    # The claude stub asserts the bare key arrived case-preserved, records its
    # argv (so a test can check which flags it was actually invoked with),
    # settles two questions into the progress log, then dies the way an API
    # failure does.
    _write_stub(
        bin_dir,
        "claude",
        f'[ "$ANTHROPIC_API_KEY" = "{PRIMARY_BARE_KEY}" ] || {{ echo "wrong key" >&2; exit 99; }}\n'
        'printf "%s\\n" "$@" > claude-argv.txt\n'
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
    # The gemini stub asserts its key arrived, records argv, then prints one
    # JSON object the way `gemini --output-format json` does: a response with a
    # PASS verdict and a stats block whose per-model token counts the adapter
    # maps to input/output/cache.
    _write_stub(
        bin_dir,
        "gemini",
        f'[ "$GEMINI_API_KEY" = "{PRIMARY_BARE_KEY}" ] || {{ echo "wrong key" >&2; exit 99; }}\n'
        'printf "%s\\n" "$@" > gemini-argv.txt\n'
        "echo '{\"response\":\"read the docs\\nAUDIT: PASS\","
        "\"stats\":{\"models\":{\"gemini-2.5-pro\":{\"tokens\":"
        "{\"prompt\":40,\"candidates\":900,\"cached\":40000,\"thoughts\":120,\"total\":41060}}}}}'\n",
    )

    scripts = _step_scripts()
    gh_output = work / "gh_output"
    gh_summary = work / "gh_summary"
    gh_output.touch()
    gh_summary.touch()

    def run(step: str, *, primary="", fallback="", model="sonnet", fb_model="", effort=""):
        outputs = {}
        for line in gh_output.read_text().splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
        script = _substitute(
            scripts[step], model=model, fb_model=fb_model, effort=effort, outputs=outputs
        )
        return subprocess.run(
            ["bash", "-c", script],
            cwd=work,
            capture_output=True,
            text=True,
            env={
                # The running interpreter's own bin dir goes first so the
                # embedded rates_cost() picks up the same "rates" install
                # this test suite runs under (real CI installs it the same
                # way: pip install rates into the job's own interpreter).
                "PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin",
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
    run.work = work
    return run


def test_audit_effort_reaches_claude_only_when_set(gate):
    primary = f"anthropic/{PRIMARY_BARE_KEY}"
    gate(STEP_RUNNERS, primary=primary)
    gate(STEP_AUDIT, primary=primary, effort="medium")
    argv = (gate.work / "claude-argv.txt").read_text().splitlines()
    assert "--effort" in argv
    assert argv[argv.index("--effort") + 1] == "medium"


def test_audit_effort_omitted_leaves_the_cli_default(gate):
    primary = f"anthropic/{PRIMARY_BARE_KEY}"
    gate(STEP_RUNNERS, primary=primary)
    gate(STEP_AUDIT, primary=primary)
    argv = (gate.work / "claude-argv.txt").read_text().splitlines()
    assert "--effort" not in argv


def test_bare_key_without_provider_prefix_is_rejected(gate):
    result = gate(STEP_RUNNERS, primary=PRIMARY_BARE_KEY)
    assert result.returncode != 0
    assert "must be 'provider/key'" in result.stderr


def test_unknown_provider_is_rejected_naming_the_known_ones(gate):
    result = gate(STEP_RUNNERS, primary="deepseek/aX-123ab")
    assert result.returncode != 0
    assert "provider 'deepseek'" in result.stderr
    # The error lists whatever providers the adapter registry carries, derived
    # from the table rather than a hardcoded pair, so a new adapter appears in
    # the message for free and nothing here assumes a fixed count.
    for provider in _registry_providers():
        assert provider in result.stderr


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
    # "gpt-test" isn't a real model rates prices, so the cost column
    # degrades to n/a rather than erroring the step.
    assert summary.rstrip().endswith("| n/a |")


def test_rates_prices_each_provider_from_its_own_usage(gate):
    # A real model name on each side, so rates actually has a rate to price
    # against — the fixed 50/900/40000 usage the claude and codex stubs
    # report translates to a specific dollar figure per provider's card.
    primary = f"anthropic/{PRIMARY_BARE_KEY}"
    fallback = f"openai/{FALLBACK_BARE_KEY}"
    setup = gate(
        STEP_RUNNERS, primary=primary, fallback=fallback,
        model="claude-sonnet-5", fb_model="gpt-5.3-codex",
    )
    assert setup.returncode == 0, setup.stderr
    gate(
        STEP_AUDIT, primary=primary, fallback=fallback,
        model="claude-sonnet-5", fb_model="gpt-5.3-codex",
    )
    verdict = gate(
        STEP_VERDICT, primary=primary, fallback=fallback,
        model="claude-sonnet-5", fb_model="gpt-5.3-codex",
    )
    assert verdict.returncode == 0, verdict.stderr

    # The codex stub's fixed usage (50 input, 40000 cache read, 900 output)
    # priced on gpt-5.3-codex's real card (cache_read_mtok 0.175, output_mtok
    # 14; fresh input clamps to 0 since cache read exceeds the input total in
    # this fixture) comes to a specific, checkable figure — not just "not
    # n/a" — proving rates actually priced it rather than degrading silently.
    summary = gate.summary_file.read_text()
    assert "| 2 (continued, openai) | 50 | 900 | 40000 | n/a | 0.0196 |" in summary


def test_google_provider_installs_the_gemini_runner(gate):
    # google is the third adapter: its provider/key resolves to the gemini CLI.
    result = gate(STEP_RUNNERS, primary=f"google/{PRIMARY_BARE_KEY}", model="gemini-2.5-pro")
    assert result.returncode == 0, result.stderr
    outputs = gate.output_file.read_text()
    assert "primary-runner=gemini" in outputs
    assert "install -g @google/gemini-cli" in result.stderr  # via the npm stub


def test_gemini_marks_the_workspace_trusted():
    # Gemini refuses to run in an untrusted checkout, overriding the approval
    # mode back to a prompt it can't answer headless and exiting before any API
    # call. The adapter must mark the workspace trusted so the run proceeds.
    script = _step_scripts()[STEP_AUDIT]
    assert "GEMINI_CLI_TRUST_WORKSPACE=true" in script


def test_gemini_runs_read_only_and_maps_its_usage(gate):
    primary = f"google/{PRIMARY_BARE_KEY}"
    setup = gate(STEP_RUNNERS, primary=primary, model="gemini-2.5-pro")
    assert setup.returncode == 0, setup.stderr
    audit = gate(STEP_AUDIT, primary=primary, model="gemini-2.5-pro")
    assert audit.returncode == 0, audit.stderr

    # The stats block's per-model tokens map to the canonical usage keys:
    # prompt -> input, candidates + thoughts -> output, cached -> cache read.
    envelope = json.loads((gate.work / "audit.json").read_text())
    assert "AUDIT: PASS" in envelope["result"]
    assert envelope["usage"] == {
        "input_tokens": 40,
        "output_tokens": 1020,
        "cache_read_input_tokens": 40000,
    }

    # The read-only guarantee: the run's own settings restrict the built-in
    # toolset to read tools, so the audit can't write or shell out even though
    # gemini ships those tools by default.
    settings = (gate.work / ".gemini" / "settings.json").read_text()
    assert "read_file" in settings and "grep_search" in settings
    assert "write_file" not in settings
    assert "run_shell_command" not in settings


def test_gemini_maps_effort_to_a_thinking_level_override(gate):
    # audit-effort is the universal reasoning knob: for a google key it becomes
    # a per-model thinkingLevel override in the workspace settings.
    primary = f"google/{PRIMARY_BARE_KEY}"
    setup = gate(STEP_RUNNERS, primary=primary, model="gemini-3.1-pro-preview", effort="low")
    assert setup.returncode == 0, setup.stderr
    audit = gate(STEP_AUDIT, primary=primary, model="gemini-3.1-pro-preview", effort="low")
    assert audit.returncode == 0, audit.stderr

    settings = json.loads((gate.work / ".gemini" / "settings.json").read_text())
    override = settings["modelConfigs"]["overrides"][0]
    assert override["match"]["model"] == "gemini-3.1-pro-preview"
    thinking = override["modelConfig"]["generateContentConfig"]["thinkingConfig"]
    assert thinking["thinkingLevel"] == "low"
    # the read-only allowlist is still present alongside the override
    assert "read_file" in settings["tools"]["core"]
    assert "write_file" not in settings["tools"]["core"]


def test_gemini_sets_no_thinking_override_when_effort_is_unset(gate):
    # Empty effort leaves Gemini at its own default (high); no override written.
    primary = f"google/{PRIMARY_BARE_KEY}"
    gate(STEP_RUNNERS, primary=primary, model="gemini-3.1-pro-preview")
    audit = gate(STEP_AUDIT, primary=primary, model="gemini-3.1-pro-preview")
    assert audit.returncode == 0, audit.stderr
    settings = json.loads((gate.work / ".gemini" / "settings.json").read_text())
    assert "modelConfigs" not in settings


# --- install gating: an app repo must not be forced to be installable ------


def _install_step(job: str) -> str:
    doc = yaml.safe_load(GATE.read_text())
    for step in doc["jobs"][job]["steps"]:
        run = step.get("run", "")
        if "pip install" in run and "pip install ." in run:
            return run
    raise AssertionError(f"no install step found in the {job!r} job")


@pytest.mark.parametrize("job", ["check", "audit"])
def test_repo_install_is_gated_on_needs_import(job):
    # Reintroducing an unconditional ``pip install .`` would break every app
    # repo (no installable package) that runs the gate, so both jobs must ask
    # ``shiplock needs-import`` before installing the checked-out repo.
    run = _install_step(job)
    assert 'shiplock needs-import' in run
    guarded = re.search(
        r'if \[ "\$\(shiplock needs-import\)" = "true" \]; then\s*'
        r'\n\s*python -m pip install \.\s*\n\s*fi',
        run,
    )
    assert guarded, f"{job} install step doesn't gate 'pip install .' on needs-import"
