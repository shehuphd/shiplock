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
