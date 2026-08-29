"""CLI surface: exit-code contract, the bare-invocation welcome, prompt output."""

from __future__ import annotations

from shiplock import __version__
from shiplock.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main


def test_no_config_is_usage_error(tmp_path, capsys):
    code = main(["check", "--root", str(tmp_path)])
    assert code == EXIT_USAGE
    assert "No shiplock.toml" in capsys.readouterr().err


def test_findings_return_exit_one(tmp_path, write_file, capsys):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    code = main(["check", "--root", str(tmp_path)])
    assert code == EXIT_FINDINGS
    assert "docs-exist" in capsys.readouterr().out


def test_clean_repo_returns_exit_zero(tmp_path, write_file, capsys):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = ["README.md"]\n')
    write_file(tmp_path, "README.md", "# Demo\n")
    code = main(["check", "--root", str(tmp_path)])
    assert code == EXIT_OK


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
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert __version__ in capsys.readouterr().out
