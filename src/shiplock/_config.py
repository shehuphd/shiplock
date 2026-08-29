"""Load and validate a repo's ``shiplock.toml``.

Each consuming repo declares its own surfaces here: which docs are public, which
sources get swept for banned words, which objects must be covered in which doc,
and so on. Every section is optional; a check whose section is absent skips with
a notice rather than inventing a default. The one hard requirement is that the
file exists and parses.

A malformed config raises ``ConfigError``, which the CLI turns into exit code 2
(usage/config error), kept distinct from exit 1 (a check found a problem).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

CONFIG_FILENAME = "shiplock.toml"
COVERAGE_KINDS = ("enum", "params", "exports")


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
    extra_banned: list[str] = field(default_factory=list)
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
class Config:
    """A repo's fully-parsed shiplock config, rooted at ``root``."""

    root: Path
    docs: DocsConfig | None = None
    style: StyleConfig | None = None
    version: VersionConfig | None = None
    architecture: ArchitectureConfig | None = None
    coverage: list[CoverageEntry] = field(default_factory=list)
    versioned_files: list[VersionedFile] = field(default_factory=list)


def load_config(root: Path) -> Config:
    """Read ``<root>/shiplock.toml`` and return a validated ``Config``.

    Raises ``ConfigError`` on a missing file, a TOML parse error, an unknown
    section key, or a value of the wrong shape.
    """
    root = root.resolve()
    path = root / CONFIG_FILENAME
    if not path.is_file():
        raise ConfigError(
            f"No {CONFIG_FILENAME} found in {root}. "
            f"Create one declaring this repo's doc surfaces, or run shiplock "
            f"from the repo root with --root."
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
        "coverage",
        "versioned_files",
    }
    unknown = set(raw) - known
    if unknown:
        listed = ", ".join(sorted(unknown))
        raise ConfigError(
            f"{CONFIG_FILENAME} has unknown section(s): {listed}. "
            f"Valid sections: {', '.join(sorted(known))}."
        )

    return Config(
        root=root,
        docs=_parse_docs(raw.get("docs")),
        style=_parse_style(raw.get("style")),
        version=_parse_version(raw.get("version")),
        architecture=_parse_architecture(raw.get("architecture")),
        coverage=_parse_coverage(raw.get("coverage")),
        versioned_files=_parse_versioned_files(raw.get("versioned_files")),
    )


def _require_str_list(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"{where} must be a list of strings.")
    return list(value)


def _require_str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where} must be a string.")
    return value


def _parse_docs(section: object) -> DocsConfig | None:
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError("[docs] must be a table.")
    return DocsConfig(
        public=_require_str_list(section.get("public", []), "[docs].public"),
        changelog=_opt_str(section.get("changelog"), "[docs].changelog"),
        readme=_opt_str(section.get("readme"), "[docs].readme"),
    )


def _parse_style(section: object) -> StyleConfig | None:
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError("[style] must be a table.")
    return StyleConfig(
        extra_banned=_require_str_list(
            section.get("extra_banned", []), "[style].extra_banned"
        ),
        allow=_require_str_list(section.get("allow", []), "[style].allow"),
        source_globs=_require_str_list(
            section.get("source_globs", []), "[style].source_globs"
        ),
        exclude=_require_str_list(section.get("exclude", []), "[style].exclude"),
    )


def _parse_version(section: object) -> VersionConfig | None:
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError("[version] must be a table.")
    return VersionConfig(package=_opt_str(section.get("package"), "[version].package"))


def _parse_architecture(section: object) -> ArchitectureConfig | None:
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError("[architecture] must be a table.")
    if "doc" not in section or "source_dir" not in section:
        raise ConfigError("[architecture] requires both 'doc' and 'source_dir'.")
    return ArchitectureConfig(
        doc=_require_str(section["doc"], "[architecture].doc"),
        source_dir=_require_str(section["source_dir"], "[architecture].source_dir"),
        exempt=_require_str_list(section.get("exempt", []), "[architecture].exempt"),
    )


def _parse_coverage(section: object) -> list[CoverageEntry]:
    if section is None:
        return []
    if not isinstance(section, list):
        raise ConfigError("[[coverage]] must be an array of tables.")
    entries: list[CoverageEntry] = []
    for i, item in enumerate(section):
        where = f"[[coverage]] entry {i}"
        if not isinstance(item, dict):
            raise ConfigError(f"{where} must be a table.")
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
        if not isinstance(item, dict):
            raise ConfigError(f"{where} must be a table.")
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


def _opt_str(value: object, where: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, where)
