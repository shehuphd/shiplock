"""The rules-file editor: the writer, the pattern builders, and the merge."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shiplock._compat import tomllib
from shiplock._rules import (
    HEADER,
    RulesFile,
    RulesFileError,
    add_rule,
    allow_entry,
    file_rule,
    folder_rule,
    has_foreign_comments,
    load_rules_file,
    merge,
    push_secret,
    remove_rule,
    render,
    save,
)


def _roundtrip(rules: RulesFile) -> dict:
    return tomllib.loads(render(rules))


def test_render_parses_back_to_the_same_rules(tmp_path):
    rules = RulesFile(
        path=tmp_path / "r.toml",
        refs=[("drafts/", r"(?<![\w@.-])drafts/"), ("it's", r"it's\d+")],
        code_names=["bluebird"],
        home_usernames=["ada"],
        emails=["ada@personal.example"],
        allow=[("bluebird", ()), ("ada@personal.example", ("README.md", "docs/*"))],
    )
    data = _roundtrip(rules)
    assert data["refs"] == [
        {"label": "drafts/", "pattern": r"(?<![\w@.-])drafts/"},
        {"label": "it's", "pattern": r"it's\d+"},
    ]
    assert data["code_names"] == ["bluebird"]
    assert data["home_usernames"] == ["ada"]
    assert data["emails"] == ["ada@personal.example"]
    assert data["allow"] == ["bluebird", {"value": "ada@personal.example", "paths": ["README.md", "docs/*"]}]


def test_render_starts_with_the_header_and_omits_empty_keys(tmp_path):
    text = render(RulesFile(path=tmp_path / "r.toml", code_names=["x"]))
    assert text.startswith(HEADER)
    assert "refs" not in text.split("\n", 2)[2]
    assert "allow" not in text.split("\n", 2)[2]


def test_load_reads_what_render_wrote(tmp_path):
    rules = RulesFile(path=tmp_path / "r.toml", code_names=["bluebird"], allow=[("x", ("a/*",))])
    save(rules)
    loaded = load_rules_file(rules.path)
    assert loaded.code_names == ["bluebird"]
    assert loaded.allow == [("x", ("a/*",))]


def test_load_missing_file_is_empty(tmp_path):
    assert load_rules_file(tmp_path / "none.toml").is_empty()


def test_load_rejects_bad_toml_and_bad_shapes(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text("code_names = [1]\n", encoding="utf-8")
    with pytest.raises(RulesFileError):
        load_rules_file(path)
    path.write_text("not toml [\n", encoding="utf-8")
    with pytest.raises(RulesFileError):
        load_rules_file(path)


@pytest.mark.parametrize(
    "text,foreign",
    [
        (HEADER + 'code_names = ["x"]\n', False),
        (HEADER + "# mine\ncode_names = [\"x\"]\n", True),
        (HEADER + 'code_names = ["x"]  # why\n', True),
        (HEADER + 'code_names = ["x#y"]\n', False),
        ("refs = [{ label = 'a#b', pattern = 'c' }]\n", False),
    ],
)
def test_has_foreign_comments(text, foreign):
    assert has_foreign_comments(text) is foreign


def test_save_refuses_to_drop_a_persons_comments_unless_rewrite(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text('# keep this\ncode_names = ["x"]\n', encoding="utf-8")
    rules = load_rules_file(path)
    rules.code_names.append("y")
    with pytest.raises(RulesFileError, match="--rewrite"):
        save(rules)
    assert "keep this" in path.read_text(encoding="utf-8")
    save(rules, rewrite=True)
    assert "keep this" not in path.read_text(encoding="utf-8")
    assert load_rules_file(path).code_names == ["x", "y"]


def test_save_rewrites_its_own_file_without_complaint(tmp_path):
    rules = RulesFile(path=tmp_path / "r.toml", code_names=["x"])
    save(rules)
    rules.code_names.append("y")
    save(rules)
    assert load_rules_file(rules.path).code_names == ["x", "y"]


# Pattern builders


@pytest.mark.parametrize(
    "name,label,fires,quiet",
    [
        ("drafts", "drafts/", ["see drafts/", "./drafts/notes", "repo/drafts/x"], ["@acme-drafts/x", "my_drafts/", "some-drafts/"]),
        ("./drafts/", "drafts/", ["drafts/"], ["drafts"]),
        (".notes", ".notes", ["in .notes/", "the .notes dir"], ["platform.notes.com", "my.notes"]),
        ("docs/internal", "docs/internal/", ["see docs/internal/x"], ["internal/x", "docs/internals/"]),
    ],
)
def test_folder_rule(name, label, fires, quiet):
    got_label, pattern = folder_rule(name)
    assert got_label == label
    for line in fires:
        assert re.search(pattern, line), line
    for line in quiet:
        assert not re.search(pattern, line), line


@pytest.mark.parametrize(
    "name,fires,quiet",
    [
        ("NOTES.md", ["see NOTES.md", "(NOTES.md)"], ["docs/NOTES.md", "my-NOTES.md", "NOTES.mdx", "x.NOTES.md"]),
        ("Makefile.local", ["Makefile.local here"], ["Makefile.local2"]),
    ],
)
def test_file_rule(name, fires, quiet):
    label, pattern = file_rule(name)
    assert label == name
    for line in fires:
        assert re.search(pattern, line), line
    for line in quiet:
        assert not re.search(pattern, line), line


def test_builders_reject_empty_names():
    with pytest.raises(RulesFileError):
        folder_rule("./")
    with pytest.raises(RulesFileError):
        file_rule("dir/")


# Edits


def test_add_rule_each_kind_and_dedup(tmp_path):
    rules = RulesFile(path=tmp_path / "r.toml")
    assert add_rule(rules, "code-name", "bluebird") == "code-name b*******"
    add_rule(rules, "code-name", "Bluebird")
    assert rules.code_names == ["bluebird"]
    add_rule(rules, "email", "ada@personal.example")
    add_rule(rules, "username", "ada")
    add_rule(rules, "folder", "drafts")
    add_rule(rules, "file", "NOTES.md")
    add_rule(rules, "ref", "ticket", pattern=r"ACME-\d+")
    assert [l for l, _ in rules.refs] == ["drafts/", "NOTES.md", "ticket"]
    assert rules.emails == ["ada@personal.example"] and rules.home_usernames == ["ada"]


def test_add_ref_needs_a_valid_pattern(tmp_path):
    rules = RulesFile(path=tmp_path / "r.toml")
    with pytest.raises(RulesFileError, match="--pattern"):
        add_rule(rules, "ref", "x")
    with pytest.raises(RulesFileError, match="valid regex"):
        add_rule(rules, "ref", "x", pattern="(")


def test_allow_entry_merges_paths_and_bare_wins(tmp_path):
    rules = RulesFile(path=tmp_path / "r.toml")
    assert allow_entry(rules, "xy", ["README.md"]) == "allow x* in README.md"
    rules.allow = []
    allow_entry(rules, "x", ["README.md"])
    allow_entry(rules, "X", ["docs/"])
    assert rules.allow == [("x", ("README.md", "docs/"))]
    allow_entry(rules, "x", [])
    assert rules.allow == [("x", ())]


def test_remove_rule_by_kind(tmp_path):
    rules = RulesFile(path=tmp_path / "r.toml")
    add_rule(rules, "code-name", "bluebird")
    add_rule(rules, "folder", "drafts")
    allow_entry(rules, "y", [])
    assert remove_rule(rules, "code-name", "BLUEBIRD") is True
    assert remove_rule(rules, "code-name", "bluebird") is False
    assert remove_rule(rules, "folder", "drafts/") is True
    assert remove_rule(rules, "allow", "y") is True
    assert rules.is_empty()


# Merge and the secret


def test_merge_dedups_and_refuses_conflicting_refs(tmp_path):
    a = RulesFile(path=tmp_path / "a", refs=[("t", "a")], code_names=["x"], allow=[("q", ("a/*",))])
    b = RulesFile(path=tmp_path / "b", refs=[("t", "a")], code_names=["X", "y"], allow=[("q", ("b/*",))])
    merged = merge([a, b])
    assert merged.refs == [("t", "a")]
    assert merged.code_names == ["x", "y"]
    assert merged.allow == [("q", ("a/*", "b/*"))]
    c = RulesFile(path=tmp_path / "c", refs=[("t", "different")])
    with pytest.raises(RulesFileError, match="two different patterns"):
        merge([a, c])


def test_push_secret_sends_the_document_on_stdin_only(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\nprintf "argv:%s\\n" "$*" > "{log}"\ncat >> "{log}"\n', encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    assert push_secret(tmp_path, 'code_names = ["bluebird"]\n') == ""
    logged = log.read_text(encoding="utf-8")
    assert logged.startswith("argv:secret set SHIPLOCK_RULES\n")
    assert "bluebird" not in logged.splitlines()[0]
    assert 'code_names = ["bluebird"]' in logged


def test_push_secret_without_gh_is_a_sentence(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert "gh CLI" in push_secret(tmp_path, "x = []\n")


def test_push_secret_failure_reports_ghs_last_line(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\necho "not logged in" >&2\nexit 4\n', encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    assert push_secret(tmp_path, "x = []\n") == "gh couldn't set the secret: not logged in"
