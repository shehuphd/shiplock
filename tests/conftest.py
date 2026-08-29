"""Shared fixtures and the per-run test artifact writer.

Every run drops a sorted CSV of (nodeid, outcome, duration) under
``.test-runs/`` (gitignored). Sorted so the file yields a stable diff across the
randomized orders pytest-randomly emits, and so a run is inspectable after the
fact rather than living only in scrollback.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

_results: list[tuple[str, str, float]] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call":
        _results.append((report.nodeid, report.outcome, round(report.duration, 4)))


def pytest_sessionfinish(session: pytest.Session) -> None:
    out = Path(str(session.config.rootdir)) / ".test-runs" / "latest.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["nodeid", "outcome", "duration"])
        writer.writerows(sorted(_results))


@pytest.fixture
def write_file():
    """Return a helper that writes text to ``root / relpath``, making parents."""

    def _write(root: Path, relpath: str, text: str) -> Path:
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def temp_module(tmp_path):
    """Create an importable package on sys.path, cleaned up after the test.

    Lets the version/coverage checks resolve a target by name without installing
    anything. Returns a factory: ``temp_module(name, source)`` -> name.
    """
    created: list[str] = []
    added_path = str(tmp_path)

    def _make(name: str, source: str) -> str:
        pkg = tmp_path / name
        pkg.mkdir()
        (pkg / "__init__.py").write_text(source, encoding="utf-8")
        if added_path not in sys.path:
            sys.path.insert(0, added_path)
        created.append(name)
        return name

    yield _make

    for name in created:
        sys.modules.pop(name, None)
    if added_path in sys.path:
        sys.path.remove(added_path)


@pytest.fixture
def git_repo(tmp_path):
    """An initialised git repo at a temp path, with identity configured."""

    def _run(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
        )

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    _run("config", "user.email", "test@example.com")
    _run("config", "user.name", "Test")
    return tmp_path
