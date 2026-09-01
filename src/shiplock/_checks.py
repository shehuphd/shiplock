"""The deterministic checks and the runner that drives them.

Each check is a function taking the parsed ``Config`` and returning
``(findings, notices)``. ``run_checks`` calls them in a fixed order and folds
their output into one ``Report``. A check whose config section is absent returns
a notice (skipped), never an empty pass; the same holds when a prerequisite is
missing (no git tag to diff against, an import that failed).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from shiplock import _style
from shiplock._config import (
    Config,
    VersionedFile,
)
from shiplock._introspect import IntrospectError, introspect
from shiplock._report import Finding, Notice

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

CheckResult = tuple[list[Finding], list[Notice]]


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    """Read a file as UTF-8, or None if it can't be read.

    A file with invalid UTF-8 is still scanned, by decoding leniently: the
    banned words and internal-ref patterns are ASCII, so a stray byte can't hide
    a hit. A missing, directory, or permission-denied path returns None so the
    caller skips it instead of surfacing a raw traceback.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return None
    except OSError:
        # Covers missing files, directories, and permission errors.
        return None


def _mentions(text: str, name: str) -> bool:
    """True when ``name`` appears in ``text`` on a word boundary."""
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def check_docs_exist(config: Config) -> CheckResult:
    """Assert every declared public doc exists on disk."""
    name = "docs-exist"
    if config.docs is None or not config.docs.public:
        return [], [Notice(name, "no [docs].public declared; skipped")]

    findings: list[Finding] = []
    for rel in config.docs.public:
        if not (config.root / rel).is_file():
            findings.append(
                Finding(name, f"declared public doc is missing: {rel}", path=rel)
            )
    return findings, []


def check_banned_words(config: Config) -> CheckResult:
    """Sweep public docs and configured sources for the house banned words.

    The changelog is swept only above its first released-version heading;
    released sections are frozen history and stay untouched.
    """
    name = "banned-words"
    files = _banned_targets(config)
    if not files:
        return [], [Notice(name, "no docs or source globs to sweep; skipped")]

    style = config.style
    extra = tuple(style.extra_banned) if style else ()
    allow = tuple(style.allow) if style else ()
    changelog = config.docs.changelog if config.docs else None

    findings: list[Finding] = []
    for path in files:
        text = _read_text(path)
        if text is None:
            continue  # docs-exist reports a missing declared doc.
        rel = _rel(config.root, path)
        cutoff = None
        if changelog is not None and rel == changelog:
            cutoff = _changelog_cutoff(text)
        for hit in _style.find_banned(text, extra, allow):
            if cutoff is not None and hit.line >= cutoff:
                continue
            findings.append(
                Finding(name, f"banned word '{hit.word}'", path=rel, line=hit.line)
            )
    return findings, []


def check_internal_refs(config: Config) -> CheckResult:
    """Flag references to internal-only artifacts in public docs."""
    name = "internal-refs"
    if config.docs is None or not config.docs.public:
        return [], [Notice(name, "no [docs].public declared; skipped")]

    findings: list[Finding] = []
    for rel in config.docs.public:
        text = _read_text(config.root / rel)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for label, pattern in _INTERNAL_REF_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        Finding(
                            name,
                            f"internal reference to {label}",
                            path=rel,
                            line=i,
                        )
                    )
    return findings, []


def check_readme_links(config: Config) -> CheckResult:
    """Assert every markdown link in the README is absolute."""
    name = "readme-links"
    readme = config.docs.readme if config.docs else None
    if readme is None:
        return [], [Notice(name, "no [docs].readme declared; skipped")]
    text = _read_text(config.root / readme)
    if text is None:
        return [], [Notice(name, f"{readme} not found; skipped (docs-exist covers it)")]

    findings: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for match in _MD_LINK.finditer(line):
            # A link target may carry a title ("url \"text\""); take the first
            # token, tolerating an empty or whitespace-only target.
            parts = (match.group("target") or "").split()
            target = parts[0] if parts else ""
            if not _is_absolute_link(target):
                findings.append(
                    Finding(
                        name,
                        f"relative link '{target}' (PyPI resolves it against "
                        f"pypi.org, not the repo)",
                        path=readme,
                        line=i,
                    )
                )
    return findings, []


def check_version(config: Config) -> CheckResult:
    """Assert pyproject version, package __version__, and changelog agree."""
    name = "version"
    if config.version is None or config.version.package is None:
        return [], [Notice(name, "no [version].package declared; skipped")]

    package = config.version.package
    notices: list[Notice] = []
    findings: list[Finding] = []

    pyproject = _read_text(config.root / "pyproject.toml")
    if pyproject is None:
        return [], [Notice(name, "no pyproject.toml found; skipped")]
    try:
        project_version = tomllib.loads(pyproject).get("project", {}).get("version")
    except tomllib.TOMLDecodeError as exc:
        return [], [Notice(name, f"pyproject.toml didn't parse: {exc}")]
    if not project_version:
        return [], [Notice(name, "pyproject.toml has no [project].version; skipped")]

    try:
        result = introspect(config.root, [{"id": "v", "op": "version", "module": package}])["v"]
    except IntrospectError as exc:
        return [], [Notice(name, f"couldn't introspect '{package}' ({exc}); skipped")]

    status = result.get("status")
    if status == "not_under_root":
        return [], [
            Notice(name, f"'{package}' didn't resolve to source under the checked "
            f"root; install it (pip install .) or run from its repo root")
        ]
    if status == "error":
        return [], [
            Notice(name, f"can't import '{package}' to read __version__ "
            f"({result.get('error')}); the check needs the package and its "
            f"dependencies importable")
        ]
    if status != "ok":
        return [], [Notice(name, f"can't read '{package}' version ({status}); skipped")]

    dunder = result.get("version")
    if dunder is None:
        notices.append(
            Notice(name, f"'{package}' has no __version__; the changelog heading is "
            f"still checked")
        )
    elif dunder != project_version:
        findings.append(
            Finding(
                name,
                f"pyproject version {project_version!r} != {package}.__version__ "
                f"{dunder!r}",
                path="pyproject.toml",
            )
        )

    changelog = config.docs.changelog if config.docs else None
    if changelog is None:
        notices.append(Notice(name, "no [docs].changelog declared; heading not checked"))
    else:
        text = _read_text(config.root / changelog)
        if text is None:
            notices.append(Notice(name, f"{changelog} not found; heading not checked"))
        elif not _changelog_covers(text, project_version):
            findings.append(
                Finding(
                    name,
                    f"{changelog} has no heading for {project_version} and no "
                    f"[Unreleased] section",
                    path=changelog,
                )
            )
    return findings, notices


def check_architecture(config: Config) -> CheckResult:
    """Assert every top-level module and subpackage is named in ARCHITECTURE."""
    name = "architecture"
    arch = config.architecture
    if arch is None:
        return [], [Notice(name, "no [architecture] declared; skipped")]

    source_dir = config.root / arch.source_dir
    if not source_dir.is_dir():
        return [], [Notice(name, f"source dir {arch.source_dir} not found; skipped")]
    text = _read_text(config.root / arch.doc)
    if text is None:
        return [], [Notice(name, f"{arch.doc} not found; skipped (docs-exist covers it)")]

    exempt = set(arch.exempt)
    findings: list[Finding] = []
    for module_name in _source_members(source_dir):
        if module_name in exempt:
            continue
        if not _mentions(text, module_name):
            findings.append(
                Finding(
                    name,
                    f"module '{module_name}' is not named in {arch.doc}",
                    path=arch.doc,
                )
            )
    return findings, []


def check_coverage(config: Config) -> CheckResult:
    """Assert every member of a declared object appears in its declared doc."""
    name = "coverage"
    if not config.coverage:
        return [], [Notice(name, "no [[coverage]] entries declared; skipped")]

    queries = [
        {"id": str(i), "op": entry.kind, "target": entry.target}
        for i, entry in enumerate(config.coverage)
    ]
    try:
        results = introspect(config.root, queries)
    except IntrospectError as exc:
        return [], [Notice(name, f"couldn't introspect coverage targets ({exc}); skipped")]

    findings: list[Finding] = []
    notices: list[Notice] = []
    for i, entry in enumerate(config.coverage):
        text = _read_text(config.root / entry.doc)
        if text is None:
            notices.append(
                Notice(name, f"{entry.doc} not found for '{entry.target}'; skipped")
            )
            continue
        result = results.get(str(i), {"status": "error", "error": "no result"})
        if result.get("status") != "ok":
            notices.append(Notice(name, _coverage_skip_reason(entry.target, result)))
            continue
        exempt = set(entry.exempt)
        for member in result.get("members", []):
            if member in exempt:
                continue
            if not _mentions(text, member):
                findings.append(
                    Finding(
                        name,
                        f"'{entry.target}' member '{member}' is not documented in "
                        f"{entry.doc}",
                        path=entry.doc,
                    )
                )
    return findings, notices


def _coverage_skip_reason(target: str, result: dict) -> str:
    """Turn an introspection status into a plain-language skip message."""
    status = result.get("status")
    if status == "not_under_root":
        return (
            f"'{target}' didn't resolve to source under the checked root; install "
            f"it (pip install .) or run from its repo root"
        )
    if status == "error":
        return (
            f"can't resolve '{target}' ({result.get('error')}); the check needs the "
            f"package and its dependencies importable"
        )
    reasons = {
        "no_all": f"'{target}' has no __all__; skipped",
        "not_enum": f"'{target}' is not an Enum; skipped",
        "not_callable": f"'{target}' is not callable; skipped",
        "unknown_op": f"'{target}' has an unsupported coverage kind; skipped",
    }
    return reasons.get(status, f"can't read '{target}' ({status}); skipped")


def check_manifest(config: Config) -> CheckResult:
    """Assert the per-file manifest exists, lists every source file, and moved
    with the sources — or, when no manifest is declared, remind that one helps.

    The reminder is a notice, never a finding: a per-file map is good hygiene,
    not something to fail a build over, and ``[manifest].remind = false``
    silences it for teams that don't keep one.
    """
    name = "manifest"
    m = config.manifest

    if m is None or m.doc is None:
        if m is not None and m.remind is False:
            return [], []
        if (config.root / "MANIFEST.md").is_file():
            return [], [
                Notice(name, "MANIFEST.md exists but isn't declared; add "
                "[manifest] with doc and sources to shiplock.toml to keep it "
                "checked, or set [manifest].remind = false to turn this "
                "reminder off")
            ]
        return [], [
            Notice(name, "no MANIFEST.md found; a per-file map of the codebase "
            "gives readers a file index without opening the code. Generate one "
            "by hand or with an AI tool and declare it under [manifest], or set "
            "[manifest].remind = false to turn this reminder off")
        ]

    text = _read_text(config.root / m.doc)
    if text is None:
        return [Finding(name, f"declared manifest is missing: {m.doc}", path=m.doc)], []

    findings: list[Finding] = []
    notices: list[Notice] = []

    if not re.search(r"^Last updated:", text, re.MULTILINE):
        findings.append(
            Finding(name, f"{m.doc} has no 'Last updated:' line", path=m.doc)
        )

    exempt: set[Path] = set()
    for pattern in m.exempt:
        exempt.update(p.resolve() for p in config.root.glob(pattern))
    sources: list[Path] = []
    seen: set[Path] = set()
    for pattern in m.sources:
        for path in config.root.glob(pattern):
            resolved = path.resolve()
            if path.is_file() and resolved not in exempt and resolved not in seen:
                seen.add(resolved)
                sources.append(path)

    for path in sources:
        rel = _rel(config.root, path)
        if not _manifest_lists(text, rel, path.name):
            findings.append(
                Finding(name, f"source file '{rel}' isn't listed in {m.doc}", path=m.doc)
            )

    tag = _last_tag(config.root)
    if tag is None:
        notices.append(
            Notice(name, "no git tag to compare the manifest against (not a git "
            "repo, no tags yet, or git isn't installed); staleness not checked")
        )
    else:
        changed = _changed_since(config.root, tag)
        if changed is not None:
            touched = sorted(
                rel for rel in (_rel(config.root, p) for p in sources) if rel in changed
            )
            if touched and m.doc not in changed:
                findings.append(
                    Finding(
                        name,
                        f"sources changed since {tag} (e.g. {touched[0]}) but "
                        f"{m.doc} didn't",
                        path=m.doc,
                    )
                )
    return findings, notices


def check_versioned_files(config: Config) -> CheckResult:
    """Assert a data file that changed since the last tag moved its marker."""
    name = "versioned-files"
    if not config.versioned_files:
        return [], [Notice(name, "no [[versioned_files]] declared; skipped")]

    tag = _last_tag(config.root)
    if tag is None:
        return [], [
            Notice(name, "no git tag to diff against (not a git repo, no tags "
            "yet, or git isn't installed); skipped")
        ]

    findings: list[Finding] = []
    notices: list[Notice] = []
    for entry in config.versioned_files:
        result = _check_one_versioned_file(config.root, tag, entry, name)
        findings.extend(result[0])
        notices.extend(result[1])
    return findings, notices


# --------------------------------------------------------------------------
# Check-specific helpers
# --------------------------------------------------------------------------


def _banned_targets(config: Config) -> list[Path]:
    """Every file the banned-word sweep covers, after exclusions."""
    root = config.root
    paths: list[Path] = []
    if config.docs:
        paths.extend(root / rel for rel in config.docs.public)
    if config.style:
        for pattern in config.style.source_globs:
            paths.extend(p for p in root.glob(pattern) if p.is_file())

    excluded: set[Path] = set()
    if config.style:
        for pattern in config.style.exclude:
            excluded.update(p.resolve() for p in root.glob(pattern))

    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in excluded or resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(path)
    return ordered


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


_RELEASED_HEADING = re.compile(
    r"^##\s.*(\d+\.\d+\.\d+|\d{4}-\d{2}-\d{2})",
)


def _changelog_cutoff(text: str) -> int | None:
    """Line number of the first released-version heading, or None if none."""
    for i, line in enumerate(text.splitlines(), start=1):
        if _RELEASED_HEADING.match(line):
            return i
    return None


def _changelog_covers(text: str, version: str) -> bool:
    """True when the changelog names this version or carries an Unreleased head."""
    for line in text.splitlines():
        if not line.startswith("##"):
            continue
        lowered = line.lower()
        if "unreleased" in lowered:
            return True
        if _mentions(line, version):
            return True
    return False


# The lookbehinds keep each pattern matching the internal artifact and not a
# lookalike: "pypi.org/project/" is a public URL, "encoding.md" isn't the
# coding-standards file, and "platform.claude.com" is a domain, not the .claude
# assistant directory.
_INTERNAL_REF_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project/", re.compile(r"(?<!pypi\.org/)\bproject/")),
    ("CODING.md", re.compile(r"(?<!\w)CODING\.md", re.IGNORECASE)),
    ("ROADMAP", re.compile(r"ROADMAP")),
    (".claude", re.compile(r"(?<![\w.])\.claude\b")),
    (".codex", re.compile(r"(?<![\w.])\.codex\b")),
    (".grok", re.compile(r"(?<![\w.])\.grok\b")),
    (".cursor", re.compile(r"(?<![\w.])\.cursor\b")),
)


_MD_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target>[^)]*)\)")


def _is_absolute_link(target: str) -> bool:
    if not target:
        return False
    return target.startswith(("http://", "https://", "#", "mailto:"))


def _source_members(source_dir: Path) -> list[str]:
    """Top-level module stems and subpackage names directly under a source dir."""
    members: set[str] = set()
    for child in source_dir.iterdir():
        if child.is_file() and child.suffix == ".py":
            members.add(child.stem)
        elif child.is_dir() and (child / "__init__.py").is_file():
            members.add(child.name)
    return sorted(members)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(root), *args]
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        # git isn't installed (or isn't executable): report it as a failed
        # command so callers skip with a notice instead of crashing.
        return subprocess.CompletedProcess(command, returncode=127, stdout="", stderr=str(exc))


def _manifest_lists(text: str, rel: str, basename: str) -> bool:
    """A file counts as listed by its repo-relative path or its file name.

    Manifests often group entries in tables under a directory heading, naming
    files by basename or a shortened path there — those spellings satisfy the
    check too. The lookbehind only rejects a longer file name that happens to
    end with this one (``xcli.py`` doesn't list ``cli.py``).
    """
    if rel in text:
        return True
    return re.search(rf"(?<![\w-]){re.escape(basename)}", text) is not None


def _changed_since(root: Path, tag: str) -> set[str] | None:
    """Repo-relative paths of tracked files that differ from ``tag``, or None
    when git can't answer."""
    result = _git(root, "diff", "--name-only", tag)
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _last_tag(root: Path) -> str | None:
    result = _git(root, "describe", "--tags", "--abbrev=0")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _check_one_versioned_file(
    root: Path, tag: str, entry: VersionedFile, name: str
) -> CheckResult:
    current = _read_text(root / entry.path)
    if current is None:
        return [Finding(name, f"declared versioned file missing: {entry.path}",
                        path=entry.path)], []

    at_tag = _git(root, "show", f"{tag}:{entry.path}")
    if at_tag.returncode != 0:
        return [], [Notice(name, f"{entry.path} absent at {tag}; skipped")]

    if current == at_tag.stdout:
        return [], []  # unchanged since the tag; nothing to enforce.

    pattern = re.compile(entry.pattern)
    now = pattern.search(current)
    then = pattern.search(at_tag.stdout)
    if now is None:
        return [Finding(name, f"version marker not found in {entry.path}",
                        path=entry.path)], []
    if then is not None and now.group(1) == then.group(1):
        return [Finding(
            name,
            f"{entry.path} changed since {tag} but its marker "
            f"{now.group(1)!r} didn't move",
            path=entry.path,
        )], []
    return [], []


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

# Fixed registry. Order is the order findings are reported in.
_CHECKS = (
    check_docs_exist,
    check_banned_words,
    check_internal_refs,
    check_readme_links,
    check_version,
    check_architecture,
    check_coverage,
    check_manifest,
    check_versioned_files,
)


def run_checks(config: Config):
    """Run every check over ``config`` and return the combined report."""
    from shiplock._report import Report

    report = Report()
    for check in _CHECKS:
        findings, notices = check(config)
        report.extend(findings, notices)
    return report
