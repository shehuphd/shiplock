"""Dogfood: shiplock's own docs must pass shiplock's own gate.

This is consumer zero wired into the suite. If a doc drifts from the code, this
test fails with the same findings a consumer would see.
"""

from __future__ import annotations

from pathlib import Path

from shiplock import load_config, run_checks

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_shiplock_passes_its_own_gate():
    report = run_checks(load_config(REPO_ROOT))
    assert report.ok, [f"{f.check} {f.location()} {f.message}" for f in report.findings]
