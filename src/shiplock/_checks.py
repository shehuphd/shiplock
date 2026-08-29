"""The deterministic checks and the runner that drives them.

Each check is a function taking the parsed ``Config`` and returning
``(findings, notices)``. ``run_checks`` calls them in a fixed order and folds
their output into one ``Report``. A check whose config section is absent returns
a notice (skipped), never an empty pass; the same holds when a prerequisite is
missing (no git tag to diff against, an import that failed).
"""

from __future__ import annotations

import enum
import importlib
import inspect
import re
import subprocess
import sys
from pathlib import Path

from shiplock import _style
from shiplock._config import (
    Config,
    CoverageEntry,
    VersionedFile,
)
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
    """Read a file as UTF-8, or None if it isn't there."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return None


def _mentions(text: str, name: str) -> bool:
    """True when ``name`` appears in ``text`` on a word boundary."""
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


class _ResolveError(Exception):
    """A coverage target couldn't be imported or resolved."""


def _resolve(target: str) -> object:
    """Resolve a ``module:Attr.path`` (or bare ``module``) to an object."""
    module_name, _, attr_path = target.partition(":")
    try:
        obj: object = importlib.import_module(module_name)
    except ImportError as exc:
        raise _ResolveError(f"can't import '{module_name}': {exc}") from exc
    for part in filter(None, attr_path.split(".")):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise _ResolveError(f"'{target}' has no attribute '{part}'") from exc
    return obj


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
            target = match.group("target").split()[0] if match.group("target") else ""
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
        module = importlib.import_module(package)
        dunder = getattr(module, "__version__")
    except ImportError as exc:
        return [], [
            Notice(name, f"can't import '{package}' to read __version__ ({exc}); "
            f"install the package (pip install .) so the check can run")
        ]
    except AttributeError:
        return [], [Notice(name, f"'{package}' has no __version__; skipped")]

    if dunder != project_version:
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

    findings: list[Finding] = []
    notices: list[Notice] = []
    for entry in config.coverage:
        text = _read_text(config.root / entry.doc)
        if text is None:
            notices.append(
                Notice(name, f"{entry.doc} not found for '{entry.target}'; skipped")
            )
            continue
        try:
            members = _coverage_members(entry)
        except _ResolveError as exc:
            notices.append(
                Notice(
                    name,
                    f"can't resolve '{entry.target}' ({exc}); install the package "
                    f"(pip install .) so the check can run",
                )
            )
            continue
        exempt = set(entry.exempt)
        for member in members:
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


def check_versioned_files(config: Config) -> CheckResult:
    """Assert a data file that changed since the last tag moved its marker."""
    name = "versioned-files"
    if not config.versioned_files:
        return [], [Notice(name, "no [[versioned_files]] declared; skipped")]

    tag = _last_tag(config.root)
    if tag is None:
        return [], [
            Notice(name, "no git tag to diff against (or not a git repo); skipped")
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


_INTERNAL_REF_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project/", re.compile(r"(?<!pypi\.org/)\bproject/")),
    ("CODING.md", re.compile(r"coding\.md", re.IGNORECASE)),
    ("ROADMAP", re.compile(r"ROADMAP")),
    (".claude", re.compile(r"\.claude\b")),
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


def _coverage_members(entry: CoverageEntry) -> list[str]:
    """The member names a coverage entry requires to be documented."""
    obj = _resolve(entry.target)
    if entry.kind == "enum":
        if not isinstance(obj, enum.EnumMeta):
            raise _ResolveError(f"'{entry.target}' is not an Enum")
        return [member.name for member in obj]  # type: ignore[union-attr]
    if entry.kind == "exports":
        dunder_all = getattr(obj, "__all__", None)
        if dunder_all is None:
            raise _ResolveError(f"'{entry.target}' has no __all__")
        return list(dunder_all)
    if entry.kind == "params":
        if not callable(obj):
            raise _ResolveError(f"'{entry.target}' is not callable")
        skip = {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        names: list[str] = []
        for param in inspect.signature(obj).parameters.values():
            if param.name in ("self", "cls") or param.kind in skip:
                continue
            names.append(param.name)
        return names
    raise _ResolveError(f"unknown coverage kind '{entry.kind}'")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


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
