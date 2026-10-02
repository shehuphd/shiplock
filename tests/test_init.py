"""``shiplock init``: each step, idempotence, never-overwrite, and the hooks."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from shiplock._init import HOOK_LINES, hook_text_for, run_init
from shiplock._rules import HEADER


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch, tmp_path):
    """No global or system git config, so the developer's hooksPath can't leak in."""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def test_init_writes_every_piece_in_a_git_repo(git_repo, write_file):
    write_file(git_repo, "README.md", "# hi\n")
    write_file(git_repo, "CHANGELOG.md", "# log\n")
    result = run_init(git_repo)
    text = "\n".join(result.lines)
    assert "shiplock.toml: written (README.md, CHANGELOG.md)" in text
    assert "shiplock.local.toml: written" in text
    assert ".gitignore: written with shiplock.local.toml" in text
    assert "pre-commit hook: installed" in text and "pre-push hook: installed" in text
    config = (git_repo / "shiplock.toml").read_text(encoding="utf-8")
    assert 'public = ["README.md", "CHANGELOG.md"]' in config
    assert 'changelog = "CHANGELOG.md"' in config and 'readme = "README.md"' in config
    assert (git_repo / "shiplock.local.toml").read_text(encoding="utf-8") == HEADER
    for name in ("pre-commit", "pre-push"):
        hook = git_repo / ".git" / "hooks" / name
        assert hook.stat().st_mode & stat.S_IXUSR
        assert hook.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


def test_init_is_idempotent_and_never_overwrites(git_repo, write_file):
    write_file(git_repo, "shiplock.toml", "[docs]\npublic = []\n")
    write_file(git_repo, "shiplock.local.toml", "# mine\n")
    write_file(git_repo, ".gitignore", "shiplock.local.toml\n")
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    result = run_init(git_repo)
    text = "\n".join(result.lines)
    assert "shiplock.toml: exists, left as is" in text
    assert "shiplock.local.toml: exists, left as is" in text
    assert ".gitignore: already covers" in text
    assert "pre-commit hook: exists, left as is" in text
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho custom\n"
    assert result.hooks_to_add == {"pre-commit": HOOK_LINES["pre-commit"]}
    assert (git_repo / "shiplock.local.toml").read_text(encoding="utf-8") == "# mine\n"
    before = (git_repo / ".gitignore").read_text(encoding="utf-8")
    run_init(git_repo)
    assert (git_repo / ".gitignore").read_text(encoding="utf-8") == before


def test_init_appends_to_a_gitignore_without_a_trailing_newline(git_repo, write_file):
    write_file(git_repo, ".gitignore", "node_modules/")
    run_init(git_repo, hooks=False)
    assert (git_repo / ".gitignore").read_text(encoding="utf-8") == "node_modules/\nshiplock.local.toml\n"


def test_init_gitignore_glob_already_covering_the_file_counts(git_repo, write_file):
    write_file(git_repo, ".gitignore", "*.local.toml\n")
    result = run_init(git_repo, hooks=False)
    assert any("already covers" in line for line in result.lines)


def test_init_outside_a_git_repo_skips_hooks(tmp_path, write_file):
    write_file(tmp_path, "README.md", "x\n")
    result = run_init(tmp_path)
    assert any("not a git repo, so none installed" in line for line in result.lines)
    assert (tmp_path / "shiplock.toml").is_file()


def test_init_no_hooks_flag(git_repo):
    result = run_init(git_repo, hooks=False)
    assert not any("hook" in line for line in result.lines)


def test_init_uses_a_repo_local_hooks_path(git_repo):
    _git(git_repo, "config", "--local", "core.hooksPath", ".githooks")
    run_init(git_repo)
    assert (git_repo / ".githooks" / "pre-commit").is_file()
    assert not (git_repo / ".git" / "hooks" / "pre-commit").exists()


def test_init_never_writes_into_a_shared_hooks_path(git_repo, tmp_path, monkeypatch):
    shared = tmp_path / "shared-hooks"
    shared.mkdir()
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[core]\n\thooksPath = {shared}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    result = run_init(git_repo)
    assert list(shared.iterdir()) == []
    assert not (git_repo / ".git" / "hooks" / "pre-commit").exists()
    assert any("set outside this repo" in line for line in result.lines)
    assert set(result.hooks_to_add) == {"pre-commit", "pre-push"}


def test_hook_scripts_quote_the_interpreter_and_name_the_scan_mode():
    pre_commit = hook_text_for("pre-commit", "/opt/py thon/bin/python")
    assert "SHIPLOCK_PY='/opt/py thon/bin/python'" in pre_commit
    assert "run_shiplock scan --staged" in pre_commit
    pre_push = hook_text_for("pre-push")
    assert 'run_shiplock scan --range "$range"' in pre_push
    assert "--not --remotes" in pre_push


def _stage_leak(git_repo, write_file):
    write_file(git_repo, ".gitignore", "shiplock.local.toml\n")
    write_file(git_repo, "shiplock.local.toml", 'code_names = ["bluebird"]\n')
    write_file(git_repo, "notes.txt", "ship the bluebird build\n")
    _git(git_repo, "add", ".gitignore", "notes.txt")


def test_installed_pre_commit_hook_blocks_a_staged_leak(git_repo, write_file):
    _stage_leak(git_repo, write_file)
    run_init(git_repo)
    proc = subprocess.run(
        ["sh", str(git_repo / ".git" / "hooks" / "pre-commit")],
        cwd=git_repo, capture_output=True, text=True,
        env={**os.environ, "SHIPLOCK_NO_USER_RULES": "1"},
    )
    assert proc.returncode == 1
    assert "internal code-name (b*******)" in proc.stdout


def test_installed_pre_commit_hook_passes_a_clean_stage(git_repo, write_file):
    write_file(git_repo, ".gitignore", "shiplock.local.toml\n")
    write_file(git_repo, "shiplock.local.toml", 'code_names = ["bluebird"]\n')
    write_file(git_repo, "notes.txt", "clean\n")
    _git(git_repo, "add", ".gitignore", "notes.txt")
    run_init(git_repo)
    proc = subprocess.run(
        ["sh", str(git_repo / ".git" / "hooks" / "pre-commit")],
        cwd=git_repo, capture_output=True, text=True,
        env={**os.environ, "SHIPLOCK_NO_USER_RULES": "1"},
    )
    assert proc.returncode == 0, proc.stderr


def test_installed_pre_push_hook_scans_the_pushed_range(git_repo, write_file):
    _stage_leak(git_repo, write_file)
    _git(git_repo, "commit", "-q", "-m", "base")
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    write_file(git_repo, "notes.txt", "clean now\n")
    _git(git_repo, "add", "notes.txt")
    _git(git_repo, "commit", "-q", "-m", "mention bluebird in the message")
    head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    run_init(git_repo)
    proc = subprocess.run(
        ["sh", str(git_repo / ".git" / "hooks" / "pre-push"), "origin", "x"],
        cwd=git_repo, capture_output=True, text=True,
        input=f"refs/heads/main {head} refs/heads/main {base}\n",
        env={**os.environ, "SHIPLOCK_NO_USER_RULES": "1"},
    )
    assert proc.returncode == 1
    assert "commit " in proc.stdout


def test_hook_fails_closed_when_shiplock_cannot_be_found(git_repo, write_file):
    _stage_leak(git_repo, write_file)
    run_init(git_repo, interpreter=str(git_repo / "no-such-python"))
    proc = subprocess.run(
        ["sh", str(git_repo / ".git" / "hooks" / "pre-commit")],
        cwd=git_repo, capture_output=True, text=True,
        env={**os.environ, "PATH": f"{git_repo}:/usr/bin:/bin", "SHIPLOCK_NO_USER_RULES": "1"},
    )
    assert proc.returncode == 1
    assert "can't run" in proc.stderr and "--no-verify" in proc.stderr


def test_hook_falls_back_to_path_when_the_interpreter_moved(git_repo, write_file):
    _stage_leak(git_repo, write_file)
    run_init(git_repo, interpreter=str(git_repo / "no-such-python"))
    shim_dir = git_repo / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "shiplock"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m shiplock "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    proc = subprocess.run(
        ["sh", str(git_repo / ".git" / "hooks" / "pre-commit")],
        cwd=git_repo, capture_output=True, text=True,
        env={**os.environ, "PATH": f"{shim_dir}:/usr/bin:/bin", "SHIPLOCK_NO_USER_RULES": "1"},
    )
    assert proc.returncode == 1
    assert "internal code-name" in proc.stdout
