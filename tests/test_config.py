"""Config loader: malformed inputs raise ConfigError; a valid file parses."""

from __future__ import annotations

import pytest

from shiplock._config import ConfigError, load_config


# --- malformed inputs must raise ------------------------------------------


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="No shiplock.toml"):
        load_config(tmp_path)


def test_malformed_toml_raises(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", "this is = = not toml")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(tmp_path)


def test_unknown_section_raises(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", "[nonsense]\nx = 1\n")
    with pytest.raises(ConfigError, match="unknown section"):
        load_config(tmp_path)


def test_bad_coverage_kind_raises(tmp_path, write_file):
    write_file(
        tmp_path,
        "shiplock.toml",
        '[[coverage]]\nobject = "m:X"\ndoc = "USAGE.md"\nkind = "bogus"\n',
    )
    with pytest.raises(ConfigError, match="kind is 'bogus'"):
        load_config(tmp_path)


def test_wrong_type_raises(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", '[docs]\npublic = "README.md"\n')
    with pytest.raises(ConfigError, match="must be a list of strings"):
        load_config(tmp_path)


def test_architecture_requires_both_keys(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", '[architecture]\ndoc = "ARCHITECTURE.md"\n')
    with pytest.raises(ConfigError, match="requires both"):
        load_config(tmp_path)


def test_manifest_remind_with_doc_raises(tmp_path, write_file):
    write_file(
        tmp_path, "shiplock.toml", '[manifest]\ndoc = "MANIFEST.md"\nremind = false\n'
    )
    with pytest.raises(ConfigError, match="remind only applies"):
        load_config(tmp_path)


def test_manifest_sources_without_doc_raises(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", '[manifest]\nsources = ["src/**/*.py"]\n')
    with pytest.raises(ConfigError, match="require 'doc'"):
        load_config(tmp_path)


# --- a valid file parses ---------------------------------------------------


def test_valid_config_parses(tmp_path, write_file):
    write_file(
        tmp_path,
        "shiplock.toml",
        """
[docs]
public = ["README.md"]
readme = "README.md"

[[coverage]]
object = "pkg:ErrorCode"
doc = "USAGE.md"
kind = "enum"
""",
    )
    config = load_config(tmp_path)
    assert config.docs is not None
    assert config.docs.public == ["README.md"]
    assert len(config.coverage) == 1
    assert config.coverage[0].kind == "enum"
    assert config.coverage[0].target == "pkg:ErrorCode"


def test_deps_without_requirements_is_rejected(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", "[deps]\nexempt = []\n")
    with pytest.raises(ConfigError, match="requirements"):
        load_config(tmp_path)


def test_deps_requirements_must_be_strings(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", "[deps]\nrequirements = [1]\n")
    with pytest.raises(ConfigError, match="list of strings"):
        load_config(tmp_path)


def test_tests_without_globs_is_rejected(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", "[tests]\nexempt = []\n")
    with pytest.raises(ConfigError, match="globs"):
        load_config(tmp_path)


def test_deps_and_tests_sections_parse(tmp_path, write_file):
    write_file(
        tmp_path,
        "shiplock.toml",
        '[deps]\nrequirements = ["requirements*.txt"]\nexempt = ["setuptools"]\n\n'
        '[tests]\nglobs = ["tests/**/*.py"]\nexempt = ["test_smoke"]\n',
    )
    config = load_config(tmp_path)
    assert config.deps is not None
    assert config.deps.requirements == ["requirements*.txt"]
    assert config.deps.exempt == ["setuptools"]
    assert config.tests is not None
    assert config.tests.globs == ["tests/**/*.py"]
    assert config.tests.exempt == ["test_smoke"]


# --- [scan] section --------------------------------------------------------


def test_scan_secrets_must_be_boolean(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", '[scan]\nsecrets = "yes"\n')
    with pytest.raises(ConfigError, match="secrets must be a boolean"):
        load_config(tmp_path)


def test_scan_extra_refs_needs_label_and_pattern(tmp_path, write_file):
    write_file(
        tmp_path, "shiplock.toml", '[[scan.extra_refs]]\npattern = "X-\\\\d+"\n'
    )
    with pytest.raises(ConfigError, match="missing required key 'label'"):
        load_config(tmp_path)


def test_scan_extra_refs_rejects_a_bad_regex(tmp_path, write_file):
    write_file(
        tmp_path,
        "shiplock.toml",
        '[[scan.extra_refs]]\nlabel = "bad"\npattern = "([unclosed"\n',
    )
    with pytest.raises(ConfigError, match="not a valid regex"):
        load_config(tmp_path)


def test_scan_blocklist_accepts_a_bare_string(tmp_path, write_file):
    write_file(tmp_path, "shiplock.toml", '[scan]\nblocklist = "~/.secrets.toml"\n')
    config = load_config(tmp_path)
    assert config.scan is not None
    assert config.scan.blocklist == ["~/.secrets.toml"]


def test_scan_section_parses(tmp_path, write_file):
    write_file(
        tmp_path,
        "shiplock.toml",
        '[scan]\nblocklist = ["a.toml", "b.toml"]\nsecrets = true\n'
        'exclude = ["vendor/**"]\n\n'
        '[[scan.extra_refs]]\nlabel = "ticket"\npattern = "ACME-\\\\d+"\n',
    )
    config = load_config(tmp_path)
    assert config.scan is not None
    assert config.scan.blocklist == ["a.toml", "b.toml"]
    assert config.scan.secrets is True
    assert config.scan.exclude == ["vendor/**"]
    assert config.scan.extra_refs[0].label == "ticket"
    assert config.scan.extra_refs[0].pattern == "ACME-\\d+"
