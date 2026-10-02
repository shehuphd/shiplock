"""User-declared rules, and the leak & internal-reference scan they drive.

shiplock ships no rules. Every internal-reference pattern, code-name, home
username, and personal email a check enforces is declared by the user:

- committed ref patterns in ``shiplock.toml`` (``[scan].refs``), for patterns
  that name nothing internal and should run in CI;
- private rules in local, gitignored rules files: the user's own file (one per
  machine, read in every repo), the files ``[scan].blocklist`` names, the
  default ``shiplock.local.toml`` at the repo root when it names none, and any
  file passed with ``--rules`` (how a CI gate supplies the rules it holds in a
  repo secret).

``internal-refs`` holds the ref patterns against the declared public docs;
``scan`` holds every rule against the whole git-tracked set, so a leak in a
file the repo never declared is caught too. The same rules run over a commit
range (``--range``, what a pre-push hook checks) and over the staged diff
(``--staged``, what a pre-commit hook checks).

Everything sensitive is masked in output: a private pattern's label, a matched
code-name, email, or username all show only their first character. An
``allow`` entry in a rules file marks something the repo contains on purpose,
anywhere or only under the paths it names: its matches never fail the run and
report as a count, only when they occur.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from shiplock._compat import tomllib
from shiplock._config import (
    CONFIG_FILENAME,
    Config,
    ConfigError,
    ScanConfig,
    parse_ref_tables,
)
from shiplock._report import Finding, Notice

CheckResult = tuple[list[Finding], list[Notice]]

DEFAULT_RULES_FILE = "shiplock.local.toml"
USER_RULES_ENV = "SHIPLOCK_USER_RULES"
NO_USER_RULES_ENV = "SHIPLOCK_NO_USER_RULES"
_RULES_KEYS = {"refs", "code_names", "home_usernames", "emails", "allow"}

# gitignore-style config files name private paths on purpose, to keep them out
# of the repo. That naming is the prevention, not a leak.
_HOUSE_EXCLUDES = (".gitignore", ".gitattributes")

# Opt-in generic secret patterns: token formats the vendors publish, not house
# rules. The last is broader and can fire on a fixture, which is why the whole
# set is off unless a repo turns ``[scan].secrets`` on.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    (
        "assigned secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*"
            r"['\"][^'\"]{8,}['\"]"
        ),
    ),
)

# An ``allow`` entry's scope: the globs it's allowed under, or () for anywhere.
Scope = tuple[str, ...]


@dataclass(frozen=True)
class Ref:
    """One declared internal-reference pattern.

    ``private`` marks a pattern from a local rules file: its label names
    something internal, so output masks it.
    """

    label: str
    pattern: re.Pattern[str]
    private: bool

    def message(self) -> str:
        if self.private:
            return f"internal reference ({mask(self.label)})"
        return f"internal reference to {self.label}"


@dataclass(frozen=True)
class Rules:
    """Every rule in force for one run, merged from all declared sources.

    ``allow`` maps a lowercased value to its scope. ``user_rules`` counts the
    entries the user's own file contributed, so a run can say where its rules
    came from; ``user_path`` is that file when it was read.
    """

    refs: tuple[Ref, ...] = ()
    code_names: tuple[str, ...] = ()
    home_usernames: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    allow: dict[str, Scope] = field(default_factory=dict)
    user_rules: int = 0
    user_path: str | None = None

    def has_identities(self) -> bool:
        return bool(self.code_names or self.home_usernames or self.emails)

    def scope_for(self, value: str) -> Scope | None:
        """The scope an ``allow`` entry gives ``value``, or None if none does."""
        return self.allow.get(value.lower())

    def allows(self, value: str, rel: str | None = None) -> bool:
        """Whether ``value`` is allowed at ``rel`` (anywhere when ``rel`` is None)."""
        scope = self.scope_for(value)
        if scope is None:
            return False
        if not scope:
            return True
        return rel is not None and _excluded(rel, list(scope))


@dataclass
class AllowTally:
    """Matches against allowed entries: counted, never reported as findings."""

    counts: dict[tuple[str, str], int] = field(default_factory=dict)
    files: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def add(self, kind: str, value: str, rel: str) -> None:
        key = (kind, value)
        self.counts[key] = self.counts.get(key, 0) + 1
        self.files.setdefault(key, set()).add(rel)

    def notices(self, check: str) -> list[Notice]:
        """One notice per allowed entry that matched; nothing for one that didn't."""
        out = []
        for (kind, value), count in sorted(self.counts.items()):
            times = "match" if count == 1 else "matches"
            where = ", ".join(sorted(self.files[(kind, value)]))
            out.append(
                Notice(
                    check,
                    f"allowed {kind} {mask(value)}: {count} {times} in {where}",
                    kind="warning",
                )
            )
        return out


def mask(value: str) -> str:
    """Hide all but the first character, so a value never echoes in full."""
    if not value:
        return value
    return value[0] + "*" * (len(value) - 1)


def strip_exempt(line: str, exempt: tuple[str, ...]) -> str:
    """Blank out exempt substrings so a ref pattern can't match inside one.

    A configured rules-file path refers to the rules mechanism, not a leak, so a
    pattern must not fire on the line that declares it. Replacing with spaces of
    the same length keeps every other column, and any leak elsewhere on the
    line, where it is.
    """
    for substring in exempt:
        if substring and substring in line:
            line = line.replace(substring, " " * len(substring))
    return line


def user_rules_path() -> tuple[Path | None, bool]:
    """The user's own rules file, and whether its absence gets a notice.

    ``SHIPLOCK_NO_USER_RULES`` turns it off. ``SHIPLOCK_USER_RULES`` names one,
    and a named file reports when missing. Otherwise the platform default:
    ``$XDG_CONFIG_HOME/shiplock/rules.toml`` (``~/.config`` when unset), or
    ``%APPDATA%\\shiplock\\rules.toml`` on Windows, silent when absent.
    """
    if os.environ.get(NO_USER_RULES_ENV):
        return None, False
    named = os.environ.get(USER_RULES_ENV)
    if named:
        return Path(os.path.expandvars(os.path.expanduser(named))), True
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "shiplock" / "rules.toml", False


@dataclass(frozen=True)
class _Source:
    raw: str
    report_missing: bool
    user: bool = False


def rules_file_paths(config: Config) -> list[tuple[str, bool]]:
    """The repo-level rules files to read, each with whether absence gets a notice.

    Files the user named (``[scan].blocklist``, ``--rules``) report when
    missing; the default file is optional and reads only when present. The
    user's own file is resolved separately by ``user_rules_path``.
    """
    named = list(config.scan.blocklist) if config.scan else []
    paths = [(p, True) for p in named] or [(DEFAULT_RULES_FILE, False)]
    paths.extend((p, True) for p in config.rules_paths)
    return paths


def _sources(config: Config) -> list[_Source]:
    out: list[_Source] = []
    user, report = user_rules_path()
    if user is not None:
        out.append(_Source(str(user), report, user=True))
    out.extend(_Source(raw, report) for raw, report in rules_file_paths(config))
    return out


def load_rules(config: Config, tracked: set[str] | None, check: str):
    """Merge every declared rule source into one ``Rules``.

    Returns ``(rules, notices, findings)``. A rules file git tracks is refused
    as a finding and not loaded: it would ship what it holds. Problems inside a
    rules file are notices, since that file varies per machine; problems in the
    committed config already raised ``ConfigError`` at load.
    """
    notices: list[Notice] = []
    findings: list[Finding] = []
    refs: list[Ref] = []
    code_names: list[str] = []
    home_usernames: list[str] = []
    emails: list[str] = []
    allow: dict[str, Scope] = {}
    user_rules = 0
    user_path: str | None = None

    if config.scan:
        refs.extend(
            Ref(r.label, re.compile(r.pattern), private=False) for r in config.scan.refs
        )

    for source in _sources(config):
        raw_path = source.raw
        path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        if not path.is_absolute():
            path = config.root / path
        rel = _relative_to_root(config.root, path)
        if tracked is not None and rel is not None and rel in tracked:
            findings.append(
                Finding(
                    check,
                    f"rules file {rel} is git-tracked; gitignore it so what it "
                    f"holds never ships",
                    path=rel,
                )
            )
            continue
        if not path.is_file():
            if source.report_missing:
                notices.append(Notice(check, f"rules file {raw_path} not found; skipped"))
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            notices.append(Notice(check, f"rules file {raw_path} unreadable: {exc}"))
            continue

        loaded = 0
        unknown = sorted(set(data) - _RULES_KEYS)
        if unknown:
            notices.append(
                Notice(check, f"rules file {raw_path} has unknown key(s) {', '.join(unknown)}; ignored")
            )
        try:
            for r in parse_ref_tables(data.get("refs"), f"{raw_path} refs"):
                refs.append(Ref(r.label, re.compile(r.pattern), private=True))
                loaded += 1
        except ConfigError as exc:
            notices.append(Notice(check, f"rules file {exc} Its refs were skipped."))
        for key, bucket in (
            ("code_names", code_names),
            ("home_usernames", home_usernames),
            ("emails", emails),
        ):
            values, bad = _str_list(data.get(key))
            bucket.extend(values)
            loaded += len(values)
            if bad:
                notices.append(Notice(check, f"rules file {raw_path} {key} must be a list of strings; skipped the rest"))
        entries, problems = _allow_entries(data.get("allow"))
        for value, scope in entries:
            _merge_scope(allow, value, scope)
        loaded += len(entries)
        for problem in problems:
            notices.append(Notice(check, f"rules file {raw_path} allow: {problem}"))
        if source.user:
            user_rules += loaded
            user_path = raw_path

    rules = Rules(
        refs=tuple(refs),
        code_names=tuple(dict.fromkeys(code_names)),
        home_usernames=tuple(dict.fromkeys(home_usernames)),
        emails=tuple(dict.fromkeys(emails)),
        allow=allow,
        user_rules=user_rules,
        user_path=user_path,
    )
    return rules, notices, findings


def _allow_entries(value: object) -> tuple[list[tuple[str, Scope]], list[str]]:
    """Parse ``allow``: bare strings (anywhere) and ``{value, paths}`` tables."""
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], ["must be a list; skipped"]
    entries: list[tuple[str, Scope]] = []
    problems: list[str] = []
    for i, item in enumerate(value, start=1):
        if isinstance(item, str):
            entries.append((item, ()))
            continue
        if not isinstance(item, dict):
            problems.append(f"entry {i} must be a string or a table; skipped")
            continue
        entry_value = item.get("value")
        paths = item.get("paths")
        if not isinstance(entry_value, str) or not entry_value:
            problems.append(f"entry {i} needs a non-empty string value; skipped")
            continue
        globs, bad = _str_list(paths)
        if bad or not globs:
            problems.append(f"entry {i} ({mask(entry_value)}) needs a non-empty list of path globs; skipped")
            continue
        entries.append((entry_value, tuple(_folder_glob(g) for g in globs)))
    return entries, problems


def _folder_glob(glob: str) -> str:
    """``docs/`` means everything under docs; other globs pass through."""
    return glob + "*" if glob.endswith("/") else glob


def _merge_scope(allow: dict[str, Scope], value: str, scope: Scope) -> None:
    """Combine scopes for one value: any bare entry allows it everywhere."""
    key = value.lower()
    current = allow.get(key)
    if current is None:
        allow[key] = scope
    elif not current or not scope:
        allow[key] = ()
    else:
        allow[key] = current + tuple(g for g in scope if g not in current)


def _scoped_out_message(base: str, scope: Scope) -> str:
    return f"{base}: allowed only in {', '.join(scope)}"


def ref_findings(
    check: str, rules: Rules, rel: str, i: int | None, probe: str, tally: AllowTally
) -> list[Finding]:
    """The ref-pattern findings for one line, allowed matches counted instead."""
    out = []
    for ref in rules.refs:
        if ref.pattern.search(probe):
            scope = rules.scope_for(ref.label)
            if scope is not None and rules.allows(ref.label, rel):
                tally.add("reference", ref.label, rel)
            elif scope is not None:
                out.append(Finding(check, _scoped_out_message(ref.message(), scope), path=rel, line=i))
            else:
                out.append(Finding(check, ref.message(), path=rel, line=i))
    return out


def self_declarations(config: Config) -> tuple[str, ...]:
    """Substrings ``shiplock.toml`` holds as rule declarations, not leaks.

    A committed ref pattern's label and regex sit in the config the scan
    reads; inside that one file they are exempt from ref matching. The regex
    appears both as parsed and as TOML-escaped source text.
    """
    if not config.scan:
        return ()
    out: list[str] = []
    for r in config.scan.refs:
        out.extend((r.label, r.pattern, r.pattern.replace("\\", "\\\\")))
    return tuple(out)


def default_scan_config() -> ScanConfig:
    """The scan a repo with no ``[scan]`` section runs.

    No committed refs; private rules come from the user's file, the default
    rules file, or ``--rules``. Secrets off.
    """
    return ScanConfig()


@dataclass
class _LineScanner:
    """Holds every rule against one line at a time, for any scan mode."""

    check: str
    config: Config
    scan: ScanConfig
    rules: Rules
    tally: AllowTally = field(default_factory=AllowTally)
    findings: list[Finding] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.code_patterns = [
            (cn, re.compile(rf"\b{re.escape(cn)}\b", re.IGNORECASE))
            for cn in self.rules.code_names
        ]
        self.home_patterns = [
            (user, re.compile(rf"(?:/Users/|/home/){re.escape(user)}\b", re.IGNORECASE))
            for user in self.rules.home_usernames
        ]
        self.email_patterns = [
            (email, re.compile(re.escape(email), re.IGNORECASE)) for email in self.rules.emails
        ]
        self.excludes = list(_HOUSE_EXCLUDES) + list(self.scan.exclude)
        self.exempt = tuple(self.scan.blocklist)
        self.config_exempt = self.exempt + self_declarations(self.config)

    def excluded(self, rel: str) -> bool:
        return _excluded(rel, self.excludes)

    def line(self, rel: str, i: int | None, line: str) -> None:
        """Scan one line at ``rel``:``i``; ``i`` is None for a commit message."""
        line_exempt = self.config_exempt if rel == CONFIG_FILENAME else self.exempt
        probe = strip_exempt(line, line_exempt)
        self.findings.extend(ref_findings(self.check, self.rules, rel, i, probe, self.tally))
        for cn, pattern in self.code_patterns:
            match = pattern.search(line)
            if match:
                self._identity("code-name", cn, f"internal code-name ({mask(match.group(0))})", rel, i)
        for user, pattern in self.home_patterns:
            if pattern.search(line):
                self._identity("home user", user, f"home path for user {mask(user)}", rel, i)
        for email, pattern in self.email_patterns:
            if pattern.search(line):
                self._identity("email", email, f"personal email ({mask(email)})", rel, i)
        if self.scan.secrets:
            for label, pattern in _SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    self.findings.append(
                        Finding(
                            self.check,
                            f"possible secret: {label} ({mask(match.group(0))})",
                            path=rel,
                            line=i,
                        )
                    )

    def _identity(self, kind: str, value: str, message: str, rel: str, i: int | None) -> None:
        scope = self.rules.scope_for(value)
        if scope is not None and self.rules.allows(value, rel):
            self.tally.add(kind, value, rel)
        elif scope is not None:
            self.findings.append(
                Finding(self.check, _scoped_out_message(message, scope), path=rel, line=i)
            )
        else:
            self.findings.append(Finding(self.check, message, path=rel, line=i))

    def closing_notices(self) -> list[Notice]:
        out: list[Notice] = []
        if not self.rules.has_identities():
            out.append(
                Notice(
                    self.check,
                    "no code-names, home usernames, or emails declared; the identity "
                    "scan was skipped",
                )
            )
        out.extend(self.tally.notices(self.check))
        return out


def _prepare(config: Config, scan: ScanConfig, name: str):
    """Load the rules and answer whether there's anything to scan with.

    Returns ``(scanner or None, notices, findings)``; a None scanner means the
    run skips, with the reason already in ``notices``.
    """
    tracked = git_tracked_files(config.root)
    if tracked is None:
        return None, [Notice(name, "not a git repo, or git unavailable; scan skipped")], [], None
    rules, notices, findings = load_rules(config, set(tracked), name)
    if rules.user_rules:
        notices.append(
            Notice(
                name,
                f"{rules.user_rules} rule(s) from the user rules file {rules.user_path}",
                kind="info",
            )
        )
    if not rules.refs and not rules.has_identities() and not scan.secrets:
        notices.append(
            Notice(
                name,
                "no rules declared ([scan].refs, a rules file, or [scan].secrets); skipped",
            )
        )
        return None, notices, findings, tracked
    scanner = _LineScanner(name, config, scan, rules)
    scanner.findings = findings
    return scanner, notices, [], tracked


def run_scan(config: Config, scan: ScanConfig) -> CheckResult:
    """Hold every declared rule against the git-tracked files."""
    name = "scan"
    scanner, notices, findings, tracked = _prepare(config, scan, name)
    if scanner is None:
        return findings, notices

    for rel in tracked:
        if scanner.excluded(rel):
            continue
        text = _read_scan_text(config.root / rel)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            scanner.line(rel, i, line)

    notices.extend(_stale_scope_notices(name, scanner.rules, tracked))
    notices.extend(scanner.closing_notices())
    return scanner.findings, notices


def _stale_scope_notices(check: str, rules: Rules, tracked: list[str]) -> list[Notice]:
    """A scoped allow glob that matches no tracked file points at nothing."""
    out: list[Notice] = []
    for value, scope in sorted(rules.allow.items()):
        for glob in scope:
            if not any(fnmatch.fnmatch(rel, glob) for rel in tracked):
                out.append(
                    Notice(
                        check,
                        f"allow entry {mask(value)} names {glob}, which matches no tracked file",
                        kind="warning",
                    )
                )
    return out


def run_scan_range(config: Config, scan: ScanConfig, rev_range: str) -> CheckResult:
    """Hold every declared rule against a commit range: added lines and messages.

    ``rev_range`` is any ``git rev-list`` expression (``origin/main..HEAD``, or
    ``HEAD --not --remotes`` for a branch the remote doesn't have yet; words
    are split on whitespace). Each commit's diff against its first parent is read for added lines, so a
    leak added and removed within the range is still found; each commit
    message is scanned as well, since a push carries those too.
    """
    name = "scan"
    scanner, notices, findings, _ = _prepare(config, scan, name)
    if scanner is None:
        return findings, notices

    words = rev_range.split()
    if not words:
        return findings, notices + [Notice(name, "an empty range; scan skipped")]
    proc = _git(config.root, "rev-list", "--reverse", *words)
    if proc.returncode != 0:
        return findings, notices + [
            Notice(name, f"range {rev_range} isn't a revision range git resolves; scan skipped")
        ]
    shas = proc.stdout.split()
    if not shas:
        notices.append(Notice(name, f"range {rev_range} holds no commits; nothing to scan"))
        return scanner.findings, notices

    for sha in shas:
        short = sha[:7]
        message = _git(config.root, "show", "-s", "--format=%B", sha).stdout
        for line in message.splitlines():
            scanner.line(f"commit {short}", None, line)
        diff = _git(config.root, "diff-tree", "--root", "-r", "-p", "-U0", "--no-color", sha).stdout
        for rel, i, line in _added_lines(diff):
            if not scanner.excluded(rel):
                scanner.line(rel, i, line)

    notices.extend(scanner.closing_notices())
    return scanner.findings, notices


def run_scan_staged(config: Config, scan: ScanConfig) -> CheckResult:
    """Hold every declared rule against the added lines of the staged diff."""
    name = "scan"
    scanner, notices, findings, _ = _prepare(config, scan, name)
    if scanner is None:
        return findings, notices

    diff = _git(config.root, "diff", "--cached", "-U0", "--no-color").stdout
    for rel, i, line in _added_lines(diff):
        if not scanner.excluded(rel):
            scanner.line(rel, i, line)

    notices.extend(scanner.closing_notices())
    return scanner.findings, notices


def _added_lines(diff: str):
    """Yield ``(path, new line number, text)`` for each added line of a diff.

    Reads unified-diff structure: a ``+++ b/path`` header names the file, a
    hunk header gives the new-side start line, and each context or added line
    advances it. Deleted lines don't move the new-side counter.
    """
    rel: str | None = None
    line_no = 0
    in_hunk = False
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw in diff.split("\n"):
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            rel = None if target == "/dev/null" else _strip_prefix(_unquote_diff_path(target))
            in_hunk = False
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git") or raw.startswith("index ") or raw.startswith("Binary files"):
            in_hunk = False
            continue
        match = hunk.match(raw)
        if match:
            line_no = int(match.group(1))
            in_hunk = True
            continue
        if not in_hunk or rel is None:
            continue
        if raw.startswith("+"):
            yield rel, line_no, raw[1:]
            line_no += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
        else:
            line_no += 1


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith("b/") else path


def _unquote_diff_path(path: str) -> str:
    """Git quotes paths with unusual characters; strip the quotes."""
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        return path[1:-1]
    return path


def git_tracked_files(root: Path) -> list[str] | None:
    """The repo-relative paths git tracks under ``root``, or None if not a repo.

    None also covers git being absent: the caller turns that into a notice
    rather than a crash, the same fail-safe the other git-backed checks use.
    """
    proc = _git(root, "ls-files", "-z")
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.split("\0") if p]


def _str_list(value: object) -> tuple[list[str], bool]:
    """The string entries of a list, and whether anything else was dropped."""
    if value is None:
        return [], False
    if not isinstance(value, list):
        return [], True
    good = [x for x in value if isinstance(x, str)]
    return good, len(good) != len(value)


def _relative_to_root(root: Path, path: Path) -> str | None:
    """``path`` as a repo-relative POSIX string, or None if it's outside root."""
    try:
        return (path.resolve()).relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _excluded(rel: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in globs)


def _read_scan_text(path: Path) -> str | None:
    """Read a tracked file as text, skipping anything that looks binary.

    A NUL byte marks a binary file (an image, a compiled artifact): decoding it
    would produce replacement-character noise, so it's skipped. Otherwise the
    bytes decode leniently, the same as the other checks.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(root), *args]
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(command, returncode=127, stdout="", stderr=str(exc))
