"""The declared-rules loader and the leak & internal-reference scan.

The scan reads the git-tracked set, so every test stages its files before
scanning; an unstaged file is invisible to ``git ls-files`` and so to the scan,
which one test asserts directly. Fixtures live in a temp git repo (``git_repo``)
so the tracked-file model, the rules-file refusal, and the excludes run against
git's own output rather than a mock. shiplock ships no rules, so each test
declares the ones it exercises.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from conftest import starter_refs
from shiplock._checks import check_internal_refs, check_scan
from shiplock._config import Config, DocsConfig, RefPattern, ScanConfig
from shiplock._scan import (
    default_scan_config,
    git_tracked_files,
    mask,
    run_scan,
    run_scan_range,
    run_scan_staged,
    strip_exempt,
)

RULES = (
    'refs = [{ label = "plan dir", pattern = "internal-plans/" }]\n'
    'code_names = ["bluebird"]\n'
    'home_usernames = ["ada"]\n'
    'emails = ["person@example.com"]\n'
)


def _add(root, *paths: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", *paths], check=True, capture_output=True)


def _messages(items) -> list[str]:
    return [i.message for i in items]


def _committed(root, **scan_kwargs):
    scan = ScanConfig(**scan_kwargs)
    return Config(root=root, scan=scan), scan


def _with_rules(git_repo, write_file, line: str, rules: str = RULES, name="notes.txt"):
    """A repo with a gitignored default rules file and one tracked file."""
    write_file(git_repo, ".gitignore", "shiplock.local.toml\n")
    write_file(git_repo, "shiplock.local.toml", rules)
    write_file(git_repo, name, line)
    _add(git_repo, ".gitignore", name)
    scan = ScanConfig()
    return Config(root=git_repo, scan=scan), scan


# --------------------------------------------------------------------------
# No built-in rules
# --------------------------------------------------------------------------


def test_scan_skips_when_no_rules_are_declared(git_repo, write_file):
    # The old built-ins would have fired here; with none declared, it's a skip.
    write_file(git_repo, "notes.txt", "see drafts/ and .claude/ for the plan")
    _add(git_repo, "notes.txt")
    findings, notices = run_scan(Config(root=git_repo), default_scan_config())
    assert findings == []
    assert any("no rules declared" in m for m in _messages(notices))


# --------------------------------------------------------------------------
# Tracked-file model
# --------------------------------------------------------------------------


def test_scan_reads_tracked_files_not_only_docs(git_repo, write_file):
    # A fixture, not a declared public doc: internal-refs would miss it.
    write_file(git_repo, "tests/fixtures/sample.txt", "see drafts/ for the plan")
    _add(git_repo, "tests/fixtures/sample.txt")
    config, scan = _committed(git_repo, refs=starter_refs())
    findings, _ = run_scan(config, scan)
    assert any("internal reference to drafts/" in m for m in _messages(findings))


def test_scan_ignores_untracked_files(git_repo, write_file):
    write_file(git_repo, "leak.txt", "drafts/ mentioned here")
    config, scan = _committed(git_repo, refs=starter_refs())
    findings, _ = run_scan(config, scan)
    assert findings == []


def test_scan_notices_when_not_a_git_repo(tmp_path):
    findings, notices = run_scan(Config(root=tmp_path), default_scan_config())
    assert findings == []
    assert any("not a git repo" in m for m in _messages(notices))


# --------------------------------------------------------------------------
# The published starter patterns
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,fires",
    [
        ("@acme-drafts/types", False),
        ("node_modules/@acme-drafts/types/x", False),
        ("some-drafts/src", False),
        ("see drafts/notes.md", True),
        ("`drafts/` folder", True),
        ("(drafts/plan.md)", True),
        ("./drafts/notes", True),
        ("repo/drafts/x", True),
        ("my_drafts/x", False),
    ],
)
def test_starter_folder_pattern(line, fires):
    ref = next(r for r in starter_refs() if r.label == "drafts/")
    import re

    assert bool(re.search(ref.pattern, line)) is fires


# --------------------------------------------------------------------------
# Committed vs private patterns
# --------------------------------------------------------------------------


def test_committed_ref_label_prints_in_full(git_repo, write_file):
    write_file(git_repo, "notes.txt", "ticket ACME-7 here")
    _add(git_repo, "notes.txt")
    config, scan = _committed(git_repo, refs=[RefPattern("ACME ticket", r"ACME-\d+")])
    findings, _ = run_scan(config, scan)
    assert _messages(findings) == ["internal reference to ACME ticket"]


def test_private_ref_label_is_masked(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "drafts in internal-plans/q3")
    findings, _ = run_scan(config, scan)
    assert _messages(findings) == ["internal reference (p*******)"]


def test_shiplock_toml_never_flags_its_own_declarations(git_repo, write_file):
    write_file(
        git_repo,
        "shiplock.toml",
        '[scan]\nrefs = [{ label = "ACME-7", pattern = "ACME-7" }]\n',
    )
    write_file(git_repo, "notes.txt", "see the ACME-7")
    _add(git_repo, "shiplock.toml", "notes.txt")
    config, scan = _committed(git_repo, refs=[RefPattern("ACME-7", "ACME-7")])
    findings, _ = run_scan(config, scan)
    assert [f.path for f in findings] == ["notes.txt"]


def test_scan_exempts_a_configured_blocklist_path_from_refs(git_repo, write_file):
    write_file(git_repo, "conf.txt", 'blocklist = ["~/.claude/shiplock-rules.toml"]')
    _add(git_repo, "conf.txt")
    config, scan = _committed(
        git_repo, blocklist=["~/.claude/shiplock-rules.toml"], refs=starter_refs()
    )
    findings, _ = run_scan(config, scan)
    assert findings == []


def test_scan_still_flags_outside_the_blocklist_path(git_repo, write_file):
    write_file(git_repo, "conf.txt", "see the .claude directory for tooling")
    _add(git_repo, "conf.txt")
    config, scan = _committed(
        git_repo, blocklist=["~/.claude/shiplock-rules.toml"], refs=starter_refs()
    )
    findings, _ = run_scan(config, scan)
    assert any("internal reference to .claude" in m for m in _messages(findings))


# --------------------------------------------------------------------------
# Identity classes, from a rules file
# --------------------------------------------------------------------------


def test_scan_fires_on_code_name(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "ship the bluebird build")
    findings, _ = run_scan(config, scan)
    assert any(m.startswith("internal code-name") for m in _messages(findings))


def test_scan_fires_on_home_path(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "built under /Users/ada/dev")
    findings, _ = run_scan(config, scan)
    assert any(m.startswith("home path for user") for m in _messages(findings))


def test_scan_fires_on_personal_email(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "reach person@example.com")
    findings, _ = run_scan(config, scan)
    assert any(m.startswith("personal email") for m in _messages(findings))


def test_scan_masks_the_matched_string(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "ship the bluebird build")
    findings, _ = run_scan(config, scan)
    code = next(f for f in findings if f.message.startswith("internal code-name"))
    assert "bluebird" not in code.message
    assert "b*******" in code.message


def test_scan_notices_when_no_identities_are_declared(git_repo, write_file):
    write_file(git_repo, "notes.txt", "ship the bluebird build under /Users/ada")
    _add(git_repo, "notes.txt")
    config, scan = _committed(git_repo, refs=starter_refs())
    findings, notices = run_scan(config, scan)
    assert findings == []
    assert any("identity scan was skipped" in m for m in _messages(notices))


def test_rules_paths_from_the_command_line_are_read(git_repo, write_file, tmp_path_factory):
    outside = tmp_path_factory.mktemp("ci") / "rules.toml"
    outside.write_text(RULES, encoding="utf-8")
    write_file(git_repo, "notes.txt", "ship the bluebird build")
    _add(git_repo, "notes.txt")
    config, scan = _committed(git_repo)
    config = dataclasses.replace(config, rules_paths=(str(outside),))
    findings, _ = run_scan(config, scan)
    assert any(m.startswith("internal code-name") for m in _messages(findings))


# --------------------------------------------------------------------------
# Allow
# --------------------------------------------------------------------------


def test_allowed_code_name_never_fails_and_reports_a_masked_count(git_repo, write_file):
    config, scan = _with_rules(
        git_repo, write_file, "bluebird here\nand bluebird again", RULES + 'allow = ["bluebird"]\n'
    )
    findings, notices = run_scan(config, scan)
    assert findings == []
    allowed = [m for m in _messages(notices) if m.startswith("allowed")]
    assert allowed == ["allowed code-name b*******: 2 matches in notes.txt"]


def test_allowed_ref_label_is_counted_not_reported(git_repo, write_file):
    config, scan = _with_rules(
        git_repo, write_file, "drafts in internal-plans/q3", RULES + 'allow = ["plan dir"]\n'
    )
    findings, notices = run_scan(config, scan)
    assert findings == []
    assert any(m.startswith("allowed reference p*******: 1 match") for m in _messages(notices))


def test_allow_entry_that_matched_nothing_prints_nothing(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "clean", RULES + 'allow = ["bluebird"]\n')
    _, notices = run_scan(config, scan)
    assert not any(m.startswith("allowed") for m in _messages(notices))


# --------------------------------------------------------------------------
# Rules-file safety and problems
# --------------------------------------------------------------------------


def test_scan_refuses_a_tracked_rules_file(git_repo, write_file):
    write_file(git_repo, "shiplock.local.toml", 'code_names = ["bluebird"]\n')
    write_file(git_repo, "notes.txt", "ship the bluebird build")
    _add(git_repo, "shiplock.local.toml", "notes.txt")
    findings, _ = run_scan(Config(root=git_repo, scan=ScanConfig()), ScanConfig())
    assert any("is git-tracked" in m for m in _messages(findings))
    # A refused rules file doesn't load, so the code-name isn't scanned for.
    assert not any(m.startswith("internal code-name") for m in _messages(findings))


def test_scan_notices_a_missing_named_rules_file(git_repo, write_file):
    write_file(git_repo, "notes.txt", "nothing to see")
    _add(git_repo, "notes.txt")
    config, scan = _committed(git_repo, blocklist=["nope.toml"])
    _, notices = run_scan(config, scan)
    assert any("nope.toml not found" in m for m in _messages(notices))


def test_missing_default_rules_file_is_silent(git_repo, write_file):
    write_file(git_repo, "notes.txt", "nothing to see")
    _add(git_repo, "notes.txt")
    _, notices = run_scan(Config(root=git_repo), default_scan_config())
    assert not any("not found" in m for m in _messages(notices))


def test_rules_file_problems_are_notices_not_errors(git_repo, write_file):
    bad = 'refs = [{ label = "x", pattern = "([" }]\nsurprise = 1\ncode_names = ["bluebird"]\n'
    config, scan = _with_rules(git_repo, write_file, "ship the bluebird build", bad)
    findings, notices = run_scan(config, scan)
    text = " ".join(_messages(notices))
    assert "not a valid regex" in text and "unknown key(s) surprise" in text
    # The rest of the file still loads.
    assert any(m.startswith("internal code-name") for m in _messages(findings))


# --------------------------------------------------------------------------
# Secrets switch (opt-in)
# --------------------------------------------------------------------------


def test_scan_skips_secrets_by_default(git_repo, write_file):
    write_file(git_repo, "conf.txt", "aws = AKIAABCDEFGHIJKLMNOP")
    _add(git_repo, "conf.txt")
    config, scan = _committed(git_repo, refs=starter_refs())
    findings, _ = run_scan(config, scan)
    assert not any("possible secret" in m for m in _messages(findings))


def test_scan_fires_on_secrets_when_enabled(git_repo, write_file):
    write_file(git_repo, "conf.txt", "aws = AKIAABCDEFGHIJKLMNOP")
    _add(git_repo, "conf.txt")
    config, scan = _committed(git_repo, secrets=True)
    findings, _ = run_scan(config, scan)
    secret = next((f for f in findings if "possible secret" in f.message), None)
    assert secret is not None
    assert "AKIAABCDEFGHIJKLMNOP" not in secret.message  # masked


# --------------------------------------------------------------------------
# Excludes and binary files
# --------------------------------------------------------------------------


def test_scan_house_excludes_gitignore(git_repo, write_file):
    # A .gitignore naming a private dir is the prevention, not a leak.
    write_file(git_repo, ".gitignore", "drafts/\n.claude/\n")
    _add(git_repo, ".gitignore")
    config, scan = _committed(git_repo, refs=starter_refs())
    findings, _ = run_scan(config, scan)
    assert findings == []


def test_scan_honours_configured_exclude(git_repo, write_file):
    write_file(git_repo, "vendor/thirdparty.txt", "references drafts/ internally")
    _add(git_repo, "vendor/thirdparty.txt")
    config, scan = _committed(git_repo, refs=starter_refs(), exclude=["vendor/**"])
    findings, _ = run_scan(config, scan)
    assert findings == []


def test_scan_skips_binary_files(git_repo):
    (git_repo / "blob.bin").write_bytes(b"drafts/\x00\x01\x02binary")
    _add(git_repo, "blob.bin")
    config, scan = _committed(git_repo, refs=starter_refs())
    findings, _ = run_scan(config, scan)
    assert findings == []


# --------------------------------------------------------------------------
# internal-refs reads the same rules
# --------------------------------------------------------------------------


def test_internal_refs_reads_a_rules_file_and_masks_its_labels(git_repo, write_file):
    config, _ = _with_rules(git_repo, write_file, "unused", name="unused.txt")
    write_file(git_repo, "README.md", "drafts in internal-plans/q3")
    config = dataclasses.replace(config, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_internal_refs(config)
    assert _messages(findings) == ["internal reference (p*******)"]


def test_internal_refs_honours_allow(git_repo, write_file):
    config, _ = _with_rules(
        git_repo, write_file, "unused", RULES + 'allow = ["plan dir"]\n', name="unused.txt"
    )
    write_file(git_repo, "README.md", "drafts in internal-plans/q3")
    config = dataclasses.replace(config, docs=DocsConfig(public=["README.md"]))
    findings, notices = check_internal_refs(config)
    assert findings == []
    assert any(m.startswith("allowed reference") for m in _messages(notices))


# --------------------------------------------------------------------------
# The scan check wrapper, git_tracked_files, mask, strip_exempt
# --------------------------------------------------------------------------


def test_check_scan_skips_without_section_or_rules(git_repo):
    findings, notices = check_scan(Config(root=git_repo))
    assert findings == []
    assert any("no rules declared" in m for m in _messages(notices))


def test_check_scan_runs_without_section_when_a_rules_file_exists(git_repo, write_file):
    # DQ2: a rules file alone is enough; no [scan] section needed.
    config, _ = _with_rules(git_repo, write_file, "ship the bluebird build")
    findings, _ = check_scan(dataclasses.replace(config, scan=None))
    assert any("internal code-name" in m for m in _messages(findings))


def test_check_scan_runs_with_section(git_repo, write_file):
    write_file(git_repo, "doc.txt", "see drafts/ plan")
    _add(git_repo, "doc.txt")
    config, _ = _committed(git_repo, refs=starter_refs())
    findings, _ = check_scan(config)
    assert any("internal reference to drafts/" in m for m in _messages(findings))


def test_git_tracked_files_lists_staged(git_repo, write_file):
    write_file(git_repo, "a.txt", "x")
    write_file(git_repo, "b.txt", "y")
    _add(git_repo, "a.txt")
    assert git_tracked_files(git_repo) == ["a.txt"]


def test_git_tracked_files_none_outside_repo(tmp_path):
    assert git_tracked_files(tmp_path) is None


@pytest.mark.parametrize(
    "value,expected",
    [("bluebird", "b*******"), ("ab", "a*"), ("x", "x"), ("", "")],
)
def test_mask(value, expected):
    assert mask(value) == expected


def test_strip_exempt_blanks_only_the_exempt_substring():
    line = "a ~/.claude/x and a .codex ref"
    out = strip_exempt(line, ("~/.claude/x",))
    assert "~/.claude/x" not in out
    assert ".codex" in out
    assert len(out) == len(line)


# --------------------------------------------------------------------------
# The user's own rules file
# --------------------------------------------------------------------------


def _user_file(monkeypatch, tmp_path, text: str, name="xdg") -> Path:
    home = tmp_path / name
    (home / "shiplock").mkdir(parents=True)
    (home / "shiplock" / "rules.toml").write_text(text, encoding="utf-8")
    monkeypatch.delenv("SHIPLOCK_NO_USER_RULES", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home / "shiplock" / "rules.toml"


def test_user_rules_file_is_read_in_any_repo(git_repo, write_file, monkeypatch, tmp_path):
    _user_file(monkeypatch, tmp_path, 'code_names = ["bluebird"]\n')
    write_file(git_repo, "notes.txt", "ship the bluebird build")
    _add(git_repo, "notes.txt")
    findings, notices = run_scan(Config(root=git_repo), default_scan_config())
    assert any("internal code-name (b*******)" in m for m in _messages(findings))
    info = [n for n in notices if n.kind == "info"]
    assert len(info) == 1
    assert info[0].message.startswith("1 rule(s) from the user rules file ")


def test_user_rules_file_adds_to_the_repo_rules(git_repo, write_file, monkeypatch, tmp_path):
    _user_file(monkeypatch, tmp_path, 'emails = ["person@example.com"]\n')
    config, scan = _with_rules(git_repo, write_file, "bluebird by person@example.com")
    findings, _ = run_scan(config, scan)
    messages = _messages(findings)
    assert any("internal code-name" in m for m in messages)
    assert any("personal email" in m for m in messages)


def test_user_rules_env_override_wins_and_reports_when_missing(git_repo, write_file, monkeypatch, tmp_path):
    _user_file(monkeypatch, tmp_path, 'code_names = ["bluebird"]\n')
    named = tmp_path / "elsewhere.toml"
    named.write_text('code_names = ["kestrel"]\n', encoding="utf-8")
    monkeypatch.setenv("SHIPLOCK_USER_RULES", str(named))
    write_file(git_repo, "notes.txt", "bluebird and kestrel")
    _add(git_repo, "notes.txt")
    findings, _ = run_scan(Config(root=git_repo), default_scan_config())
    assert _messages(findings) == ["internal code-name (k******)"]

    named.unlink()
    _, notices = run_scan(Config(root=git_repo), default_scan_config())
    assert any("not found" in m for m in _messages(notices))


def test_user_rules_file_absent_by_default_is_silent(git_repo, monkeypatch, tmp_path):
    monkeypatch.delenv("SHIPLOCK_NO_USER_RULES", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    _, notices = run_scan(Config(root=git_repo), default_scan_config())
    assert not any("not found" in m for m in _messages(notices))


def test_no_user_rules_env_skips_the_user_file(git_repo, write_file, monkeypatch, tmp_path):
    _user_file(monkeypatch, tmp_path, 'code_names = ["bluebird"]\n')
    monkeypatch.setenv("SHIPLOCK_NO_USER_RULES", "1")
    write_file(git_repo, "notes.txt", "ship the bluebird build")
    _add(git_repo, "notes.txt")
    findings, notices = run_scan(Config(root=git_repo), default_scan_config())
    assert findings == []
    assert any("no rules declared" in m for m in _messages(notices))


# --------------------------------------------------------------------------
# Scoped allow entries (DQ8)
# --------------------------------------------------------------------------

SCOPED = RULES + 'allow = [{ value = "person@example.com", paths = ["README.md", "docs/"] }]\n'


def test_scoped_allow_inside_its_paths_is_counted(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "mail person@example.com", SCOPED, name="README.md")
    findings, notices = run_scan(config, scan)
    assert findings == []
    expected = f"allowed email {mask('person@example.com')}: 1 match in README.md"
    assert any(m.startswith(expected) for m in _messages(notices))


def test_scoped_allow_folder_shorthand_covers_nested_files(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "mail person@example.com", SCOPED, name="docs/deep/contact.md")
    findings, _ = run_scan(config, scan)
    assert findings == []


def test_scoped_allow_outside_its_paths_is_a_finding_naming_the_paths(git_repo, write_file):
    config, scan = _with_rules(git_repo, write_file, "mail person@example.com", SCOPED, name="notes.txt")
    findings, _ = run_scan(config, scan)
    assert _messages(findings) == [
        f"personal email ({mask('person@example.com')}): allowed only in README.md, docs/*"
    ]


def test_scoped_allow_applies_to_ref_labels_and_code_names(git_repo, write_file):
    rules = RULES + (
        'allow = [{ value = "plan dir", paths = ["README.md"] },'
        ' { value = "bluebird", paths = ["README.md"] }]\n'
    )
    config, scan = _with_rules(git_repo, write_file, "bluebird in internal-plans/", rules, name="notes.txt")
    findings, _ = run_scan(config, scan)
    messages = _messages(findings)
    assert "internal reference (p*******): allowed only in README.md" in messages
    assert "internal code-name (b*******): allowed only in README.md" in messages


def test_bare_allow_entry_overrides_a_scoped_one(git_repo, write_file):
    rules = RULES + 'allow = [{ value = "bluebird", paths = ["README.md"] }, "bluebird"]\n'
    config, scan = _with_rules(git_repo, write_file, "ship the bluebird build", rules, name="notes.txt")
    findings, _ = run_scan(config, scan)
    assert findings == []


def test_two_scoped_entries_for_one_value_combine_their_paths(git_repo, write_file):
    rules = RULES + (
        'allow = [{ value = "bluebird", paths = ["README.md"] },'
        ' { value = "bluebird", paths = ["notes.txt"] }]\n'
    )
    config, scan = _with_rules(git_repo, write_file, "ship the bluebird build", rules, name="notes.txt")
    findings, _ = run_scan(config, scan)
    assert findings == []


def test_scoped_allow_glob_matching_no_tracked_file_is_a_warning(git_repo, write_file):
    rules = RULES + 'allow = [{ value = "bluebird", paths = ["gone.md"] }]\n'
    config, scan = _with_rules(git_repo, write_file, "nothing here", rules)
    _, notices = run_scan(config, scan)
    stale = [n for n in notices if "matches no tracked file" in n.message]
    assert len(stale) == 1
    assert stale[0].kind == "warning"
    assert "b*******" in stale[0].message and "gone.md" in stale[0].message


def test_malformed_allow_entries_are_notices_and_skipped(git_repo, write_file):
    rules = RULES + 'allow = [{ paths = ["README.md"] }, { value = "bluebird", paths = [] }, 7]\n'
    config, scan = _with_rules(git_repo, write_file, "ship the bluebird build", rules)
    findings, notices = run_scan(config, scan)
    assert any("internal code-name" in m for m in _messages(findings))
    problems = [m for m in _messages(notices) if "allow:" in m]
    assert len(problems) == 3
    assert not any("bluebird" in m for m in problems)


def test_internal_refs_honours_a_scoped_allow(git_repo, write_file):
    write_file(git_repo, ".gitignore", "shiplock.local.toml\n")
    write_file(git_repo, "shiplock.local.toml", RULES + 'allow = [{ value = "plan dir", paths = ["USAGE.md"] }]\n')
    write_file(git_repo, "USAGE.md", "see internal-plans/\n")
    write_file(git_repo, "README.md", "see internal-plans/\n")
    _add(git_repo, ".gitignore", "USAGE.md", "README.md")
    config = Config(root=git_repo, docs=DocsConfig(public=["USAGE.md", "README.md"]))
    findings, notices = check_internal_refs(config)
    assert [f.path for f in findings] == ["README.md"]
    assert "allowed only in USAGE.md" in findings[0].message
    assert any("allowed reference p*******: 1 match in USAGE.md" in m for m in _messages(notices))


# --------------------------------------------------------------------------
# Range and staged scans (DQ10, DQ11)
# --------------------------------------------------------------------------


def _commit(root, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _rules_repo(git_repo, write_file, rules: str = RULES):
    write_file(git_repo, ".gitignore", "shiplock.local.toml\n")
    write_file(git_repo, "shiplock.local.toml", rules)
    _add(git_repo, ".gitignore")
    base = _commit(git_repo, "base")
    return Config(root=git_repo, scan=ScanConfig()), ScanConfig(), base


def test_range_scan_finds_a_leak_added_then_removed_within_the_range(git_repo, write_file):
    config, scan, base = _rules_repo(git_repo, write_file)
    write_file(git_repo, "notes.txt", "ship the bluebird build\n")
    _add(git_repo, "notes.txt")
    _commit(git_repo, "add notes")
    write_file(git_repo, "notes.txt", "ship the build\n")
    _add(git_repo, "notes.txt")
    _commit(git_repo, "clean notes")

    head_findings, _ = run_scan(config, scan)
    assert head_findings == []
    findings, _ = run_scan_range(config, scan, f"{base}..HEAD")
    assert [(f.path, f.line, f.message) for f in findings] == [
        ("notes.txt", 1, "internal code-name (b*******)")
    ]


def test_range_scan_reads_commit_messages(git_repo, write_file):
    config, scan, base = _rules_repo(git_repo, write_file)
    write_file(git_repo, "notes.txt", "clean\n")
    _add(git_repo, "notes.txt")
    sha = _commit(git_repo, "wire up bluebird")
    findings, _ = run_scan_range(config, scan, f"{base}..HEAD")
    assert [(f.path, f.line, f.message) for f in findings] == [
        (f"commit {sha[:7]}", None, "internal code-name (b*******)")
    ]


def test_range_scan_reports_the_line_number_in_the_adding_commit(git_repo, write_file):
    config, scan, base = _rules_repo(git_repo, write_file)
    write_file(git_repo, "notes.txt", "one\ntwo\nthree\n")
    _add(git_repo, "notes.txt")
    _commit(git_repo, "three lines")
    write_file(git_repo, "notes.txt", "one\ntwo\nbluebird\nthree\n")
    _add(git_repo, "notes.txt")
    _commit(git_repo, "insert")
    findings, _ = run_scan_range(config, scan, f"{base}..HEAD")
    assert [(f.path, f.line) for f in findings] == [("notes.txt", 3)]


def test_range_scan_honours_excludes_and_allow(git_repo, write_file):
    config, scan, base = _rules_repo(git_repo, write_file, RULES + 'allow = ["ada"]\n')
    scan = ScanConfig(exclude=["tests/**"])
    write_file(git_repo, "tests/fixture.txt", "bluebird\n")
    write_file(git_repo, "notes.txt", "/Users/ada/dev\n")
    _add(git_repo, "tests/fixture.txt", "notes.txt")
    _commit(git_repo, "fixtures")
    findings, notices = run_scan_range(config, scan, f"{base}..HEAD")
    assert findings == []
    assert any(m.startswith("allowed home user a**: 1 match in notes.txt") for m in _messages(notices))


def test_range_scan_takes_a_multi_word_rev_list_expression(git_repo, write_file):
    # What the pre-push hook passes for a branch the remote doesn't have yet.
    config, scan, _ = _rules_repo(git_repo, write_file)
    write_file(git_repo, "notes.txt", "bluebird\n")
    _add(git_repo, "notes.txt")
    _commit(git_repo, "add")
    findings, _ = run_scan_range(config, scan, "HEAD --not --remotes")
    assert [f.path for f in findings] == ["notes.txt"]


def test_range_scan_with_an_unresolvable_range_is_a_notice(git_repo, write_file):
    config, scan, _ = _rules_repo(git_repo, write_file)
    findings, notices = run_scan_range(config, scan, "nope..HEAD")
    assert findings == []
    assert any("isn't a revision range git resolves" in m for m in _messages(notices))


def test_range_scan_with_no_commits_says_so(git_repo, write_file):
    config, scan, base = _rules_repo(git_repo, write_file)
    findings, notices = run_scan_range(config, scan, f"{base}..HEAD")
    assert findings == []
    assert any("holds no commits" in m for m in _messages(notices))


def test_range_scan_skips_with_no_rules(git_repo, write_file):
    write_file(git_repo, "a.txt", "x\n")
    _add(git_repo, "a.txt")
    base = _commit(git_repo, "base")
    findings, notices = run_scan_range(Config(root=git_repo), default_scan_config(), f"{base}..HEAD")
    assert findings == []
    assert any("no rules declared" in m for m in _messages(notices))


def test_staged_scan_reads_only_the_staged_added_lines(git_repo, write_file):
    config, scan, _ = _rules_repo(git_repo, write_file)
    write_file(git_repo, "old.txt", "bluebird was here\n")
    _add(git_repo, "old.txt")
    _commit(git_repo, "already committed")
    write_file(git_repo, "new.txt", "clean\nbluebird\n")
    write_file(git_repo, "unstaged.txt", "bluebird\n")
    _add(git_repo, "new.txt")
    findings, _ = run_scan_staged(config, scan)
    assert [(f.path, f.line, f.message) for f in findings] == [
        ("new.txt", 2, "internal code-name (b*******)")
    ]


def test_staged_scan_with_nothing_staged_is_clean(git_repo, write_file):
    config, scan, _ = _rules_repo(git_repo, write_file)
    findings, _ = run_scan_staged(config, scan)
    assert findings == []


@pytest.mark.parametrize(
    "diff,expected",
    [
        (
            "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,3 @@\n one\n+two\n three\n",
            [("f.txt", 2, "two")],
        ),
        (
            "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -3 +3,2 @@\n-gone\n+new a\n+new b\n",
            [("f.txt", 3, "new a"), ("f.txt", 4, "new b")],
        ),
        (
            "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n",
            [],
        ),
        (
            'diff --git "a/sp ace.txt" "b/sp ace.txt"\n--- "a/sp ace.txt"\n+++ "b/sp ace.txt"\n@@ -0,0 +1 @@\n+hello\n',
            [("sp ace.txt", 1, "hello")],
        ),
        ("diff --git a/x.png b/x.png\nBinary files a/x.png and b/x.png differ\n", []),
    ],
)
def test_added_lines_parses_unified_diffs(diff, expected):
    from shiplock._scan import _added_lines

    assert list(_added_lines(diff)) == expected
