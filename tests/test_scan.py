"""The leak & internal-reference scan.

The scan reads the git-tracked set, so every test stages its files before
scanning; an unstaged file is invisible to ``git ls-files`` and so to the scan,
which one test asserts directly. Fixtures live in a temp git repo (``git_repo``)
so the tracked-file model, the blocklist refusal, and the excludes run against
git's own output rather than a mock.
"""

from __future__ import annotations

import subprocess

import pytest

from shiplock._checks import check_internal_refs, check_scan
from shiplock._config import Config, RefPattern, ScanConfig
from shiplock._scan import (
    build_ref_patterns,
    default_scan_config,
    git_tracked_files,
    mask,
    run_scan,
)


def _add(root, *paths: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", *paths], check=True, capture_output=True)


def _messages(findings) -> list[str]:
    return [f.message for f in findings]


# --------------------------------------------------------------------------
# Tracked-file model
# --------------------------------------------------------------------------


def test_scan_reads_tracked_files_not_only_docs(git_repo, write_file):
    # A fixture, not a declared public doc: internal-refs would miss it, scan
    # catches it because it reads the tracked set.
    write_file(git_repo, "tests/fixtures/sample.txt", "see project/ for the plan")
    _add(git_repo, "tests/fixtures/sample.txt")
    findings, _ = run_scan(Config(root=git_repo), default_scan_config())
    assert any("internal reference to project/" in m for m in _messages(findings))


def test_scan_ignores_untracked_files(git_repo, write_file):
    write_file(git_repo, "leak.txt", "project/ mentioned here")
    # Deliberately not staged.
    findings, _ = run_scan(Config(root=git_repo), default_scan_config())
    assert findings == []


def test_scan_notices_when_not_a_git_repo(tmp_path):
    findings, notices = run_scan(Config(root=tmp_path), default_scan_config())
    assert findings == []
    assert any("not a git repo" in n.message for n in notices)


# --------------------------------------------------------------------------
# Internal-reference patterns (house + new + caller extras)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,label",
    [
        ("see project/ok", "project/"),
        ("read CODING.md first", "CODING.md"),
        ("the ROADMAP says", "ROADMAP"),
        ("logged in BUGS.md", "BUGS.md"),
        ("noted in LESSONS.md", "LESSONS.md"),
        ("tracked in TESTS.md", "TESTS.md"),
        ("the .claude dir", ".claude"),
    ],
)
def test_scan_fires_on_house_ref_patterns(git_repo, write_file, line, label):
    write_file(git_repo, "notes.txt", line)
    _add(git_repo, "notes.txt")
    findings, _ = run_scan(Config(root=git_repo), default_scan_config())
    assert any(f"internal reference to {label}" in m for m in _messages(findings))


def test_scan_carves_out_pypi_project_url(git_repo, write_file):
    write_file(git_repo, "README.md", "install from https://pypi.org/project/shiplock/")
    _add(git_repo, "README.md")
    findings, _ = run_scan(Config(root=git_repo), default_scan_config())
    assert findings == []


def test_scan_honours_caller_extra_ref_patterns(git_repo, write_file):
    write_file(git_repo, "notes.txt", "internal codename ACME-7 here")
    _add(git_repo, "notes.txt")
    scan = ScanConfig(extra_refs=[RefPattern(label="ACME ticket", pattern=r"ACME-\d+")])
    findings, _ = run_scan(Config(root=git_repo, scan=scan), scan)
    assert any("internal reference to ACME ticket" in m for m in _messages(findings))


def test_extra_ref_patterns_also_extend_internal_refs(tmp_path, write_file):
    write_file(tmp_path, "README.md", "mentions ACME-7")
    scan = ScanConfig(extra_refs=[RefPattern(label="ACME ticket", pattern=r"ACME-\d+")])
    from shiplock._config import DocsConfig

    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]), scan=scan)
    findings, _ = check_internal_refs(config)
    assert any("internal reference to ACME ticket" in m for m in _messages(findings))


def test_build_ref_patterns_includes_house_and_extras():
    labels = [label for label, _ in build_ref_patterns()]
    assert "project/" in labels and "BUGS.md" in labels
    extended = [label for label, _ in build_ref_patterns([RefPattern("X", r"x")])]
    assert "X" in extended and len(extended) == len(labels) + 1


# --------------------------------------------------------------------------
# Identity classes, from the blocklist
# --------------------------------------------------------------------------


def _blocklisted_repo(git_repo, write_file, line: str):
    write_file(git_repo, ".gitignore", ".secrets.toml\n")
    write_file(
        git_repo,
        ".secrets.toml",
        'code_names = ["bluebird"]\nhome_usernames = ["ada"]\n'
        'emails = ["person@example.com"]\n',
    )
    write_file(git_repo, "doc.txt", line)
    _add(git_repo, ".gitignore", "doc.txt")  # blocklist stays untracked
    scan = ScanConfig(blocklist=[".secrets.toml"])
    return Config(root=git_repo, scan=scan), scan


def test_scan_fires_on_code_name(git_repo, write_file):
    config, scan = _blocklisted_repo(git_repo, write_file, "ship the bluebird build")
    findings, _ = run_scan(config, scan)
    assert any(m.startswith("internal code-name") for m in _messages(findings))


def test_scan_fires_on_home_path(git_repo, write_file):
    config, scan = _blocklisted_repo(git_repo, write_file, "built under /Users/ada/dev")
    findings, _ = run_scan(config, scan)
    assert any(m.startswith("home path for user") for m in _messages(findings))


def test_scan_fires_on_personal_email(git_repo, write_file):
    config, scan = _blocklisted_repo(git_repo, write_file, "reach person@example.com")
    findings, _ = run_scan(config, scan)
    assert any(m.startswith("personal email") for m in _messages(findings))


def test_scan_masks_the_matched_string(git_repo, write_file):
    config, scan = _blocklisted_repo(git_repo, write_file, "ship the bluebird build")
    findings, _ = run_scan(config, scan)
    code = next(f for f in findings if f.message.startswith("internal code-name"))
    assert "bluebird" not in code.message
    assert "b*******" in code.message


def test_scan_notices_and_skips_identity_without_blocklist(git_repo, write_file):
    write_file(git_repo, "doc.txt", "ship the bluebird build under /Users/ada")
    _add(git_repo, "doc.txt")
    findings, notices = run_scan(Config(root=git_repo), default_scan_config())
    assert not any(m.startswith("internal code-name") for m in _messages(findings))
    assert any("no blocklist configured" in n.message for n in notices)


# --------------------------------------------------------------------------
# Blocklist safety
# --------------------------------------------------------------------------


def test_scan_refuses_a_tracked_blocklist(git_repo, write_file):
    write_file(git_repo, "blocklist.toml", 'code_names = ["bluebird"]\n')
    write_file(git_repo, "doc.txt", "ship the bluebird build")
    _add(git_repo, "blocklist.toml", "doc.txt")  # blocklist IS tracked
    scan = ScanConfig(blocklist=["blocklist.toml"])
    findings, _ = run_scan(Config(root=git_repo, scan=scan), scan)
    assert any("is git-tracked" in m for m in _messages(findings))
    # A refused blocklist doesn't load, so the code-name isn't scanned for.
    assert not any(m.startswith("internal code-name") for m in _messages(findings))


def test_scan_notices_a_missing_blocklist(git_repo, write_file):
    write_file(git_repo, "doc.txt", "nothing to see")
    _add(git_repo, "doc.txt")
    scan = ScanConfig(blocklist=["nope.toml"])
    _, notices = run_scan(Config(root=git_repo, scan=scan), scan)
    assert any("not found" in n.message for n in notices)


# --------------------------------------------------------------------------
# Secrets switch (opt-in)
# --------------------------------------------------------------------------


def test_scan_skips_secrets_by_default(git_repo, write_file):
    write_file(git_repo, "conf.txt", "aws = AKIAABCDEFGHIJKLMNOP")
    _add(git_repo, "conf.txt")
    findings, _ = run_scan(Config(root=git_repo), default_scan_config())
    assert not any("possible secret" in m for m in _messages(findings))


def test_scan_fires_on_secrets_when_enabled(git_repo, write_file):
    write_file(git_repo, "conf.txt", "aws = AKIAABCDEFGHIJKLMNOP")
    _add(git_repo, "conf.txt")
    scan = ScanConfig(secrets=True)
    findings, _ = run_scan(Config(root=git_repo, scan=scan), scan)
    secret = next((f for f in findings if "possible secret" in f.message), None)
    assert secret is not None
    assert "AKIAABCDEFGHIJKLMNOP" not in secret.message  # masked


# --------------------------------------------------------------------------
# Excludes and binary files
# --------------------------------------------------------------------------


def test_scan_house_excludes_gitignore(git_repo, write_file):
    # .gitignore naming an internal dir is the prevention, not a leak.
    write_file(git_repo, ".gitignore", "project/\n.claude/\n")
    _add(git_repo, ".gitignore")
    findings, _ = run_scan(Config(root=git_repo), default_scan_config())
    assert findings == []


def test_scan_honours_configured_exclude(git_repo, write_file):
    write_file(git_repo, "vendor/thirdparty.txt", "references project/ internally")
    _add(git_repo, "vendor/thirdparty.txt")
    scan = ScanConfig(exclude=["vendor/**"])
    findings, _ = run_scan(Config(root=git_repo, scan=scan), scan)
    assert findings == []


def test_scan_skips_binary_files(git_repo, write_file):
    path = git_repo / "blob.bin"
    path.write_bytes(b"project/\x00\x01\x02binary")
    _add(git_repo, "blob.bin")
    findings, _ = run_scan(Config(root=git_repo), default_scan_config())
    assert findings == []


# --------------------------------------------------------------------------
# The scan check wrapper and git_tracked_files
# --------------------------------------------------------------------------


def test_check_scan_skips_without_section(git_repo):
    findings, notices = check_scan(Config(root=git_repo))
    assert findings == []
    assert any("no [scan] declared" in n.message for n in notices)


def test_check_scan_runs_with_section(git_repo, write_file):
    write_file(git_repo, "doc.txt", "see project/ plan")
    _add(git_repo, "doc.txt")
    config = Config(root=git_repo, scan=ScanConfig())
    findings, _ = check_scan(config)
    assert any("internal reference to project/" in m for m in _messages(findings))


def test_git_tracked_files_lists_staged(git_repo, write_file):
    write_file(git_repo, "a.txt", "x")
    write_file(git_repo, "b.txt", "y")
    _add(git_repo, "a.txt")
    tracked = git_tracked_files(git_repo)
    assert tracked == ["a.txt"]


def test_git_tracked_files_none_outside_repo(tmp_path):
    assert git_tracked_files(tmp_path) is None


# --------------------------------------------------------------------------
# mask
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [("bluebird", "b*******"), ("ab", "a*"), ("x", "x"), ("", "")],
)
def test_mask(value, expected):
    assert mask(value) == expected


# --------------------------------------------------------------------------
# Blocklist-path carve-out (a configured path is not an internal-ref leak)
# --------------------------------------------------------------------------


def test_scan_exempts_a_configured_blocklist_path_from_refs(git_repo, write_file):
    # The recommended ~/.claude blocklist home must not trip the .claude pattern
    # on the line that declares it.
    write_file(git_repo, "conf.txt", 'blocklist = ["~/.claude/shiplock-blocklist.toml"]')
    _add(git_repo, "conf.txt")
    scan = ScanConfig(blocklist=["~/.claude/shiplock-blocklist.toml"])
    findings, _ = run_scan(Config(root=git_repo, scan=scan), scan)
    assert findings == []


def test_scan_still_flags_claude_outside_the_blocklist_path(git_repo, write_file):
    write_file(git_repo, "conf.txt", "see the .claude directory for tooling")
    _add(git_repo, "conf.txt")
    scan = ScanConfig(blocklist=["~/.claude/shiplock-blocklist.toml"])
    findings, _ = run_scan(Config(root=git_repo, scan=scan), scan)
    assert any("internal reference to .claude" in m for m in _messages(findings))


def test_internal_refs_exempts_a_configured_blocklist_path(tmp_path, write_file):
    from shiplock._config import DocsConfig

    write_file(tmp_path, "USAGE.md", 'set blocklist = ["~/.claude/shiplock-blocklist.toml"]')
    scan = ScanConfig(blocklist=["~/.claude/shiplock-blocklist.toml"])
    config = Config(root=tmp_path, docs=DocsConfig(public=["USAGE.md"]), scan=scan)
    findings, _ = check_internal_refs(config)
    assert findings == []


def test_strip_exempt_blanks_only_the_exempt_substring():
    from shiplock._scan import strip_exempt

    out = strip_exempt("a ~/.claude/x and a .codex ref", ("~/.claude/x",))
    assert "~/.claude/x" not in out
    assert ".codex" in out  # untouched
    assert len(out) == len("a ~/.claude/x and a .codex ref")  # length preserved
