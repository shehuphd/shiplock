"""Editing rules files, so nobody writes TOML or a regex by hand.

A rules file has five keys: four string arrays (``code_names``,
``home_usernames``, ``emails``, and the ``allow`` list, whose entries may also
be ``{value, paths}`` tables) and ``refs``, an array of ``{label, pattern}``
tables. shiplock owns that format, so a small writer for it is enough; the
standard library reads TOML but doesn't write it. Rewriting a file drops any
comment a person wrote in it, so the writer refuses a file that holds one
unless told to rewrite; the header comment it writes itself is recognised and
kept.

Rules about the person (code-names, usernames, emails, patterns for their own
conventions) go to the user file by default; rules about one repo (its
folders, its files, what it allows) go to the repo's file.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from shiplock._compat import tomllib
from shiplock._config import Config, ConfigError, parse_ref_tables
from shiplock._scan import (
    DEFAULT_RULES_FILE,
    Scope,
    _allow_entries,
    mask,
    user_rules_path,
)

HEADER = (
    "# shiplock rules file, written by `shiplock rules`. Keep it gitignored.\n"
    "# Keys: refs, code_names, home_usernames, emails, allow.\n"
)
_HEADER_LINES = {line for line in HEADER.splitlines()}

KINDS = ("code-name", "email", "username", "ref", "folder", "file")
_LIST_KEY = {"code-name": "code_names", "email": "emails", "username": "home_usernames"}

SECRET_NAME = "SHIPLOCK_RULES"


class RulesFileError(Exception):
    """A rules file can't be edited as asked; the message says why."""


@dataclass
class RulesFile:
    """The editable content of one rules file."""

    path: Path
    refs: list[tuple[str, str]] = field(default_factory=list)
    code_names: list[str] = field(default_factory=list)
    home_usernames: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    allow: list[tuple[str, Scope]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.refs or self.code_names or self.home_usernames or self.emails or self.allow)

    def counts(self) -> dict[str, int]:
        return {
            "refs": len(self.refs),
            "code_names": len(self.code_names),
            "home_usernames": len(self.home_usernames),
            "emails": len(self.emails),
            "allow": len(self.allow),
        }


def load_rules_file(path: Path) -> RulesFile:
    """Read a rules file, or an empty one when the file doesn't exist."""
    out = RulesFile(path=path)
    if not path.is_file():
        return out
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RulesFileError(f"{path} is not valid TOML: {exc}") from exc
    try:
        out.refs = [(r.label, r.pattern) for r in parse_ref_tables(data.get("refs"), f"{path} refs")]
    except ConfigError as exc:
        raise RulesFileError(str(exc)) from exc
    for key in ("code_names", "home_usernames", "emails"):
        values = data.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise RulesFileError(f"{path} {key} must be a list of strings")
        setattr(out, key, list(values))
    entries, problems = _allow_entries(data.get("allow"))
    if problems:
        raise RulesFileError(f"{path} allow: {problems[0]}")
    out.allow = entries
    return out


def has_foreign_comments(text: str) -> bool:
    """Whether ``text`` holds a comment the writer didn't put there."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if stripped not in _HEADER_LINES:
                return True
            continue
        # An inline comment: a ``#`` outside any quoted string.
        unquoted = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "", line)
        if "#" in unquoted:
            return True
    return False


def render(rules: RulesFile) -> str:
    """The rules file as TOML, in shiplock's own layout."""
    out = [HEADER]
    if rules.refs:
        out.append("refs = [\n")
        for label, pattern in rules.refs:
            out.append(f"  {{ label = {_toml_str(label)}, pattern = {_toml_str(pattern)} }},\n")
        out.append("]\n")
    for key in ("code_names", "home_usernames", "emails"):
        values = getattr(rules, key)
        if values:
            out.append(f"{key} = [{', '.join(_toml_str(v) for v in values)}]\n")
    if rules.allow:
        out.append("allow = [\n")
        for value, scope in rules.allow:
            if scope:
                paths = ", ".join(_toml_str(g) for g in scope)
                out.append(f"  {{ value = {_toml_str(value)}, paths = [{paths}] }},\n")
            else:
                out.append(f"  {_toml_str(value)},\n")
        out.append("]\n")
    return "".join(out)


def save(rules: RulesFile, rewrite: bool = False, entry: str = "") -> None:
    """Write the file, refusing to drop a person's comments unless ``rewrite``.

    ``entry`` is the TOML for what the caller just added, printed in the
    refusal so the person can paste it in by hand.
    """
    path = rules.path
    if path.is_file() and not rewrite:
        text = path.read_text(encoding="utf-8")
        if has_foreign_comments(text):
            shown = f"\n{entry.rstrip()}\n" if entry else " "
            raise RulesFileError(
                f"{path} holds comments that a rewrite would drop. Add this by hand:"
                f"{shown}or rerun with --rewrite to let shiplock rewrite the file."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(rules), encoding="utf-8")


def entry_toml(kind: str, value: str, pattern: str | None = None, scope: Scope = ()) -> str:
    """The TOML a person would paste to add one entry by hand."""
    if kind in _LIST_KEY:
        return f"{_LIST_KEY[kind]} = [{_toml_str(value)}]"
    if kind == "allow":
        if scope:
            return f"allow = [{{ value = {_toml_str(value)}, paths = [{', '.join(_toml_str(g) for g in scope)}] }}]"
        return f"allow = [{_toml_str(value)}]"
    label, pat = (value, pattern or "") if kind == "ref" else (folder_rule(value) if kind == "folder" else file_rule(value))
    return f"refs = [{{ label = {_toml_str(label)}, pattern = {_toml_str(pat)} }}]"


def _toml_str(value: str) -> str:
    """A TOML string: literal when it can be, so a regex reads as written."""
    if "'" not in value and "\n" not in value and "\r" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


# --------------------------------------------------------------------------
# Pattern builders
# --------------------------------------------------------------------------


def folder_rule(name: str) -> tuple[str, str]:
    """A ``(label, pattern)`` for mentions of a folder.

    ``drafts`` or ``./drafts/`` become label ``drafts/`` and a pattern that
    fires on ``drafts/`` but not on the same word ending a longer name or an
    npm scope. A dot folder (``.notes``) gets the dot form, which fires on
    ``.notes`` anywhere but not inside a domain like ``platform.notes.com``.
    A nested path keeps every component, since its last one alone would fire
    on unrelated text.
    """
    clean = name.strip().replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]
    clean = clean.strip("/")
    if not clean:
        raise RulesFileError("a folder name is needed")
    parts = clean.split("/")
    if len(parts) == 1 and parts[0].startswith("."):
        stem = parts[0]
        return stem, rf"(?<![\w.]){re.escape(stem)}\b"
    escaped = "/".join(re.escape(p) for p in parts)
    return clean + "/", rf"(?<![\w@.-]){escaped}/"


def file_rule(name: str) -> tuple[str, str]:
    """A ``(label, pattern)`` for mentions of a file by name.

    ``NOTES.md`` fires on ``NOTES.md`` and not on ``docs/NOTES.md``,
    ``my-NOTES.md``, or ``NOTES.mdx``.
    """
    clean = name.strip().replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]
    if not clean or clean.endswith("/"):
        raise RulesFileError("a file name is needed")
    tail = r"\b" if re.match(r"\w", clean[-1]) else ""
    return clean, rf"(?<![\w/.-]){re.escape(clean)}{tail}"


# --------------------------------------------------------------------------
# Where each rule goes
# --------------------------------------------------------------------------


def repo_rules_path(root: Path, config: Config | None = None) -> Path:
    """The repo's rules file: the first ``[scan].blocklist`` entry, or the default."""
    if config is not None and config.scan and config.scan.blocklist:
        raw = config.scan.blocklist[0]
        path = Path(raw).expanduser()
        return path if path.is_absolute() else root / path
    return root / DEFAULT_RULES_FILE


def user_file_path() -> Path:
    path, _ = user_rules_path()
    if path is None:
        raise RulesFileError(
            "the user rules file is turned off (SHIPLOCK_NO_USER_RULES is set); "
            "unset it, or write to the repo file with --repo"
        )
    return path


def target_path(kind: str, root: Path, config: Config | None, to_user: bool | None) -> Path:
    """Which file a rule goes to: ``to_user`` overrides, else by kind."""
    if to_user is None:
        to_user = kind in ("code-name", "email", "username", "ref")
    return user_file_path() if to_user else repo_rules_path(root, config)


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------


def add_rule(rules: RulesFile, kind: str, value: str, pattern: str | None = None) -> str:
    """Add one rule; returns the masked description printed for it."""
    if kind in _LIST_KEY:
        bucket: list[str] = getattr(rules, _LIST_KEY[kind])
        if value.lower() not in (v.lower() for v in bucket):
            bucket.append(value)
        return f"{kind} {mask(value)}"
    if kind == "ref":
        if not pattern:
            raise RulesFileError("a ref needs --pattern; use 'folder' or 'file' to have one built")
        label, pat = value, pattern
    elif kind == "folder":
        label, pat = folder_rule(value)
    elif kind == "file":
        label, pat = file_rule(value)
    else:
        raise RulesFileError(f"'{kind}' isn't a rule kind; the kinds are {', '.join(KINDS)}")
    try:
        re.compile(pat)
    except re.error as exc:
        raise RulesFileError(f"pattern {pat!r} isn't a valid regex: {exc}") from exc
    if not any(l == label for l, _ in rules.refs):
        rules.refs.append((label, pat))
    return f"ref {mask(label)}"


def allow_entry(rules: RulesFile, value: str, paths: list[str]) -> str:
    """Allow ``value`` everywhere, or under ``paths``; repeats merge paths."""
    scope: Scope = tuple(paths)
    for i, (existing, existing_scope) in enumerate(rules.allow):
        if existing.lower() == value.lower():
            if not existing_scope or not scope:
                rules.allow[i] = (existing, ())
            else:
                rules.allow[i] = (existing, existing_scope + tuple(g for g in scope if g not in existing_scope))
            break
    else:
        rules.allow.append((value, scope))
    where = f" in {', '.join(scope)}" if scope else " anywhere"
    return f"allow {mask(value)}{where}"


def remove_rule(rules: RulesFile, kind: str, value: str) -> bool:
    """Remove a rule by kind and value: a list entry, a ref, folder, or file by label, or an allow entry."""
    if kind in _LIST_KEY:
        bucket: list[str] = getattr(rules, _LIST_KEY[kind])
        before = len(bucket)
        bucket[:] = [v for v in bucket if v.lower() != value.lower()]
        return len(bucket) != before
    if kind in ("ref", "folder", "file"):
        labels = {value}
        if kind == "folder":
            labels.add(folder_rule(value)[0])
        if kind == "file":
            labels.add(file_rule(value)[0])
        before = len(rules.refs)
        rules.refs = [(l, p) for l, p in rules.refs if l not in labels]
        return len(rules.refs) != before
    if kind == "allow":
        before = len(rules.allow)
        rules.allow = [(v, s) for v, s in rules.allow if v.lower() != value.lower()]
        return len(rules.allow) != before
    raise RulesFileError(f"'{kind}' isn't a rule kind; the kinds are {', '.join(KINDS)}, allow")


# --------------------------------------------------------------------------
# Merging and the CI secret
# --------------------------------------------------------------------------


def merge(files: list[RulesFile]) -> RulesFile:
    """One document from several files, for the CI secret."""
    out = RulesFile(path=Path(SECRET_NAME))
    seen_refs: dict[str, str] = {}
    for f in files:
        for label, pattern in f.refs:
            if label in seen_refs and seen_refs[label] != pattern:
                raise RulesFileError(
                    f"ref {mask(label)} has two different patterns across the rules files; "
                    f"make them agree before pushing the secret"
                )
            if label not in seen_refs:
                seen_refs[label] = pattern
                out.refs.append((label, pattern))
        for key in ("code_names", "home_usernames", "emails"):
            bucket: list[str] = getattr(out, key)
            for v in getattr(f, key):
                if v.lower() not in (b.lower() for b in bucket):
                    bucket.append(v)
        for value, scope in f.allow:
            allow_entry(out, value, list(scope))
    return out


def push_secret(root: Path, document: str) -> str:
    """Store ``document`` as the repo's ``SHIPLOCK_RULES`` secret through ``gh``.

    The document goes on stdin, never as an argument, so it never appears in a
    process list or shell history. Returns a plain sentence on failure.
    """
    try:
        proc = subprocess.run(
            ["gh", "secret", "set", SECRET_NAME],
            input=document,
            capture_output=True,
            text=True,
            cwd=str(root),
            check=False,
        )
    except OSError:
        return (
            "the gh CLI isn't installed or isn't on PATH; install it from "
            "https://cli.github.com/ and run 'gh auth login', or set the secret by hand"
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return f"gh couldn't set the secret: {detail[-1] if detail else 'no output'}"
    return ""


def shared_repo_caution() -> str:
    return (
        f"The {SECRET_NAME} secret carries your full user rules into this repo. "
        f"Anyone who can edit this repo's workflows can read a secret, so on a "
        f"shared repo use --repo-only to send only this repo's own rules."
    )

