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
    # keycall pre-flights each audit key; the stub accepts every key and
    # records its argv so a test can assert which provider it was asked
    # to verify. Tests that need a rejected key overwrite this stub.
    _write_stub(
        bin_dir,
        "keycall",
        'printf "%s\\n" "$@" >> keycall-argv.txt\n'
        'echo "stub keycall: key accepted"\n',
    )
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
        'printf "%s\\n" "$@" > codex-argv.txt\n'
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
    run.bin_dir = bin_dir
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


def test_audit_effort_reaches_codex_as_a_reasoning_effort_override(gate):
    # For an openai key, audit-effort becomes codex's model_reasoning_effort
    # config override. The failover path drives codex here (claude primary dies,
    # codex fallback continues), and the effort applies to every attempt.
    primary = f"anthropic/{PRIMARY_BARE_KEY}"
    fallback = f"openai/{FALLBACK_BARE_KEY}"
    gate(STEP_RUNNERS, primary=primary, fallback=fallback, fb_model="gpt-test")
    gate(STEP_AUDIT, primary=primary, fallback=fallback, fb_model="gpt-test", effort="high")
    argv = (gate.work / "codex-argv.txt").read_text().splitlines()
    assert "-c" in argv
    assert argv[argv.index("-c") + 1] == "model_reasoning_effort=high"


def test_audit_effort_omitted_leaves_codex_default(gate):
    primary = f"anthropic/{PRIMARY_BARE_KEY}"
    fallback = f"openai/{FALLBACK_BARE_KEY}"
    gate(STEP_RUNNERS, primary=primary, fallback=fallback, fb_model="gpt-test")
    gate(STEP_AUDIT, primary=primary, fallback=fallback, fb_model="gpt-test")
    argv = (gate.work / "codex-argv.txt").read_text().splitlines()
    assert not any(a.startswith("model_reasoning_effort=") for a in argv)


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


def _picking_keycall_stub(bin_dir: Path, model_by_provider: dict[str, str]) -> None:
    """A keycall stub whose --generate verify answers with a per-provider
    model, the way the live CLI reports the catalog candidate that
    generated."""
    cases = "\n".join(
        f'  {provider}) echo "ok env:KEYCALL_PREFLIGHT_KEY: generated with {model} '
        f'(filtered position 0, provider-list position 0, 812 ms, total tokens: 42)";;'
        for provider, model in model_by_provider.items()
    )
    _write_stub(
        bin_dir,
        "keycall",
        'printf "%s\\n" "$@" >> keycall-argv.txt\n'
        'provider=""\n'
        'args=("$@")\n'
        'for ((i = 0; i < ${#args[@]}; i++)); do\n'
        '  [ "${args[$i]}" = "--provider" ] && provider="${args[$((i + 1))]}"\n'
        "done\n"
        'case " $* " in *" --generate "*) ;; *) echo "stub keycall: key accepted"; exit 0;; esac\n'
        f'case "$provider" in\n{cases}\n'
        '  *) echo "stub keycall: key accepted";;\n'
        "esac\n",
    )


def test_an_empty_model_is_picked_from_the_keys_own_catalog(gate):
    # The provider comes from the key, and with no model pinned the gate
    # routes to whatever the key's live catalog answered with, so the key
    # slot can hold any provider without the caller naming a model.
    _picking_keycall_stub(gate.bin_dir, {"anthropic": "claude-sonnet-4-5"})
    result = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}", model="")
    assert result.returncode == 0, result.stderr
    assert "primary-model=claude-sonnet-4-5" in gate.output_file.read_text()
    argv = (gate.work / "keycall-argv.txt").read_text()
    assert "--generate" in argv, "the pick verifies with a live generation"
    assert PRIMARY_BARE_KEY not in argv, "the key goes by env var, never argv"


def test_a_cross_provider_fallback_without_a_model_picks_from_its_own_key(gate):
    _picking_keycall_stub(
        gate.bin_dir, {"anthropic": "claude-sonnet-4-5", "openai": "gpt-5.1"}
    )
    result = gate(
        STEP_RUNNERS,
        primary=f"anthropic/{PRIMARY_BARE_KEY}",
        fallback=f"openai/{FALLBACK_BARE_KEY}",
        model="",
        fb_model="",
    )
    assert result.returncode == 0, result.stderr
    outputs = gate.output_file.read_text()
    assert "primary-model=claude-sonnet-4-5" in outputs
    assert "fallback-model=gpt-5.1" in outputs


def test_a_same_provider_fallback_reuses_the_picked_model(gate):
    _picking_keycall_stub(gate.bin_dir, {"anthropic": "claude-sonnet-4-5"})
    result = gate(
        STEP_RUNNERS,
        primary=f"anthropic/{PRIMARY_BARE_KEY}",
        fallback=f"anthropic/{FALLBACK_BARE_KEY}",
        model="",
        fb_model="",
    )
    assert result.returncode == 0, result.stderr
    assert "fallback-model=claude-sonnet-4-5" in gate.output_file.read_text()
    argv = (gate.work / "keycall-argv.txt").read_text()
    assert argv.count("verify") == 2, "the fallback key still gets its own check"


def test_a_pick_with_no_answering_model_is_rejected(gate):
    # The default stub accepts the key but reports no generation, the shape
    # of a key whose catalog answered with nothing; the gate must refuse to
    # run rather than guess a model for it.
    result = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}", model="")
    assert result.returncode != 0
    assert "no advertised model answered" in result.stderr


def test_a_pinned_model_reaches_the_outputs_unchanged(gate):
    result = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}", model="opus")
    assert result.returncode == 0, result.stderr
    assert "primary-model=opus" in gate.output_file.read_text()
    argv = (gate.work / "keycall-argv.txt").read_text()
    assert "--generate" not in argv, "a pinned model needs no selection call"


def test_malformed_fallback_key_is_rejected_up_front(gate):
    result = gate(
        STEP_RUNNERS,
        primary=f"anthropic/{PRIMARY_BARE_KEY}",
        fallback=FALLBACK_BARE_KEY,
    )
    assert result.returncode != 0
    assert "AUDIT_FALLBACK_API_KEY" in result.stderr


def test_an_anthropic_model_name_is_refused_on_an_openai_key(gate):
    r = gate(STEP_RUNNERS, primary=f"openai/{PRIMARY_BARE_KEY}", model="sonnet")
    assert r.returncode != 0
    assert "anthropic naming" in r.stderr
    assert "openai" in r.stderr
    assert "keycall verify" in r.stderr


def test_an_openai_model_name_is_refused_on_an_anthropic_key(gate):
    # The misconfiguration this check exists for: a gpt name against an
    # anthropic key, which otherwise 404s at run time after an attempt
    # is already spent.
    r = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}",
             model="gpt-5-codex")
    assert r.returncode != 0
    assert "openai naming" in r.stderr


def test_the_fallback_model_is_checked_against_the_fallback_key(gate):
    r = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}",
             fallback=f"openai/{FALLBACK_BARE_KEY}",
             model="sonnet", fb_model="haiku-4-5")
    assert r.returncode != 0
    assert "audit-fallback-model" in r.stderr
    assert "anthropic naming" in r.stderr


def test_a_model_outside_every_known_family_passes_the_family_check(gate):
    # A name matching no registry family is a new family, not a proven
    # mismatch; the live catalog pre-flight is the layer that judges it.
    r = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}",
             model="frontier-9000")
    assert r.returncode == 0, r.stderr


def test_each_audit_key_is_preflighted_through_keycall(gate):
    r = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}",
             fallback=f"openai/{FALLBACK_BARE_KEY}",
             model="sonnet", fb_model="gpt-test")
    assert r.returncode == 0, r.stderr
    argv = (gate.work / "keycall-argv.txt").read_text()
    assert argv.count("verify") == 2
    assert "anthropic" in argv and "openai" in argv
    assert PRIMARY_BARE_KEY not in argv, "the key travels by env var, never argv"


def test_a_key_the_provider_rejects_fails_before_any_attempt(gate):
    _write_stub(
        gate.bin_dir,
        "keycall",
        'echo "x stub keycall: key rejected by provider" >&2\n'
        "exit 1\n",
    )
    r = gate(STEP_RUNNERS, primary=f"anthropic/{PRIMARY_BARE_KEY}")
    assert r.returncode != 0
    assert "failed live verification" in r.stderr
    assert "key rejected by provider" in r.stderr


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


def _judge(gate, result_text):
    """Seed audit.json with a chosen result and run the verdict step over it."""
    primary = f"anthropic/{PRIMARY_BARE_KEY}"
    gate(STEP_RUNNERS, primary=primary)
    (gate.work / "audit.json").write_text(json.dumps({"result": result_text}))
    return gate(STEP_VERDICT, primary=primary)


def test_verdict_accepts_an_exact_final_line_with_trailing_space(gate):
    # The contract line is the last non-blank line; trailing whitespace and
    # trailing blank lines are tolerated.
    result = _judge(gate, "read the docs\nAUDIT: PASS  \n\n")
    assert result.returncode == 0, result.stderr
    assert "semantic audit passed" in result.stdout


def test_verdict_opens_an_issue_on_an_exact_fail(gate):
    result = _judge(gate, "found a problem\nAUDIT: FAIL")
    assert result.returncode == 1
    assert "semantic audit failed" in result.stderr


def test_verdict_rejects_a_passing_lookalike(gate):
    # "AUDIT: PASSING" is not the verdict; a loose substring match would have
    # read it as a pass. Fail closed.
    result = _judge(gate, "all green\nAUDIT: PASSING")
    assert result.returncode == 1
    assert "no valid AUDIT verdict" in result.stderr


def test_verdict_rejects_text_after_the_verdict_line(gate):
    # A verdict that isn't the final line violates the contract: content after
    # it means the run didn't end on a clean verdict. Fail closed.
    result = _judge(gate, "notes\nAUDIT: PASS\nHope this helps!")
    assert result.returncode == 1
    assert "no valid AUDIT verdict" in result.stderr


def test_failover_preserves_the_interrupted_attempts_output_bundle(gate):
    # A primary that writes a full bundle (envelope plus a provider's
    # intermediates) then dies must have every piece preserved under
    # audit-first.* before the fallback reuses the audit.json* names, so the
    # failed first attempt stays debuggable. The claude stub here stands in for
    # a codex/gemini primary by leaving .events/.raw beside its envelope.
    primary = f"anthropic/{PRIMARY_BARE_KEY}"
    fallback = f"openai/{FALLBACK_BARE_KEY}"
    _write_stub(
        gate.bin_dir,
        "claude",
        f'[ "$ANTHROPIC_API_KEY" = "{PRIMARY_BARE_KEY}" ] || {{ echo "wrong key" >&2; exit 99; }}\n'
        'printf "partial events\\n" > audit.json.events\n'
        'printf "partial raw\\n" > audit.json.raw\n'
        'printf "Q1 settled\\n" > audit-progress.md\n'
        'printf "{\\"result\\":\\"partial\\"}\\n"\n'
        'echo "stub claude: dying after partial output" >&2\n'
        "exit 1\n",
    )
    gate(STEP_RUNNERS, primary=primary, fallback=fallback, fb_model="gpt-test")
    gate(STEP_AUDIT, primary=primary, fallback=fallback, fb_model="gpt-test")
    assert (gate.work / "audit-first.json.events").read_text() == "partial events\n"
    assert (gate.work / "audit-first.json.raw").read_text() == "partial raw\n"
    assert json.loads((gate.work / "audit-first.json").read_text())["result"] == "partial"
    # The fallback's fresh output owns the audit.json* names.
    assert json.loads((gate.work / "audit.json").read_text())["result"]


def test_upload_step_keeps_every_attempts_raw_output():
    doc = yaml.safe_load(GATE.read_text())
    upload = next(
        step for step in doc["jobs"]["audit"]["steps"]
        if step.get("name", "").startswith("Upload audit debug")
    )
    paths = upload["with"]["path"]
    # Gemini's raw response file, for both the sole and the interrupted-first
    # attempt, must be in the debug bundle.
    assert "audit.json.raw" in paths
    assert "audit-first.json.raw" in paths


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
