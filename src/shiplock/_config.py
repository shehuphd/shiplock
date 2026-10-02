"""Load and validate a repo's ``shiplock.toml``.

Each consuming repo declares its own surfaces here: which docs are public, which
sources get swept for banned words, which objects must be covered in which doc,
and so on. Every section is optional; a check whose section is absent skips with
a notice rather than inventing a default. ``load_config`` requires the file to
exist and parse; a repo with no ``shiplock.toml`` gets ``default_config``
instead, which checks what's detectable.

A malformed config raises ``ConfigError``, which the CLI turns into exit code 2
(usage/config error), kept distinct from exit 1 (a check found a problem).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from shiplock._compat import tomllib

CONFIG_FILENAME = "shiplock.toml"
COVERAGE_KINDS = ("enum", "params", "exports")
# Keys that belong only in a gitignored rules file: committing them would
# publish the private rules they hold.
_PRIVATE_SCAN_KEYS = {"allow", "code_names", "home_usernames", "emails"}


class ConfigError(Exception):
    """Raised when the config is missing, unparseable, or internally invalid.

    The message is written for a human to act on: it names the file and the
    specific problem, never a raw parser traceback.
    """


@dataclass(frozen=True)
class DocsConfig:
    public: list[str] = field(default_factory=list)
    changelog: str | None = None
    readme: str | None = None


@dataclass(frozen=True)
class StyleConfig:
    # The repo's own banned-word list; shiplock ships none.
    banned: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    source_globs: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VersionConfig:
    package: str | None = None


@dataclass(frozen=True)
class ArchitectureConfig:
    doc: str
    source_dir: str
    exempt: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ManifestConfig:
    # doc declared -> the manifest check runs in full. No doc and remind False
    # -> the missing-manifest reminder is silenced.
    doc: str | None = None
    sources: list[str] = field(default_factory=list)
    exempt: list[str] = field(default_factory=list)
    remind: bool = True


@dataclass(frozen=True)
class CoverageEntry:
    # ``target`` holds the TOML ``object`` key; renamed to avoid shadowing the
    # builtin. Format: "module:Attr.path", or a bare "module" for exports.
    target: str
    doc: str
    kind: str
    exempt: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VersionedFile:
    path: str
    pattern: str


@dataclass(frozen=True)
class DepsConfig:
    # Globs naming the requirements files to hold against pyproject.toml.
    requirements: list[str]
    exempt: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TestsConfig:
    # Globs naming the test files whose test functions must each carry an
    # expectation (an assert, a pytest.raises, an assert_* call).
    globs: list[str]
    exempt: list[str] = field(default_factory=list)

    # The Test* name makes pytest try to collect this class wherever it's
    # imported into a test module; this opts it out.
    __test__ = False


@dataclass(frozen=True)
class RefPattern:
    # A declared internal-reference pattern: ``label`` names what a hit means,
    # ``pattern`` is the regex. Read by both ``internal-refs`` and ``scan``.
    label: str
    pattern: str


@dataclass(frozen=True)
class ScanConfig:
    # The leak & internal-reference scan over the git-tracked set. ``blocklist``
    # names the local, gitignored rules files (private ref patterns, code-names,
    # home usernames, personal emails, allow entries); empty means the default
    # ``shiplock.local.toml`` at the repo root. ``refs`` holds committed ref
    # patterns that name nothing internal. ``secrets`` opts into generic
    # secret-pattern detection. ``exclude`` globs skip tracked files.
    blocklist: list[str] = field(default_factory=list)
    refs: list[RefPattern] = field(default_factory=list)
    secrets: bool = False
    exclude: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnchorsConfig:
    """``[anchors]``: the doc-anchors and doc-defaults checks.

    ``exempt`` lists tokens the docs name on purpose that the code doesn't
    declare (another tool's flag, an env var a CI service sets). ``defaults``
    turns the stated-default comparison off when a repo's parser isn't
    argparse.
    """

    exempt: list[str] = field(default_factory=list)
    defaults: bool = True


@dataclass(frozen=True)
class JudgeConfig:
    """``[judge]``: the model-judged layer, ``shiplock judge``.

    ``docs`` defaults to ``[docs].public``. ``undocumented`` lists code
    surface (flags, commands, env vars) that stays out of the docs on
    purpose, so coverage doesn't warn about it. ``provider`` names the
    judgment provider; ``model`` pins its model.
    """

    docs: list[str] = field(default_factory=list)
    undocumented: list[str] = field(default_factory=list)
    provider: str = "typesafe"
    model: str = "jev-1.13.0"


@dataclass(frozen=True)
class Config:
    """A repo's fully-parsed shiplock config, rooted at ``root``."""

    root: Path
    docs: DocsConfig | None = None
    style: StyleConfig | None = None
    version: VersionConfig | None = None
    architecture: ArchitectureConfig | None = None
    manifest: ManifestConfig | None = None
    coverage: list[CoverageEntry] = field(default_factory=list)
    versioned_files: list[VersionedFile] = field(default_factory=list)
    deps: DepsConfig | None = None
    tests: TestsConfig | None = None
    scan: ScanConfig | None = None
    anchors: AnchorsConfig | None = None
    judge: JudgeConfig | None = None
    # Extra rules files passed on the command line (``--rules``), e.g. the file
    # a CI gate writes from a repo secret. Read alongside ``[scan].blocklist``.
    rules_paths: tuple[str, ...] = ()
    # Deprecated keys the config used, reported as notices by the run.
    deprecations: tuple[str, ...] = ()


_DEFAULT_DOC_NAMES = (
    "README.md",
    "USAGE.md",
    "ARCHITECTURE.md",
    "CHANGELOG.md",
    "MANIFEST.md",
    "CONTRIBUTING.md",
)


def default_config(root: Path) -> Config:
    """A config for a repo with no ``shiplock.toml``: check what's detectable.

    The recognized doc names that exist at ``root`` become the public docs, the
    README and changelog get their special handling when present, and every
    check that needs a declaration skips with its usual notice. This is what
    lets ``shiplock check path/to/repo`` do something useful with no setup.
    """
    root = root.resolve()
    present = [name for name in _DEFAULT_DOC_NAMES if (root / name).is_file()]
    return Config(
        root=root,
        docs=DocsConfig(
            public=present,
            changelog="CHANGELOG.md" if "CHANGELOG.md" in present else None,
            readme="README.md" if "README.md" in present else None,
        ),
    )


def load_config(root: Path) -> Config:
    """Read ``<root>/shiplock.toml`` and return a validated ``Config``.

    Raises ``ConfigError`` on a missing file, a TOML parse error, an unknown
    section key, or a value of the wrong shape.
    """
    root = root.resolve()
    path = root / CONFIG_FILENAME
    if not path.is_file():
        raise ConfigError(
            f"No {CONFIG_FILENAME} found in {root}. Create one declaring this "
            f"repo's doc surfaces, or point shiplock at the directory that "
            f"holds it."
        )

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_FILENAME} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Can't read {path}: {exc}") from exc

    return _parse(root, raw)


def _parse(root: Path, raw: dict) -> Config:
    """Turn the raw TOML mapping into a typed ``Config``, validating as we go."""
    known = {
        "docs",
        "style",
        "version",
        "architecture",
        "manifest",
        "coverage",
        "versioned_files",
        "deps",
        "tests",
        "scan",
        "anchors",
        "judge",
    }
    unknown = set(raw) - known
    if unknown:
        listed = ", ".join(sorted(unknown))
        raise ConfigError(
            f"{CONFIG_FILENAME} has unknown section(s): {listed}. "
            f"Valid sections: {', '.join(sorted(known))}."
        )

    deprecations: list[str] = []
    return Config(
        root=root,
        docs=_parse_docs(raw.get("docs")),
        style=_parse_style(raw.get("style"), deprecations),
        version=_parse_version(raw.get("version")),
        architecture=_parse_architecture(raw.get("architecture")),
        manifest=_parse_manifest(raw.get("manifest")),
        coverage=_parse_coverage(raw.get("coverage")),
        versioned_files=_parse_versioned_files(raw.get("versioned_files")),
        deps=_parse_deps(raw.get("deps")),
        tests=_parse_tests(raw.get("tests")),
        scan=_parse_scan(raw.get("scan"), deprecations),
        anchors=_parse_anchors(raw.get("anchors")),
        judge=_parse_judge(raw.get("judge")),
        deprecations=tuple(deprecations),
    )


def _renamed(section: dict, old: str, new: str, where: str, deprecations: list[str]):
    """Read ``new``, accepting the deprecated ``old`` name until 1.0.0."""
    if old in section and new in section:
        raise ConfigError(f"{where} sets both '{new}' and its old name '{old}'; keep '{new}'.")
    if old in section:
        deprecations.append(f"{where}.{old} is deprecated; rename it to '{new}'.")
        return section[old]
    return section.get(new)


def _require_table(section: object, where: str) -> dict:
    if not isinstance(section, dict):
        raise ConfigError(f"{where} must be a table.")
    return section


def _require_str_list(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"{where} must be a list of strings.")
    return list(value)


def _require_str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where} must be a string.")
    return value


def _parse_anchors(section: object) -> AnchorsConfig | None:
    if section is None:
        return None
    table = _require_table(section, "[anchors]")
    unknown = set(table) - {"exempt", "defaults"}
    if unknown:
        raise ConfigError(f"[anchors] has unknown key(s): {', '.join(sorted(unknown))}.")
    defaults = table.get("defaults", True)
    if not isinstance(defaults, bool):
        raise ConfigError("[anchors].defaults must be true or false.")
    return AnchorsConfig(
        exempt=_require_str_list(table.get("exempt", []), "[anchors].exempt"),
        defaults=defaults,
    )


def _parse_judge(section: object) -> JudgeConfig | None:
    if section is None:
        return None
    table = _require_table(section, "[judge]")
    unknown = set(table) - {"docs", "undocumented", "provider", "model"}
    if unknown:
        raise ConfigError(f"[judge] has unknown key(s): {', '.join(sorted(unknown))}.")
    return JudgeConfig(
        docs=_require_str_list(table.get("docs", []), "[judge].docs"),
        undocumented=_require_str_list(table.get("undocumented", []), "[judge].undocumented"),
        provider=_require_str(table.get("provider", "typesafe"), "[judge].provider"),
        model=_require_str(table.get("model", "jev-1.13.0"), "[judge].model"),
    )


def _parse_docs(section: object) -> DocsConfig | None:
    if section is None:
        return None
    _require_table(section, "[docs]")
    return DocsConfig(
        public=_require_str_list(section.get("public", []), "[docs].public"),
        changelog=_opt_str(section.get("changelog"), "[docs].changelog"),
        readme=_opt_str(section.get("readme"), "[docs].readme"),
    )


def _parse_style(section: object, deprecations: list[str]) -> StyleConfig | None:
    if section is None:
        return None
    _require_table(section, "[style]")
    banned = _renamed(section, "extra_banned", "banned", "[style]", deprecations)
    return StyleConfig(
        banned=_require_str_list(banned if banned is not None else [], "[style].banned"),
        allow=_require_str_list(section.get("allow", []), "[style].allow"),
        source_globs=_require_str_list(
            section.get("source_globs", []), "[style].source_globs"
        ),
        exclude=_require_str_list(section.get("exclude", []), "[style].exclude"),
    )


def _parse_version(section: object) -> VersionConfig | None:
    if section is None:
        return None
    _require_table(section, "[version]")
    return VersionConfig(package=_opt_str(section.get("package"), "[version].package"))


def _parse_architecture(section: object) -> ArchitectureConfig | None:
    if section is None:
        return None
    _require_table(section, "[architecture]")
    if "doc" not in section or "source_dir" not in section:
        raise ConfigError("[architecture] requires both 'doc' and 'source_dir'.")
    return ArchitectureConfig(
        doc=_require_str(section["doc"], "[architecture].doc"),
        source_dir=_require_str(section["source_dir"], "[architecture].source_dir"),
        exempt=_require_str_list(section.get("exempt", []), "[architecture].exempt"),
    )


def _parse_manifest(section: object) -> ManifestConfig | None:
    if section is None:
        return None
    _require_table(section, "[manifest]")
    doc = _opt_str(section.get("doc"), "[manifest].doc")
    remind = section.get("remind", True)
    if not isinstance(remind, bool):
        raise ConfigError("[manifest].remind must be a boolean.")
    if doc is not None and remind is False:
        raise ConfigError(
            "[manifest].remind only applies when no manifest is declared; "
            "remove 'remind' or remove 'doc'."
        )
    if doc is None and (section.get("sources") or section.get("exempt")):
        raise ConfigError("[manifest].sources and .exempt require 'doc'.")
    return ManifestConfig(
        doc=doc,
        sources=_require_str_list(section.get("sources", []), "[manifest].sources"),
        exempt=_require_str_list(section.get("exempt", []), "[manifest].exempt"),
        remind=remind,
    )


def _parse_coverage(section: object) -> list[CoverageEntry]:
    if section is None:
        return []
    if not isinstance(section, list):
        raise ConfigError("[[coverage]] must be an array of tables.")
    entries: list[CoverageEntry] = []
    for i, item in enumerate(section):
        where = f"[[coverage]] entry {i}"
        _require_table(item, where)
        for key in ("object", "doc", "kind"):
            if key not in item:
                raise ConfigError(f"{where} is missing required key '{key}'.")
        kind = _require_str(item["kind"], f"{where}.kind")
        if kind not in COVERAGE_KINDS:
            raise ConfigError(
                f"{where}.kind is '{kind}'; choose from {', '.join(COVERAGE_KINDS)}."
            )
        entries.append(
            CoverageEntry(
                target=_require_str(item["object"], f"{where}.object"),
                doc=_require_str(item["doc"], f"{where}.doc"),
                kind=kind,
                exempt=_require_str_list(item.get("exempt", []), f"{where}.exempt"),
            )
        )
    return entries


def _parse_versioned_files(section: object) -> list[VersionedFile]:
    if section is None:
        return []
    if not isinstance(section, list):
        raise ConfigError("[[versioned_files]] must be an array of tables.")
    entries: list[VersionedFile] = []
    for i, item in enumerate(section):
        where = f"[[versioned_files]] entry {i}"
        _require_table(item, where)
        for key in ("path", "pattern"):
            if key not in item:
                raise ConfigError(f"{where} is missing required key '{key}'.")
        entries.append(
            VersionedFile(
                path=_require_str(item["path"], f"{where}.path"),
                pattern=_require_str(item["pattern"], f"{where}.pattern"),
            )
        )
    return entries


def _parse_deps(section: object) -> DepsConfig | None:
    if section is None:
        return None
    _require_table(section, "[deps]")
    if "requirements" not in section:
        raise ConfigError(
            "[deps] requires 'requirements': the globs naming the requirements "
            "files to hold against pyproject.toml."
        )
    return DepsConfig(
        requirements=_require_str_list(section["requirements"], "[deps].requirements"),
        exempt=_require_str_list(section.get("exempt", []), "[deps].exempt"),
    )


def _parse_tests(section: object) -> TestsConfig | None:
    if section is None:
        return None
    _require_table(section, "[tests]")
    if "globs" not in section:
        raise ConfigError(
            "[tests] requires 'globs': the globs naming the test files to scan."
        )
    return TestsConfig(
        globs=_require_str_list(section["globs"], "[tests].globs"),
        exempt=_require_str_list(section.get("exempt", []), "[tests].exempt"),
    )


def _parse_scan(section: object, deprecations: list[str]) -> ScanConfig | None:
    if section is None:
        return None
    _require_table(section, "[scan]")
    private = sorted(_PRIVATE_SCAN_KEYS & set(section))
    if private:
        raise ConfigError(
            f"[scan] sets {', '.join(private)}, which would publish private rules "
            f"in a committed file. Move them to a gitignored rules file "
            f"(shiplock.local.toml, or one [scan].blocklist names)."
        )

    raw_blocklist = section.get("blocklist", [])
    if isinstance(raw_blocklist, str):
        raw_blocklist = [raw_blocklist]
    blocklist = _require_str_list(raw_blocklist, "[scan].blocklist")

    secrets = section.get("secrets", False)
    if not isinstance(secrets, bool):
        raise ConfigError("[scan].secrets must be a boolean.")

    refs = parse_ref_tables(
        _renamed(section, "extra_refs", "refs", "[scan]", deprecations), "[scan].refs"
    )

    return ScanConfig(
        blocklist=blocklist,
        refs=refs,
        secrets=secrets,
        exclude=_require_str_list(section.get("exclude", []), "[scan].exclude"),
    )


def parse_ref_tables(section: object, where: str) -> list[RefPattern]:
    """Validate an array of ``{label, pattern}`` tables into ``RefPattern``s.

    Shared with the local rules-file loader, which catches the ``ConfigError``
    and reports it as a notice, since that file varies per machine.
    """
    if section is None:
        return []
    if not isinstance(section, list):
        raise ConfigError(f"{where} must be an array of tables.")
    entries: list[RefPattern] = []
    for i, item in enumerate(section):
        at = f"{where} entry {i}"
        _require_table(item, at)
        for key in ("label", "pattern"):
            if key not in item:
                raise ConfigError(f"{at} is missing required key '{key}'.")
        pattern = _require_str(item["pattern"], f"{at}.pattern")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{at}.pattern is not a valid regex: {exc}") from exc
        entries.append(RefPattern(label=_require_str(item["label"], f"{at}.label"), pattern=pattern))
    return entries


def _opt_str(value: object, where: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, where)
