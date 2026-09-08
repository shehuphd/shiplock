"""The deterministic checks and the runner that drives them.

Each check is a function taking the parsed ``Config`` and returning
``(findings, notices)``. ``run_checks`` calls them in a fixed order and folds
their output into one ``Report``. A check whose config section is absent returns
a notice (skipped), never an empty pass; the same holds when a prerequisite is
missing (no git tag to diff against, an import that failed).
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shiplock import _style
from shiplock._compat import tomllib
from shiplock._config import (
    Config,
    VersionedFile,
)
from shiplock._introspect import IntrospectError, introspect
from shiplock._report import Finding, Notice

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


def _globbed_files(root: Path, patterns: list[str]) -> list[Path]:
    """Files matched by ``patterns`` under ``root``, deduplicated, glob order."""
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            resolved = path.resolve()
            if path.is_file() and resolved not in seen:
                seen.add(resolved)
                files.append(path)
    return files


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
    sources = [
        p for p in _globbed_files(config.root, m.sources) if p.resolve() not in exempt
    ]

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


def check_deps(config: Config) -> CheckResult:
    """Flag a package declared in both pyproject.toml and a requirements file.

    Two declarations of one dependency drift apart the day one of them is
    edited; the finding names the requirements line so the fix is a deletion.
    """
    name = "deps-declared-once"
    if config.deps is None:
        return [], [Notice(name, "no [deps] declared; skipped")]

    pyproject = _read_text(config.root / "pyproject.toml")
    if pyproject is None:
        return [], [Notice(name, "no pyproject.toml found; skipped")]
    try:
        raw = tomllib.loads(pyproject)
    except tomllib.TOMLDecodeError as exc:
        return [], [Notice(name, f"pyproject.toml didn't parse: {exc}")]

    declared = _pyproject_dep_names(raw)
    if not declared:
        return [], [Notice(name, "pyproject.toml declares no dependencies; skipped")]

    files = _globbed_files(config.root, config.deps.requirements)
    if not files:
        return [], [Notice(name, "[deps].requirements matched no files; skipped")]

    exempt = {_canonical_dep(x) for x in config.deps.exempt}
    findings: list[Finding] = []
    for path in files:
        text = _read_text(path)
        if text is None:
            continue
        rel = _rel(config.root, path)
        for i, line in enumerate(text.splitlines(), start=1):
            requirement = _requirement_name(line)
            if requirement is None:
                continue
            if requirement in declared and requirement not in exempt:
                findings.append(
                    Finding(
                        name,
                        f"'{requirement}' is declared in both pyproject.toml and "
                        f"{rel}; keep one declaration",
                        path=rel,
                        line=i,
                    )
                )
    return findings, []


def check_test_assertions(config: Config) -> CheckResult:
    """Flag a test function that carries no expectation at all.

    A test with no assert, no ``pytest.raises``/``warns``, and no ``assert_*``
    call passes whatever the code does; it documents nothing and guards
    nothing. Not-raising is an outcome a test must pin with an explicit check
    on the side effect, or exempt by name if not-raising truly is the contract.
    """
    name = "test-assertions"
    if config.tests is None:
        return [], [Notice(name, "no [tests] declared; skipped")]

    scanned = _globbed_files(config.root, config.tests.globs)
    if not scanned:
        return [], [Notice(name, "[tests].globs matched no files; skipped")]

    index, notices = _index_modules(config.root, _index_paths(config.root, scanned), name)
    scanned_keys = {_module_dotted(config.root, p) for p in scanned}

    exempt = set(config.tests.exempt)
    findings: list[Finding] = []
    for key, module in index.items():
        if key not in scanned_keys:
            continue
        rel = _rel(config.root, module.path)
        for func in _test_functions(module.tree):
            if func.name in exempt or f"{rel}::{func.name}" in exempt:
                continue
            if not _has_expectation(func, module, index):
                findings.append(
                    Finding(
                        name,
                        f"test '{func.name}' has no assertion, raises-check, or "
                        f"assert_* call; it passes no matter what the code does",
                        path=rel,
                        line=func.lineno,
                    )
                )
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
        paths.extend(_globbed_files(root, config.style.source_globs))

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


def _canonical_dep(name: str) -> str:
    """A package name in PEP 503 canonical form: lowercased, separators folded."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pyproject_dep_names(raw: dict) -> set[str]:
    """Canonical names of every dependency pyproject.toml declares."""
    project = raw.get("project", {})
    if not isinstance(project, dict):
        return set()
    specs: list[str] = []
    deps = project.get("dependencies", [])
    if isinstance(deps, list):
        specs.extend(s for s in deps if isinstance(s, str))
    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for group in optional.values():
            if isinstance(group, list):
                specs.extend(s for s in group if isinstance(s, str))
    names: set[str] = set()
    for spec in specs:
        parsed = _requirement_name(spec)
        if parsed is not None:
            names.add(parsed)
    return names


def _requirement_name(line: str) -> str | None:
    """The canonical package name on a requirements line, or None.

    Comment and blank lines, pip options (``-r``, ``-e``, ``--hash``), and bare
    URL or path lines carry no comparable name and return None. Extras,
    specifiers, and environment markers are stripped: only the name is compared.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "-")):
        return None
    if "://" in stripped.split("#", 1)[0].split(";", 1)[0].split("@", 1)[0]:
        return None
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", stripped)
    if match is None:
        return None
    return _canonical_dep(match.group(0))


# Call names that count as an expectation inside a test body. ``raises`` and
# ``warns`` cover pytest.raises/warns used as context managers or calls;
# anything starting with ``assert`` covers unittest's self.assert* and mock's
# assert_called* family.
_EXPECTATION_CALLS = {"raises", "warns", "deprecated_call"}


def _test_functions(tree: ast.Module):
    """Test functions pytest would collect: module-level ``test_*`` functions
    and ``test_*`` methods of ``Test*`` classes, at any nesting of the latter."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                yield node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in ast.walk(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("test_"):
                        yield item


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


def _dotted_target(node: ast.expr) -> str | None:
    """An attribute/name chain as a dotted string: ``a.b.c``, or None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


@dataclass(frozen=True)
class _TestModule:
    """One parsed module of the test set, with what it defines and imports."""

    dotted: str
    path: Path
    tree: ast.Module
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
    # local name -> (module, relative level, name in that module)
    from_imports: dict[str, tuple[str, int, str]]
    # local alias -> the module it names (``import a.b as c``, ``import a.b``)
    module_aliases: dict[str, str]


def _module_dotted(root: Path, path: Path) -> str:
    """``tests/support/helpers.py`` -> ``tests.support.helpers``."""
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        relative = Path(path.name)
    return ".".join(relative.with_suffix("").parts)


def _conftest_files(root: Path, scanned: list[Path]) -> list[Path]:
    """Every ``conftest.py`` from each scanned file's directory up to root."""
    found: list[Path] = []
    seen: set[Path] = set()
    root_resolved = root.resolve()
    for path in scanned:
        directory = path.resolve().parent
        while True:
            candidate = directory / "conftest.py"
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                found.append(candidate)
            if directory == root_resolved or root_resolved not in directory.parents:
                break
            directory = directory.parent
    return found


def _index_paths(root: Path, scanned: list[Path]) -> list[Path]:
    """Every module resolution may need to read: the whole test tree.

    A shared helper lives beside the tests as often as inside them, in a
    ``support/`` subpackage the test globs don't match, so the index covers
    each scanned file's own directory tree rather than the globs alone, plus
    any ``conftest.py`` above it.
    """
    directories = {p.resolve().parent for p in scanned}
    tops = [d for d in directories if not any(other in d.parents for other in directories)]

    paths: list[Path] = []
    seen: set[Path] = set()
    for directory in sorted(tops):
        for path in sorted(directory.rglob("*.py")):
            resolved = path.resolve()
            if path.is_file() and resolved not in seen:
                seen.add(resolved)
                paths.append(path)
    for path in _conftest_files(root, scanned):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            paths.append(path)
    return paths


def _index_modules(
    root: Path, paths: list[Path], check: str
) -> tuple[dict[str, _TestModule], list[Notice]]:
    """Parse each path once into the index resolution reads from."""
    index: dict[str, _TestModule] = {}
    notices: list[Notice] = []
    for path in paths:
        text = _read_text(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            notices.append(
                Notice(check, f"{_rel(root, path)} didn't parse ({exc.msg}); skipped")
            )
            continue
        functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        from_imports: dict[str, tuple[str, int, str]] = {}
        module_aliases: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[node.name] = node
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    local = alias.asname or alias.name
                    from_imports[local] = (node.module or "", node.level, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    module_aliases[local] = alias.name
        index[_module_dotted(root, path)] = _TestModule(
            dotted=_module_dotted(root, path),
            path=path,
            tree=tree,
            functions=functions,
            from_imports=from_imports,
            module_aliases=module_aliases,
        )
    return index, notices


def _find_module(
    dotted: str, level: int, importer: _TestModule, index: dict[str, _TestModule]
) -> _TestModule | None:
    """The indexed module an import names, or None when it's outside the set.

    A relative import resolves against the importer's own package. An absolute
    one matches an indexed module outright or by dotted-path suffix, since a
    test set is importable from its own root as well as the repo root; an
    ambiguous suffix resolves to nothing rather than a guess.
    """
    if level:
        parts = importer.dotted.split(".")[:-1]
        if level > 1:
            parts = parts[: len(parts) - (level - 1)]
        if dotted:
            parts = parts + dotted.split(".")
        return index.get(".".join(parts))
    if not dotted:
        return None
    if dotted in index:
        return index[dotted]
    matches = [m for key, m in index.items() if key.endswith("." + dotted)]
    return matches[0] if len(matches) == 1 else None


def _resolve_call(
    call: ast.Call, module: _TestModule, index: dict[str, _TestModule]
) -> tuple[_TestModule, ast.FunctionDef | ast.AsyncFunctionDef] | None:
    """The test-set function a call names, or None when it resolves outside.

    Resolving outside the set is the ordinary case: the code under test, a
    method on a fixture, a builtin. Those are never expectations, so a call
    the index can't place is left alone.
    """
    dotted = _dotted_target(call.func)
    if dotted is None:
        return None
    parts = dotted.split(".")

    if len(parts) == 1:
        name = parts[0]
        own = module.functions.get(name)
        if own is not None:
            return module, own
        if name in module.from_imports:
            source, level, original = module.from_imports[name]
            target = _find_module(source, level, module, index)
            if target is not None and original in target.functions:
                return target, target.functions[original]
        return None

    func_name = parts[-1]
    prefix = ".".join(parts[:-1])
    # `import tests.helpers as h` / `import tests.helpers`, then h.check()
    candidate = module.module_aliases.get(prefix)
    level = 0
    if candidate is None and prefix in module.from_imports:
        # `from tests import helpers`, then helpers.check()
        source, level, original = module.from_imports[prefix]
        candidate = f"{source}.{original}" if source else original
    if candidate is None:
        candidate = prefix
    target = _find_module(candidate, level, module, index)
    if target is not None and func_name in target.functions:
        return target, target.functions[func_name]
    return None


def _has_expectation(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    module: _TestModule,
    index: dict[str, _TestModule],
    _seen: set[tuple[str, str]] | None = None,
) -> bool:
    """True when the test pins an outcome, directly or through a helper.

    Direct: an assert, a raises/warns context, an ``assert*`` call, anywhere in
    the test's own tree (inline helpers included). Indirect: a call to any
    function in the scanned test set, followed recursively, so a shared
    ``_expect_x(...)`` helper counts for every test that calls it. The seen set
    makes mutual recursion terminate.
    """
    if _seen is None:
        _seen = set()
    key = (module.dotted, func.name)
    if key in _seen:
        return False
    _seen.add(key)

    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            called = _call_name(node)
            if called in _EXPECTATION_CALLS or called.startswith("assert"):
                return True
            resolved = _resolve_call(node, module, index)
            if resolved is not None:
                target_module, target_func = resolved
                if _has_expectation(target_func, target_module, index, _seen):
                    return True
    return False


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
    check_deps,
    check_test_assertions,
)


def run_checks(config: Config):
    """Run every check over ``config`` and return the combined report."""
    from shiplock._report import Report

    report = Report()
    for check in _CHECKS:
        findings, notices = check(config)
        report.extend(findings, notices)
    return report
