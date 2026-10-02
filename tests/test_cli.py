"""CLI surface: usage errors first, then the exit-code contract and outputs."""

from __future__ import annotations

import json

import pytest

from shiplock import __version__
from shiplock.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main


# --- usage errors, as a person would hit them ------------------------------


def test_typoed_command_gets_a_suggestion(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["chek"])
    assert exc.value.code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "isn't a shiplock command" in err
    assert "shiplock check" in err
    assert "usage:" not in err


def test_hopeless_typo_gets_no_guess(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["zzqqxx"])
    assert exc.value.code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "isn't a shiplock command" in err
    assert "Perhaps" not in err


def test_unknown_flag_is_a_sentence(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["check", "--bogus"])
    assert exc.value.code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "unknown option" in err
    assert "--bogus" in err
    assert "usage:" not in err


def test_nonexistent_path_is_a_sentence(capsys):
    code = main(["check", "/no/such/dir"])
    assert code == EXIT_USAGE
    assert "isn't a directory" in capsys.readouterr().err


def test_malformed_config_is_usage_error(tmp_path, write_file, capsys):
    write_file(tmp_path, "shiplock.toml", "this is = = not toml")
    code = main(["check", str(tmp_path)])
    assert code == EXIT_USAGE
    assert "not valid TOML" in capsys.readouterr().err


# --- the zero-config default run -------------------------------------------


def test_default_run_checks_detected_docs(tmp_path, write_file, capsys):
    write_file(tmp_path, "README.md", "see [the guide](docs/guide.md)\n")
    code = main(["check", str(tmp_path)])
    assert code == EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "readme-links" in captured.out
    assert "no shiplock.toml" in captured.err
    # shiplock ships no word list, so the default run skips banned words.
    assert "banned-words skipped" in captured.err


def test_default_run_note_names_the_config_file(tmp_path, write_file, capsys):
    write_file(tmp_path, "README.md", "clean words only\n")
    code = main(["check", str(tmp_path)])
    assert code == EXIT_OK
    assert "no shiplock.toml" in capsys.readouterr().err


# --- the configured run and exit codes --------------------------------------


def test_findings_return_exit_one(tmp_path, write_file, capsys):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    code = main(["check", str(tmp_path)])
    assert code == EXIT_FINDINGS
    assert "docs-exist" in capsys.readouterr().out


def test_clean_repo_returns_exit_zero(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    write_file(tmp_path, "README.md", "# Demo\n")
    assert main(["check", str(tmp_path)]) == EXIT_OK


def test_path_defaults_to_current_directory(tmp_path, write_file, monkeypatch):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    write_file(tmp_path, "README.md", "# Demo\n")
    monkeypatch.chdir(tmp_path)
    assert main(["check"]) == EXIT_OK


# --- machine output ----------------------------------------------------------


def test_json_emits_one_parseable_object(tmp_path, write_file, capsys):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    code = main(["check", str(tmp_path), "--json"])
    assert code == EXIT_FINDINGS
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["ok"] is False
    assert data["findings"][0]["check"] == "docs-exist"
    assert isinstance(data["notices"], list)


def test_json_keeps_stdout_to_the_object_alone(tmp_path, write_file, capsys):
    write_file(tmp_path, "README.md", "# Demo\n")
    main(["check", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    # json.loads over the whole of stdout proves it is one document: a stray
    # notice line before or after the object would make this raise.
    data = json.loads(out)
    assert data["ok"] is True


# --- color discipline --------------------------------------------------------


class _Tty:
    """A stream stand-in whose isatty answer is fixed."""

    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_color_appears_on_a_tty(monkeypatch):
    from shiplock.cli import _RED, _paint

    monkeypatch.delenv("NO_COLOR", raising=False)
    painted = _paint("finding", _RED, _Tty(True))
    assert painted.startswith("\033[") and painted.endswith("\033[0m")


def test_no_color_env_suppresses_color_even_on_a_tty(monkeypatch):
    from shiplock.cli import _RED, _paint

    monkeypatch.setenv("NO_COLOR", "1")
    assert _paint("finding", _RED, _Tty(True)) == "finding"


def test_piped_output_carries_no_escape_codes(tmp_path, write_file, capsys):
    # capsys streams aren't ttys, so this is the piped case.
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    main(["check", str(tmp_path)])
    captured = capsys.readouterr()
    assert "\033[" not in captured.out
    assert "\033[" not in captured.err


# --- welcome and prompt ------------------------------------------------------


def test_bare_invocation_greets_and_succeeds(capsys):
    code = main([])
    assert code == EXIT_OK
    assert "shiplock" in capsys.readouterr().out


def test_prompt_ends_with_verdict_contract(capsys):
    code = main(["prompt"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "AUDIT: PASS" in out
    assert "AUDIT: FAIL" in out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_prompt_rejects_an_unknown_kind_with_a_sentence(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["prompt", "ablatoin"])
    assert exc.value.code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "isn't a shiplock prompt" in err
    assert "ablation" in err


def test_prompt_ablation_is_advisory_with_no_verdict_line(capsys):
    code = main(["prompt", "ablation"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("# Shiplock ablation audit")
    assert "AUDIT: PASS" not in out


def test_prompt_audit_kind_matches_the_default(capsys):
    main(["prompt"])
    default = capsys.readouterr().out
    main(["prompt", "audit"])
    assert capsys.readouterr().out == default


# --- needs-import: what a CI gate reads before installing the repo ---------


def test_needs_import_false_for_an_app_repo(tmp_path, write_file, capsys):
    # An app repo configures only file-reading checks, so the gate can skip
    # the pip install: it doesn't have to be an installable package.
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    code = main(["needs-import", str(tmp_path)])
    assert code == EXIT_OK
    assert capsys.readouterr().out.strip() == "false"


def test_needs_import_true_when_version_is_configured(tmp_path, write_file, capsys):
    write_file(tmp_path, "shiplock.toml", '[version]\npackage = "mypkg"\n')
    code = main(["needs-import", str(tmp_path)])
    assert code == EXIT_OK
    assert capsys.readouterr().out.strip() == "true"


def test_needs_import_true_when_coverage_is_configured(tmp_path, write_file, capsys):
    write_file(
        tmp_path,
        "shiplock.toml",
        '[[coverage]]\nobject = "mypkg"\ndoc = "README.md"\nkind = "exports"\n',
    )
    code = main(["needs-import", str(tmp_path)])
    assert code == EXIT_OK
    assert capsys.readouterr().out.strip() == "true"


def test_needs_import_defaults_to_false_and_stays_clean_on_a_bad_config(
    tmp_path, write_file, capsys
):
    # The gate must read a usable value even when the config is broken; the
    # following ``shiplock check`` surfaces the error for real.
    write_file(tmp_path, "shiplock.toml", "this is = = not toml")
    code = main(["needs-import", str(tmp_path)])
    assert code == EXIT_OK
    out = capsys.readouterr()
    assert out.out.strip() == "false"
    assert "shiplock:" in out.err


def test_needs_import_false_for_a_missing_path(capsys):
    code = main(["needs-import", "/no/such/dir"])
    assert code == EXIT_OK
    assert capsys.readouterr().out.strip() == "false"


def test_needs_import_defaults_to_the_current_directory(
    tmp_path, write_file, monkeypatch, capsys
):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    monkeypatch.chdir(tmp_path)
    code = main(["needs-import"])
    assert code == EXIT_OK
    assert capsys.readouterr().out.strip() == "false"


# --- scan: the leak & internal-reference subcommand ------------------------


def _init_git(root, write_file, relpath, text):
    import subprocess

    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    write_file(root, relpath, text)
    subprocess.run(
        ["git", "-C", str(root), "add", relpath], check=True, capture_output=True
    )
    return root


RULES = 'refs = [{ label = "drafts/", pattern = "\\\\bdrafts/" }]\n'


def _rules_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("rules") / "rules.toml"
    path.write_text(RULES, encoding="utf-8")
    return str(path)


def test_scan_finding_returns_exit_one(tmp_path, tmp_path_factory, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "see drafts/ for the plan")
    code = main(["scan", str(tmp_path), "--rules", _rules_file(tmp_path_factory)])
    assert code == EXIT_FINDINGS
    # A label from a rules file is private, so it prints masked.
    assert "internal reference (d******)" in capsys.readouterr().out


def test_scan_clean_returns_exit_zero(tmp_path, tmp_path_factory, write_file):
    _init_git(tmp_path, write_file, "notes.txt", "nothing internal here")
    assert main(["scan", str(tmp_path), "--rules", _rules_file(tmp_path_factory)]) == EXIT_OK


def test_scan_with_no_rules_skips_and_exits_zero(tmp_path, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "see drafts/ for the plan")
    assert main(["scan", str(tmp_path)]) == EXIT_OK
    assert "no rules declared" in capsys.readouterr().err


def test_scan_reads_the_default_rules_file(tmp_path, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "see drafts/ for the plan")
    write_file(tmp_path, ".gitignore", "shiplock.local.toml\n")
    write_file(tmp_path, "shiplock.local.toml", RULES)
    assert main(["scan", str(tmp_path)]) == EXIT_FINDINGS


def test_scan_bad_path_is_usage_error(capsys):
    code = main(["scan", "/no/such/dir"])
    assert code == EXIT_USAGE
    assert "isn't a directory it can scan" in capsys.readouterr().err


def test_scan_json_emits_one_parseable_object(tmp_path, tmp_path_factory, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "see drafts/ plan")
    code = main(["scan", str(tmp_path), "--json", "--rules", _rules_file(tmp_path_factory)])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_FINDINGS
    assert payload["ok"] is False
    assert payload["findings"][0]["check"] == "scan"


def test_check_passes_rules_to_internal_refs(tmp_path, tmp_path_factory, write_file, capsys):
    _init_git(tmp_path, write_file, "README.md", "see drafts/ plan\n")
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    code = main(["check", str(tmp_path), "--rules", _rules_file(tmp_path_factory)])
    assert code == EXIT_FINDINGS
    assert "internal-refs" in capsys.readouterr().out


def test_check_reports_a_deprecated_key(tmp_path, write_file, capsys):
    write_file(tmp_path, "README.md", "fine\n")
    write_file(
        tmp_path,
        "shiplock.toml",
        '[docs]\npublic = ["README.md"]\n[style]\nextra_banned = ["leverage"]\n',
    )
    main(["check", str(tmp_path)])
    assert "extra_banned is deprecated" in capsys.readouterr().err


def test_allowed_match_renders_as_a_warning_not_a_skip(tmp_path, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "see drafts/ for the plan")
    write_file(tmp_path, ".gitignore", "shiplock.local.toml\n")
    write_file(tmp_path, "shiplock.local.toml", RULES + 'allow = ["drafts/"]\n')
    assert main(["scan", str(tmp_path)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "scan warning — allowed reference d******: 1 match in notes.txt" in err
    assert "scan skipped — allowed" not in err


def test_json_notices_carry_their_kind(tmp_path, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "see drafts/ for the plan")
    write_file(tmp_path, ".gitignore", "shiplock.local.toml\n")
    write_file(tmp_path, "shiplock.local.toml", RULES + 'allow = ["drafts/"]\n')
    main(["scan", str(tmp_path), "--json"])
    kinds = {n["kind"] for n in json.loads(capsys.readouterr().out)["notices"]}
    assert kinds == {"skip", "warning"}


def _commit_all(root, message: str) -> str:
    import subprocess

    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_scan_range_reads_the_pushed_commits(tmp_path, tmp_path_factory, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "clean\n")
    base = _commit_all(tmp_path, "base")
    write_file(tmp_path, "notes.txt", "see drafts/ plan\n")
    _commit_all(tmp_path, "mention drafts/ in notes")
    write_file(tmp_path, "notes.txt", "clean again\n")
    _commit_all(tmp_path, "tidy")
    rules = _rules_file(tmp_path_factory)

    assert main(["scan", str(tmp_path), "--rules", rules]) == EXIT_OK
    capsys.readouterr()
    code = main(["scan", str(tmp_path), "--rules", rules, "--range", f"{base}..HEAD"])
    out = capsys.readouterr().out
    assert code == EXIT_FINDINGS
    assert "notes.txt:1" in out
    assert "commit " in out


def test_scan_staged_reads_the_index(tmp_path, tmp_path_factory, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "see drafts/ plan\n")
    code = main(["scan", str(tmp_path), "--rules", _rules_file(tmp_path_factory), "--staged"])
    assert code == EXIT_FINDINGS
    assert "notes.txt:1" in capsys.readouterr().out


def test_scan_range_and_staged_are_mutually_exclusive(tmp_path, write_file, capsys):
    _init_git(tmp_path, write_file, "notes.txt", "x\n")
    with pytest.raises(SystemExit) as exc:
        main(["scan", str(tmp_path), "--range", "HEAD~1..HEAD", "--staged"])
    assert exc.value.code == EXIT_USAGE


def test_user_rules_info_renders_as_info(tmp_path, write_file, monkeypatch, capsys):
    home = tmp_path / "xdg" / "shiplock"
    home.mkdir(parents=True)
    (home / "rules.toml").write_text('code_names = ["bluebird"]\n', encoding="utf-8")
    monkeypatch.delenv("SHIPLOCK_NO_USER_RULES", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    _init_git(repo, write_file, "notes.txt", "clean\n")
    assert main(["scan", str(repo)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "scan info — 1 rule(s) from the user rules file" in err
    assert "skipped" not in err.split("scan info")[1].split("\n")[0]
