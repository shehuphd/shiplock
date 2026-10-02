"""``shiplock rules suggest``: ignored folders and named files, minus tool output."""

from __future__ import annotations

import subprocess

from shiplock._config import Config
from shiplock._suggest import Suggestion, named_ignored_files, render, suggest


def _add(root, *paths):
    subprocess.run(["git", "-C", str(root), "add", *paths], check=True, capture_output=True)


def _repo(git_repo, write_file, gitignore: str, tracked: dict[str, str], ignored_dirs=()):
    write_file(git_repo, ".gitignore", gitignore)
    for rel, text in tracked.items():
        write_file(git_repo, rel, text)
    for d in ignored_dirs:
        write_file(git_repo, f"{d}/keep", "x")
    _add(git_repo, ".gitignore", *tracked)
    return Config(root=git_repo)


def test_suggests_ignored_folders_with_mention_counts(git_repo, write_file):
    config = _repo(
        git_repo, write_file,
        "drafts/\nnode_modules/\n.venv/\n",
        {"README.md": "see drafts/ for plans\n", "docs/a.md": "drafts/ again\n", "x.txt": "nothing\n"},
        ignored_dirs=("drafts", "node_modules", ".venv"),
    )
    got = suggest(config)
    assert got == [Suggestion("folder", "drafts", 2, ("README.md", "docs/a.md"))]


def test_suggests_files_named_outright_but_not_globs_or_tool_files(git_repo, write_file):
    config = _repo(
        git_repo, write_file,
        "NOTES.md\n*.log\n.env\n/PRIVATE.txt\nshiplock.local.toml\n",
        {"README.md": "read NOTES.md\n"},
    )
    got = suggest(config)
    assert [(s.kind, s.name, s.mentions) for s in got] == [("file", "NOTES.md", 1), ("file", "PRIVATE.txt", 0)]


def test_named_ignored_files_reads_info_exclude_too(git_repo, write_file):
    write_file(git_repo, ".git/info/exclude", "SECRET.md\n")
    write_file(git_repo, ".gitignore", "# comment\n!keep.md\nA.md\n")
    assert named_ignored_files(git_repo) == ["A.md", "SECRET.md"]


def test_covered_folders_are_not_suggested(git_repo, write_file):
    config = _repo(git_repo, write_file, "drafts/\nshiplock.local.toml\n", {"README.md": "x\n"}, ignored_dirs=("drafts",))
    write_file(git_repo, "shiplock.local.toml", "refs = [{ label = 'drafts/', pattern = '(?<![\\\\w@.-])drafts/' }]\n")
    assert suggest(config) == []


def test_nested_ignored_folder_keeps_its_path_and_tool_dirs_are_dropped(git_repo, write_file):
    config = _repo(
        git_repo, write_file,
        "docs/internal/\ndocs/_build/\n",
        {"README.md": "see docs/internal/x\n"},
        ignored_dirs=("docs/internal", "docs/_build"),
    )
    got = suggest(config)
    assert [(s.kind, s.name, s.mentions) for s in got] == [("folder", "docs/internal", 1)]


def test_mentions_in_gitignore_do_not_count(git_repo, write_file):
    config = _repo(git_repo, write_file, "drafts/\n", {"README.md": "x\n"}, ignored_dirs=("drafts",))
    assert suggest(config)[0].mentions == 0


def test_outside_a_git_repo_suggests_nothing(tmp_path):
    assert suggest(Config(root=tmp_path)) == []


def test_render_lists_both_kinds_and_the_add_commands():
    text = render([
        Suggestion("folder", "drafts", 3, ("a", "b", "c", "d")),
        Suggestion("folder", ".notes", 0, ()),
        Suggestion("file", "NOTES.md", 1, ("README.md",)),
    ])
    assert "drafts/" in text and "named in 3 tracked files (a, b, c, ...)" in text
    assert ".notes/" in text and "not named anywhere" in text
    assert "NOTES.md" in text and "named in 1 tracked file (README.md)" in text
    assert "shiplock rules add folder drafts/ .notes/" in text
    assert "shiplock rules add file NOTES.md" in text
    assert render([]) == "No ignored folders or named files need a rule.\n"
