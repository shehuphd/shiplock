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
    write_file(tmp_path, "README.md", "this is real\n")
    code = main(["check", str(tmp_path)])
    assert code == EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "banned-words" in captured.out
    assert "no shiplock.toml" in captured.err


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
    json.loads(out)  # the whole of stdout is one JSON document


# --- color discipline --------------------------------------------------------


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
