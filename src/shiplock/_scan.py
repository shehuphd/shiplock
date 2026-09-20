"""The leak & internal-reference scan over a repo's git-tracked files.

Where ``internal-refs`` reads only the declared public docs, ``scan`` reads the
git-tracked set (``git ls-files``): a leak gate has to catch what a repo forgot
to declare, so a code-name in a test fixture or a README example doesn't ship.
It flags the house internal-reference patterns (extendable by the caller) plus
an identity class, code-names, home paths, and personal emails, drawn from a
local, gitignored blocklist whose location the caller sets. An opt-in switch
adds generic secret-pattern detection.

Every matched sensitive string is masked in output: a finding names the file,
the line, and the rule, and shows only the first character of the match. A scan
that echoed a code-name would move the leak into the CI log it writes.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shiplock._config import Config, RefPattern, ScanConfig
from shiplock._report import Finding, Notice

CheckResult = tuple[list[Finding], list[Notice]]

# gitignore-style config files name the internal directories on purpose, to
# keep them out of the repo. That naming is the prevention, not a leak, so the
# scan never treats these files as a finding.
_HOUSE_EXCLUDES = (".gitignore", ".gitattributes")

# The lookbehinds keep each pattern matching the internal artifact and not a
# lookalike: "pypi.org/project/" is a public URL, "encoding.md" isn't the
# coding-standards file, and "platform.claude.com" is a domain, not the .claude
# assistant directory.
_HOUSE_REFS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project/", re.compile(r"(?<!pypi\.org/)\bproject/")),
    ("CODING.md", re.compile(r"(?<!\w)CODING\.md", re.IGNORECASE)),
    ("ROADMAP", re.compile(r"ROADMAP")),
    ("BUGS.md", re.compile(r"(?<!\w)BUGS\.md", re.IGNORECASE)),
    ("LESSONS.md", re.compile(r"(?<!\w)LESSONS\.md", re.IGNORECASE)),
    ("TESTS.md", re.compile(r"(?<!\w)TESTS\.md", re.IGNORECASE)),
    (".claude", re.compile(r"(?<![\w.])\.claude\b")),
    (".codex", re.compile(r"(?<![\w.])\.codex\b")),
    (".grok", re.compile(r"(?<![\w.])\.grok\b")),
    (".cursor", re.compile(r"(?<![\w.])\.cursor\b")),
)

# Opt-in generic secret patterns. Conservative forms with a low false-positive
# rate; the last is broader and can fire on a fixture, which is why the whole
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


def build_ref_patterns(
    extra: list[RefPattern] = [],
) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """The house internal-ref patterns plus the caller's extras, compiled.

    Both ``internal-refs`` and ``scan`` build from this, so the extendable
    pattern set is defined once. Caller patterns are validated at config parse
    time, so the compile here can't raise.
    """
    patterns = list(_HOUSE_REFS)
    for rp in extra:
        patterns.append((rp.label, re.compile(rp.pattern)))
    return tuple(patterns)


@dataclass(frozen=True)
class Blocklist:
    """The sensitive identity inputs, merged from the configured local files."""

    code_names: tuple[str, ...] = ()
    home_usernames: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.code_names or self.home_usernames or self.emails)


def mask(match: str) -> str:
    """Hide all but the first character, so a match never echoes in full."""
    if not match:
        return match
    return match[0] + "*" * (len(match) - 1)


def strip_exempt(line: str, exempt: tuple[str, ...]) -> str:
    """Blank out exempt substrings so a ref pattern can't match inside one.

    A configured blocklist path is a reference to the blocklist mechanism, not
    a leak: `~/.claude/shiplock-blocklist.toml` names the recommended blocklist
    home, so the `.claude` pattern must not fire on the line that declares it.
    This generalises the `pypi.org/project/` carve-out to the caller's declared
    paths. Replacing with spaces of the same length keeps every other column,
    and any leak elsewhere on the line, where it is.
    """
    for substring in exempt:
        if substring and substring in line:
            line = line.replace(substring, " " * len(substring))
    return line


def default_scan_config() -> ScanConfig:
    """The scan a bare ``shiplock scan`` runs when a repo declares no ``[scan]``.

    House ref-patterns over the tracked set, no blocklist (the identity classes
    skip with a notice), secrets off. An explicit ``scan`` invocation should do
    something useful with no setup, the way ``check`` does.
    """
    return ScanConfig()


def run_scan(config: Config, scan: ScanConfig) -> CheckResult:
    """Scan the git-tracked files for internal refs and identity leaks."""
    name = "scan"
    tracked = git_tracked_files(config.root)
    if tracked is None:
        return [], [Notice(name, "not a git repo, or git unavailable; scan skipped")]

    findings: list[Finding] = []
    notices: list[Notice] = []

    blocklist, bl_notices, bl_findings = _load_blocklists(
        config.root, scan.blocklist, set(tracked), name
    )
    notices.extend(bl_notices)
    findings.extend(bl_findings)

    ref_patterns = build_ref_patterns(scan.extra_refs)
    code_patterns = [
        (cn, re.compile(rf"\b{re.escape(cn)}\b", re.IGNORECASE))
        for cn in blocklist.code_names
    ]
    home_patterns = [
        (user, re.compile(rf"(?:/Users/|/home/){re.escape(user)}\b", re.IGNORECASE))
        for user in blocklist.home_usernames
    ]
    email_patterns = [
        (email, re.compile(re.escape(email), re.IGNORECASE))
        for email in blocklist.emails
    ]
    excludes = list(_HOUSE_EXCLUDES) + list(scan.exclude)
    exempt = tuple(scan.blocklist)

    for rel in tracked:
        if _excluded(rel, excludes):
            continue
        text = _read_scan_text(config.root / rel)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            ref_probe = strip_exempt(line, exempt)
            for label, pattern in ref_patterns:
                if pattern.search(ref_probe):
                    findings.append(
                        Finding(name, f"internal reference to {label}", path=rel, line=i)
                    )
            for cn, pattern in code_patterns:
                match = pattern.search(line)
                if match:
                    findings.append(
                        Finding(
                            name,
                            f"internal code-name ({mask(match.group(0))})",
                            path=rel,
                            line=i,
                        )
                    )
            for user, pattern in home_patterns:
                if pattern.search(line):
                    findings.append(
                        Finding(
                            name,
                            f"home path for user {mask(user)}",
                            path=rel,
                            line=i,
                        )
                    )
            for email, pattern in email_patterns:
                if pattern.search(line):
                    findings.append(
                        Finding(
                            name,
                            f"personal email ({mask(email)})",
                            path=rel,
                            line=i,
                        )
                    )
            if scan.secrets:
                for label, pattern in _SECRET_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        findings.append(
                            Finding(
                                name,
                                f"possible secret: {label} ({mask(match.group(0))})",
                                path=rel,
                                line=i,
                            )
                        )

    if blocklist.is_empty() and not bl_findings:
        notices.append(
            Notice(
                name,
                "no blocklist configured; the identity scan (code-names, home "
                "paths, personal emails) was skipped",
            )
        )
    return findings, notices


def git_tracked_files(root: Path) -> list[str] | None:
    """The repo-relative paths git tracks under ``root``, or None if not a repo.

    None also covers git being absent: the caller turns that into a notice
    rather than a crash, the same fail-safe the other git-backed checks use.
    """
    proc = _git(root, "ls-files", "-z")
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.split("\0") if p]


def _load_blocklists(
    root: Path, paths: list[str], tracked: set[str], check: str
) -> tuple[Blocklist, list[Notice], list[Finding]]:
    """Merge the configured blocklist files; refuse a tracked one.

    A blocklist that git tracks would ship the code-names it holds, so a
    configured blocklist path that appears in the tracked set is a finding, not
    a silent load. A configured path that doesn't exist is a notice: the file
    is the caller's to place, and its absence disables the identity classes
    rather than failing the run.
    """
    from shiplock._compat import tomllib

    notices: list[Notice] = []
    findings: list[Finding] = []
    code_names: list[str] = []
    home_usernames: list[str] = []
    emails: list[str] = []

    for raw_path in paths:
        expanded = os.path.expandvars(os.path.expanduser(raw_path))
        path = Path(expanded)
        if not path.is_absolute():
            path = root / path
        rel = _relative_to_root(root, path)
        if rel is not None and rel in tracked:
            findings.append(
                Finding(
                    check,
                    f"blocklist file {rel} is git-tracked; gitignore it so its "
                    f"code-names never ship",
                    path=rel,
                )
            )
            continue
        if not path.is_file():
            notices.append(Notice(check, f"blocklist {raw_path} not found; skipped"))
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            notices.append(Notice(check, f"blocklist {raw_path} unreadable: {exc}"))
            continue
        code_names.extend(_str_list(data.get("code_names")))
        home_usernames.extend(_str_list(data.get("home_usernames")))
        emails.extend(_str_list(data.get("emails")))

    return (
        Blocklist(
            code_names=tuple(dict.fromkeys(code_names)),
            home_usernames=tuple(dict.fromkeys(home_usernames)),
            emails=tuple(dict.fromkeys(emails)),
        ),
        notices,
        findings,
    )


def _str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []


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
    bytes decode leniently, the same as the other checks, since the patterns are
    ASCII and a stray byte can't hide a hit.
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
