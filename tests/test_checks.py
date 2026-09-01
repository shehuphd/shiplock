"""Each check: the failing cases it must fire on, then the clean case.

Configs are built directly rather than through TOML, so each test isolates one
check's logic. The loader has its own tests in test_config.py.
"""

from __future__ import annotations

import subprocess

from shiplock._checks import (
    check_architecture,
    check_banned_words,
    check_coverage,
    check_docs_exist,
    check_internal_refs,
    check_manifest,
    check_readme_links,
    check_version,
    check_versioned_files,
)
from shiplock._config import (
    ArchitectureConfig,
    Config,
    CoverageEntry,
    DocsConfig,
    ManifestConfig,
    StyleConfig,
    VersionConfig,
    VersionedFile,
)


# --- docs-exist -----------------------------------------------------------


def test_docs_exist_fires_on_missing_doc(tmp_path):
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_docs_exist(config)
    assert [f.path for f in findings] == ["README.md"]


def test_docs_exist_skips_without_docs_section(tmp_path):
    findings, notices = check_docs_exist(Config(root=tmp_path))
    assert findings == []
    assert len(notices) == 1


def test_docs_exist_clean_when_present(tmp_path, write_file):
    write_file(tmp_path, "README.md", "# Demo\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_docs_exist(config)
    assert findings == []


# --- banned-words ---------------------------------------------------------


def test_banned_words_fires_in_a_public_doc(tmp_path, write_file):
    write_file(tmp_path, "README.md", "this is real\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_banned_words(config)
    assert len(findings) == 1
    assert findings[0].line == 1


def test_banned_words_skips_released_changelog_section(tmp_path, write_file):
    # A banned word under a released heading is frozen history: not swept.
    write_file(
        tmp_path,
        "CHANGELOG.md",
        "# Changelog\n\n## 1.0.0 — 2026-01-01\n\n- shipped a real thing\n",
    )
    config = Config(
        root=tmp_path,
        docs=DocsConfig(public=["CHANGELOG.md"], changelog="CHANGELOG.md"),
    )
    findings, _ = check_banned_words(config)
    assert findings == []


def test_banned_words_sweeps_unreleased_changelog_section(tmp_path, write_file):
    write_file(
        tmp_path,
        "CHANGELOG.md",
        "# Changelog\n\n## [Unreleased]\n\n- a real change\n\n## 1.0.0 — 2026-01-01\n",
    )
    config = Config(
        root=tmp_path,
        docs=DocsConfig(public=["CHANGELOG.md"], changelog="CHANGELOG.md"),
    )
    findings, _ = check_banned_words(config)
    assert len(findings) == 1


def test_banned_words_fires_in_a_source_glob(tmp_path, write_file):
    write_file(tmp_path, "src/pkg/core.py", "# a real workhorse\n")
    config = Config(root=tmp_path, style=StyleConfig(source_globs=["src/**/*.py"]))
    findings, _ = check_banned_words(config)
    assert len(findings) == 1
    assert findings[0].path == "src/pkg/core.py"


def test_banned_words_honors_exclude_glob(tmp_path, write_file):
    write_file(tmp_path, "src/pkg/_words.py", "# defines real, gaps, sits\n")
    config = Config(
        root=tmp_path,
        style=StyleConfig(
            source_globs=["src/**/*.py"], exclude=["src/pkg/_words.py"]
        ),
    )
    findings, notices = check_banned_words(config)
    # Only file was excluded, so there's nothing to sweep -> a notice, not a hit.
    assert findings == []
    assert len(notices) == 1


# --- internal-refs --------------------------------------------------------


def test_banned_words_scans_non_utf8_file_without_crashing(tmp_path):
    # Invalid UTF-8 bytes must not raise; ASCII banned words still get caught.
    (tmp_path / "README.md").write_bytes(b"\xff\xfe this is real\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_banned_words(config)
    assert any(f.message == "banned word 'real'" for f in findings)


def test_internal_refs_fires_on_project_folder(tmp_path, write_file):
    write_file(tmp_path, "README.md", "see project/NOTES.md for details\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_internal_refs(config)
    assert len(findings) == 1


def test_internal_refs_carves_out_pypi_project_url(tmp_path, write_file):
    write_file(tmp_path, "README.md", "https://pypi.org/project/shiplock/\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_internal_refs(config)
    assert findings == []


def test_internal_refs_roadmap_is_case_sensitive(tmp_path, write_file):
    # Uppercase ROADMAP is the file/label form and fires; prose "roadmap" doesn't.
    write_file(tmp_path, "README.md", "our roadmap is public\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_internal_refs(config)
    assert findings == []


def test_internal_refs_fires_on_claude_dir(tmp_path, write_file):
    write_file(tmp_path, "README.md", "config lives in .claude/settings\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_internal_refs(config)
    assert len(findings) == 1


def test_internal_refs_fires_on_other_assistant_dirs(tmp_path, write_file):
    write_file(
        tmp_path,
        "README.md",
        "notes in .codex/plan.md, .grok/state, and .cursor/rules\n",
    )
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_internal_refs(config)
    assert len(findings) == 3


def test_internal_refs_ignores_claude_domain(tmp_path, write_file):
    # "platform.claude.com" is a domain, not the .claude assistant directory.
    write_file(tmp_path, "README.md", "get a key at platform.claude.com/keys\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_internal_refs(config)
    assert findings == []


def test_internal_refs_ignores_coding_lookalike(tmp_path, write_file):
    # "encoding.md" is not the coding-standards file.
    write_file(tmp_path, "README.md", "see encoding.md for byte details\n")
    config = Config(root=tmp_path, docs=DocsConfig(public=["README.md"]))
    findings, _ = check_internal_refs(config)
    assert findings == []


# --- readme-links ---------------------------------------------------------


def test_readme_links_fires_on_relative_link(tmp_path, write_file):
    write_file(tmp_path, "README.md", "see [the manual](USAGE.md)\n")
    config = Config(root=tmp_path, docs=DocsConfig(readme="README.md"))
    findings, _ = check_readme_links(config)
    assert len(findings) == 1


def test_readme_links_accepts_absolute_and_anchor_and_mailto(tmp_path, write_file):
    write_file(
        tmp_path,
        "README.md",
        "[a](https://x.com) [b](#section) [c](mailto:x@y.com)\n",
    )
    config = Config(root=tmp_path, docs=DocsConfig(readme="README.md"))
    findings, _ = check_readme_links(config)
    assert findings == []


def test_readme_links_tolerates_whitespace_target(tmp_path, write_file):
    # A whitespace-only target must not crash the split; it's still a finding.
    write_file(tmp_path, "README.md", "see [x](   ) here\n")
    config = Config(root=tmp_path, docs=DocsConfig(readme="README.md"))
    findings, _ = check_readme_links(config)
    assert len(findings) == 1


def test_readme_links_skips_without_readme(tmp_path):
    findings, notices = check_readme_links(Config(root=tmp_path, docs=DocsConfig()))
    assert findings == []
    assert len(notices) == 1


# --- version --------------------------------------------------------------


def _write_pyproject(write_file, root, version):
    write_file(root, "pyproject.toml", f'[project]\nname = "x"\nversion = "{version}"\n')


def test_version_fires_on_mismatch(tmp_path, write_file, temp_module):
    name = temp_module("vermismatch", '__version__ = "1.0.0"\n')
    _write_pyproject(write_file, tmp_path, "2.0.0")
    write_file(tmp_path, "CHANGELOG.md", "## [Unreleased]\n")
    config = Config(
        root=tmp_path,
        docs=DocsConfig(changelog="CHANGELOG.md"),
        version=VersionConfig(package=name),
    )
    findings, _ = check_version(config)
    assert any("!=" in f.message for f in findings)


def test_version_skips_on_import_failure(tmp_path, write_file):
    _write_pyproject(write_file, tmp_path, "1.0.0")
    config = Config(
        root=tmp_path, version=VersionConfig(package="no_such_module_xyz")
    )
    findings, notices = check_version(config)
    assert findings == []
    assert any("can't import" in n.message for n in notices)


def test_version_fires_on_changelog_without_heading(tmp_path, write_file, temp_module):
    name = temp_module("vernoheading", '__version__ = "1.0.0"\n')
    _write_pyproject(write_file, tmp_path, "1.0.0")
    write_file(tmp_path, "CHANGELOG.md", "# Changelog\n\n## 0.9.0 — 2025-01-01\n")
    config = Config(
        root=tmp_path,
        docs=DocsConfig(changelog="CHANGELOG.md"),
        version=VersionConfig(package=name),
    )
    findings, _ = check_version(config)
    assert any("no heading" in f.message for f in findings)


def test_version_notice_when_package_resolves_outside_root(tmp_path, write_file):
    # 'json' imports, but its source isn't under root: compare nothing, notice.
    _write_pyproject(write_file, tmp_path, "1.0.0")
    write_file(tmp_path, "CHANGELOG.md", "## [Unreleased]\n")
    config = Config(
        root=tmp_path,
        docs=DocsConfig(changelog="CHANGELOG.md"),
        version=VersionConfig(package="json"),
    )
    findings, notices = check_version(config)
    assert findings == []
    assert any("resolve to source under" in n.message for n in notices)


def test_version_clean_when_aligned(tmp_path, write_file, temp_module):
    name = temp_module("veraligned", '__version__ = "1.0.0"\n')
    _write_pyproject(write_file, tmp_path, "1.0.0")
    write_file(tmp_path, "CHANGELOG.md", "## [Unreleased]\n")
    config = Config(
        root=tmp_path,
        docs=DocsConfig(changelog="CHANGELOG.md"),
        version=VersionConfig(package=name),
    )
    findings, _ = check_version(config)
    assert findings == []


# --- architecture ---------------------------------------------------------


def _arch_config(root):
    return Config(
        root=root,
        architecture=ArchitectureConfig(
            doc="ARCHITECTURE.md", source_dir="src/pkg", exempt=["__init__"]
        ),
    )


def test_architecture_fires_on_unnamed_module(tmp_path, write_file):
    write_file(tmp_path, "src/pkg/__init__.py", "")
    write_file(tmp_path, "src/pkg/core.py", "x = 1\n")
    write_file(tmp_path, "ARCHITECTURE.md", "# Arch\nnothing named here\n")
    findings, _ = check_architecture(_arch_config(tmp_path))
    assert any("core" in f.message for f in findings)


def test_architecture_clean_when_named(tmp_path, write_file):
    write_file(tmp_path, "src/pkg/__init__.py", "")
    write_file(tmp_path, "src/pkg/core.py", "x = 1\n")
    write_file(tmp_path, "ARCHITECTURE.md", "# Arch\nThe core module does work.\n")
    findings, _ = check_architecture(_arch_config(tmp_path))
    assert findings == []


# --- coverage -------------------------------------------------------------


def test_coverage_fires_on_undocumented_enum_member(tmp_path, write_file, temp_module):
    name = temp_module(
        "covenum",
        "import enum\nclass Color(enum.Enum):\n    RED = 1\n    BLUE = 2\n",
    )
    write_file(tmp_path, "USAGE.md", "# Usage\nRED is documented.\n")
    config = Config(
        root=tmp_path,
        coverage=[CoverageEntry(target=f"{name}:Color", doc="USAGE.md", kind="enum")],
    )
    findings, _ = check_coverage(config)
    assert any("BLUE" in f.message for f in findings)


def test_coverage_fires_on_undocumented_param(tmp_path, write_file, temp_module):
    name = temp_module("covparams", "def build(host, port):\n    return host\n")
    write_file(tmp_path, "USAGE.md", "# Usage\nPass a host.\n")
    config = Config(
        root=tmp_path,
        coverage=[CoverageEntry(target=f"{name}:build", doc="USAGE.md", kind="params")],
    )
    findings, _ = check_coverage(config)
    assert any("port" in f.message for f in findings)


def test_coverage_skips_on_import_failure(tmp_path, write_file):
    write_file(tmp_path, "USAGE.md", "# Usage\n")
    config = Config(
        root=tmp_path,
        coverage=[CoverageEntry(target="no_such_xyz", doc="USAGE.md", kind="exports")],
    )
    findings, notices = check_coverage(config)
    assert findings == []
    assert any("can't resolve" in n.message for n in notices)


def test_coverage_fires_on_undocumented_export(tmp_path, write_file, temp_module):
    name = temp_module(
        "covmissing",
        '__all__ = ["Widget", "helper"]\nclass Widget: ...\ndef helper(): ...\n',
    )
    write_file(tmp_path, "USAGE.md", "# Usage\nOnly Widget is documented.\n")
    config = Config(
        root=tmp_path,
        coverage=[CoverageEntry(target=name, doc="USAGE.md", kind="exports")],
    )
    findings, _ = check_coverage(config)
    assert any("helper" in f.message for f in findings)


def test_coverage_clean_when_all_documented(tmp_path, write_file, temp_module):
    name = temp_module(
        "covexports",
        '__all__ = ["Widget", "helper"]\nclass Widget: ...\ndef helper(): ...\n',
    )
    write_file(tmp_path, "USAGE.md", "# Usage\nWidget and helper are here.\n")
    config = Config(
        root=tmp_path,
        coverage=[CoverageEntry(target=name, doc="USAGE.md", kind="exports")],
    )
    findings, _ = check_coverage(config)
    assert findings == []


# --- manifest -------------------------------------------------------------


_MANIFEST_TEXT = "# Manifest\n\nLast updated: 2026-09-01 00:00:00 UTC\n\n| core.py | logic |\n"


def test_manifest_reminds_when_no_manifest_exists(tmp_path):
    findings, notices = check_manifest(Config(root=tmp_path))
    assert findings == []
    assert any("remind = false" in n.message for n in notices)


def test_manifest_reminds_when_undeclared_but_present(tmp_path, write_file):
    write_file(tmp_path, "MANIFEST.md", _MANIFEST_TEXT)
    findings, notices = check_manifest(Config(root=tmp_path))
    assert findings == []
    assert any("isn't declared" in n.message for n in notices)


def test_manifest_reminder_can_be_silenced(tmp_path):
    config = Config(root=tmp_path, manifest=ManifestConfig(remind=False))
    findings, notices = check_manifest(config)
    assert findings == []
    assert notices == []


def test_manifest_fires_when_declared_doc_missing(tmp_path):
    config = Config(root=tmp_path, manifest=ManifestConfig(doc="MANIFEST.md"))
    findings, _ = check_manifest(config)
    assert any("missing" in f.message for f in findings)


def test_manifest_fires_without_last_updated_line(tmp_path, write_file):
    write_file(tmp_path, "MANIFEST.md", "# Manifest\n\n| core.py | logic |\n")
    config = Config(root=tmp_path, manifest=ManifestConfig(doc="MANIFEST.md"))
    findings, _ = check_manifest(config)
    assert any("Last updated" in f.message for f in findings)


def test_manifest_fires_on_unlisted_source_file(tmp_path, write_file):
    write_file(tmp_path, "MANIFEST.md", _MANIFEST_TEXT)
    write_file(tmp_path, "src/pkg/core.py", "x = 1\n")
    write_file(tmp_path, "src/pkg/extra.py", "y = 2\n")
    config = Config(
        root=tmp_path,
        manifest=ManifestConfig(doc="MANIFEST.md", sources=["src/**/*.py"]),
    )
    findings, _ = check_manifest(config)
    assert any("extra.py" in f.message for f in findings)
    assert not any("core.py" in f.message for f in findings)


def test_manifest_clean_when_all_listed(tmp_path, write_file):
    write_file(tmp_path, "MANIFEST.md", _MANIFEST_TEXT)
    write_file(tmp_path, "src/pkg/core.py", "x = 1\n")
    config = Config(
        root=tmp_path,
        manifest=ManifestConfig(doc="MANIFEST.md", sources=["src/**/*.py"]),
    )
    findings, _ = check_manifest(config)
    assert findings == []


def test_manifest_fires_when_sources_moved_without_it(git_repo, write_file):
    write_file(git_repo, "MANIFEST.md", _MANIFEST_TEXT)
    write_file(git_repo, "src/pkg/core.py", "x = 1\n")
    _commit_and_tag(git_repo, "v1.0.0")
    write_file(git_repo, "src/pkg/core.py", "x = 2\n")  # source moved, manifest not
    config = Config(
        root=git_repo,
        manifest=ManifestConfig(doc="MANIFEST.md", sources=["src/**/*.py"]),
    )
    findings, _ = check_manifest(config)
    assert any("didn't" in f.message for f in findings)


def test_manifest_clean_when_it_moved_with_sources(git_repo, write_file):
    write_file(git_repo, "MANIFEST.md", _MANIFEST_TEXT)
    write_file(git_repo, "src/pkg/core.py", "x = 1\n")
    _commit_and_tag(git_repo, "v1.0.0")
    write_file(git_repo, "src/pkg/core.py", "x = 2\n")
    write_file(git_repo, "MANIFEST.md", _MANIFEST_TEXT + "\nrevised\n")
    config = Config(
        root=git_repo,
        manifest=ManifestConfig(doc="MANIFEST.md", sources=["src/**/*.py"]),
    )
    findings, _ = check_manifest(config)
    assert findings == []


# --- versioned-files ------------------------------------------------------


def _commit_and_tag(root, tag):
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "x"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(root), "tag", tag], check=True, capture_output=True)


_VF_PATTERN = r'"v":\s*"([^"]+)"'


def test_versioned_files_fires_when_marker_unmoved(git_repo, write_file):
    write_file(git_repo, "data.json", '{"v": "1", "x": 1}')
    _commit_and_tag(git_repo, "v1.0.0")
    write_file(git_repo, "data.json", '{"v": "1", "x": 2}')  # content moved, marker not
    config = Config(
        root=git_repo,
        versioned_files=[VersionedFile(path="data.json", pattern=_VF_PATTERN)],
    )
    findings, _ = check_versioned_files(config)
    assert any("didn't move" in f.message for f in findings)


def test_versioned_files_clean_when_marker_moved(git_repo, write_file):
    write_file(git_repo, "data.json", '{"v": "1", "x": 1}')
    _commit_and_tag(git_repo, "v1.0.0")
    write_file(git_repo, "data.json", '{"v": "2", "x": 2}')  # marker moved with content
    config = Config(
        root=git_repo,
        versioned_files=[VersionedFile(path="data.json", pattern=_VF_PATTERN)],
    )
    findings, _ = check_versioned_files(config)
    assert findings == []


def test_versioned_files_clean_when_unchanged(git_repo, write_file):
    write_file(git_repo, "data.json", '{"v": "1", "x": 1}')
    _commit_and_tag(git_repo, "v1.0.0")
    config = Config(
        root=git_repo,
        versioned_files=[VersionedFile(path="data.json", pattern=_VF_PATTERN)],
    )
    findings, _ = check_versioned_files(config)
    assert findings == []


def test_versioned_files_notices_when_git_is_missing(tmp_path, write_file, monkeypatch):
    # With git unresolvable on PATH the check must skip with a notice, not crash.
    write_file(tmp_path, "data.json", '{"v": "1"}')
    monkeypatch.setenv("PATH", "")
    config = Config(
        root=tmp_path,
        versioned_files=[VersionedFile(path="data.json", pattern=_VF_PATTERN)],
    )
    findings, notices = check_versioned_files(config)
    assert findings == []
    assert any("git" in n.message for n in notices)


def test_versioned_files_skips_without_tag(git_repo, write_file):
    write_file(git_repo, "data.json", '{"v": "1"}')
    config = Config(
        root=git_repo,
        versioned_files=[VersionedFile(path="data.json", pattern=_VF_PATTERN)],
    )
    findings, notices = check_versioned_files(config)
    assert findings == []
    assert any("no git tag" in n.message for n in notices)
