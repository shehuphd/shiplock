"""Suggested rules from what a repo already keeps out of git.

shiplock ships no folder names. The repo says which folders its owner keeps
private: every directory git ignores. A tracked file that names one of them
is a likely leak, so ``shiplock rules suggest`` lists those folders (and the
files a ``.gitignore`` line names outright), minus tool output, minus
anything an existing rule covers, with how many tracked files name each.
Nothing is written: the user picks, then ``shiplock rules add`` writes.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shiplock._config import CONFIG_FILENAME, Config
from shiplock._rules import file_rule, folder_rule
from shiplock._scan import (
    _HOUSE_EXCLUDES,
    DEFAULT_RULES_FILE,
    _git,
    _read_scan_text,
    git_tracked_files,
    load_rules,
)

# Tool output: built, downloaded, or cached folders that are never private.
# Compiled with GitHub's public .gitignore templates as the reference. A
# false positive costs one suggestion line the user declines, so the list
# stays small rather than complete.
TOOL_DIRS: frozenset[str] = frozenset(
    {
        "node_modules", "bower_components", "jspm_packages", ".pnpm-store",
        ".yarn", ".npm", ".cache", ".parcel-cache", ".next", ".nuxt", ".svelte-kit",
        ".turbo", ".vite", ".angular", "dist", "build", "out", "public/build",
        "target", "bin", "obj", "pkg", "vendor", "_site", "site", ".docusaurus",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
        ".nox", ".venv", "venv", "env", ".env", ".eggs", "htmlcov", ".coverage",
        ".hypothesis", ".ipynb_checkpoints", "wheels", "sdist", ".gradle",
        ".idea", ".vscode", ".vs", ".fleet", ".settings", ".metadata", ".classpath",
        "DerivedData", "Pods", "Carthage", ".dart_tool", ".pub-cache", ".terraform",
        ".serverless", ".aws-sam", "coverage", "logs", "log", "tmp", "temp",
        ".tmp", ".temp", ".sass-cache", ".DS_Store", "cmake-build-debug",
        "cmake-build-release", "CMakeFiles", ".cargo", "_build", "deps",
        ".elixir_ls", ".bundle", ".stack-work", "dist-newstyle", ".test-runs",
    }
)

# Files a .gitignore names outright that hold secrets or machine state, not
# a private document: mentioning them in a README is normal.
TOOL_FILES: frozenset[str] = frozenset(
    {
        ".env", ".env.local", ".envrc", ".DS_Store", "Thumbs.db", "desktop.ini",
        ".python-version", ".node-version", ".nvmrc", ".tool-versions",
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
        "Pipfile.lock", "composer.lock", "Gemfile.lock", "Cargo.lock",
        "coverage.xml", ".coverage", "npm-debug.log", "yarn-error.log",
    }
)


@dataclass(frozen=True)
class Suggestion:
    """One folder or file the repo ignores and no rule covers."""

    kind: str  # "folder" or "file"
    name: str
    mentions: int
    files: tuple[str, ...]


def ignored_dirs(root: Path) -> list[str]:
    """Ignored directories present in the working tree, per git's own rules."""
    proc = _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
    if proc.returncode != 0:
        return []
    listed = [entry.rstrip("/") for entry in proc.stdout.split("\0") if entry.endswith("/")]
    if not listed:
        return []
    # ``--directory`` also collapses a folder whose contents are all ignored,
    # though no rule names the folder itself; keep only the ones a rule names.
    check = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-z", "--stdin", "--no-index"],
        input="\0".join(listed) + "\0",
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode not in (0, 1):
        return listed
    named = {entry.rstrip("/") for entry in check.stdout.split("\0") if entry}
    return [d for d in listed if d in named]


def named_ignored_files(root: Path) -> list[str]:
    """Files a ``.gitignore`` line names outright: no wildcard, no trailing slash."""
    out: list[str] = []
    for source in (root / ".gitignore", root / ".git" / "info" / "exclude"):
        if not source.is_file():
            continue
        for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if any(ch in line for ch in "*?[") or line.endswith("/"):
                continue
            name = line.lstrip("/")
            if name and name not in out:
                out.append(name)
    return out


def _is_tool_dir(path: str) -> bool:
    parts = path.split("/")
    return any(part in TOOL_DIRS for part in parts) or path in TOOL_DIRS


def suggest(config: Config) -> list[Suggestion]:
    """Ignored folders and named files no rule covers, with their mentions."""
    root = config.root
    tracked = git_tracked_files(root)
    if tracked is None:
        return []
    rules, _, _ = load_rules(config, set(tracked), "rules")
    patterns = [ref.pattern for ref in rules.refs]

    candidates: list[tuple[str, str]] = []
    dir_names = set(ignored_dirs(root))
    for name in sorted(dir_names):
        if _is_tool_dir(name):
            continue
        candidates.append(("folder", name))
    own_files = {DEFAULT_RULES_FILE, CONFIG_FILENAME}
    if config.scan:
        own_files.update(Path(b).name for b in config.scan.blocklist)
    for name in named_ignored_files(root):
        if name in dir_names or (root / name).is_dir():
            continue
        if name in TOOL_FILES or name in own_files or _is_tool_dir(name):
            continue
        candidates.append(("file", name))

    texts: dict[str, str] = {}
    for rel in tracked:
        if rel in _HOUSE_EXCLUDES:
            continue
        text = _read_scan_text(root / rel)
        if text is not None:
            texts[rel] = text

    out: list[Suggestion] = []
    for kind, name in candidates:
        label, built = folder_rule(name) if kind == "folder" else file_rule(name)
        probe = label if kind == "folder" else name
        if any(p.search(probe) for p in patterns):
            continue
        built_re = re.compile(built)
        hits = tuple(rel for rel, text in texts.items() if built_re.search(text))
        out.append(Suggestion(kind, name, len(hits), hits))
    return out


def render(suggestions: list[Suggestion]) -> str:
    """The suggestions as the terminal shows them, names in full."""
    if not suggestions:
        return "No ignored folders or named files need a rule.\n"
    lines: list[str] = []
    folders = [s for s in suggestions if s.kind == "folder"]
    files = [s for s in suggestions if s.kind == "file"]
    if folders:
        lines.append("Ignored folders not covered by a rule:")
        lines.extend(_row(s) for s in folders)
    if files:
        lines.append("Ignored files named outright in .gitignore, not covered by a rule:")
        lines.extend(_row(s) for s in files)
    lines.append("Add the ones that are private with:")
    if folders:
        lines.append("  shiplock rules add folder " + " ".join(s.name + "/" for s in folders))
    if files:
        lines.append("  shiplock rules add file " + " ".join(s.name for s in files))
    return "\n".join(lines) + "\n"


def _row(s: Suggestion) -> str:
    shown = s.name + "/" if s.kind == "folder" else s.name
    if s.mentions == 0:
        where = "not named anywhere"
    else:
        sample = ", ".join(s.files[:3]) + (", ..." if len(s.files) > 3 else "")
        noun = "tracked file" if s.mentions == 1 else "tracked files"
        where = f"named in {s.mentions} {noun} ({sample})"
    return f"  {shown:<24} {where}"
