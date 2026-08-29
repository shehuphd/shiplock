"""Introspection binds to the checked root, not the environment."""

from __future__ import annotations

from shiplock._introspect import introspect


def test_introspect_reads_a_package_under_root(tmp_path):
    pkg = tmp_path / "demo_bind"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    result = introspect(tmp_path, [{"id": "v", "op": "version", "module": "demo_bind"}])
    assert result["v"]["status"] == "ok"
    assert result["v"]["version"] == "9.9.9"


def test_introspect_flags_a_module_resolving_outside_root(tmp_path):
    # json imports fine, but its source is the stdlib, not under this root.
    result = introspect(tmp_path, [{"id": "j", "op": "version", "module": "json"}])
    assert result["j"]["status"] == "not_under_root"


def test_introspect_reports_import_error_as_a_status(tmp_path):
    result = introspect(tmp_path, [{"id": "x", "op": "version", "module": "no_such_xyz"}])
    assert result["x"]["status"] == "error"
