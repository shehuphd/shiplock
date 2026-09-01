#!/usr/bin/env python3
"""Mutation check: break each deterministic check, confirm its own test fails.

For every check, this applies a targeted edit that neuters it, runs the one test
written to catch that check, and requires the test to FAIL. A mutation that
leaves its test green means the test guards nothing. Sources are restored in a
finally block, so an interrupted run still leaves the tree as it found it.

Run from anywhere:

    python scripts/mutation_check.py

Exits 0 only when every mutation is caught. The CI ``mutation`` job runs this on
every push, so the pass is a standing, verifiable artifact rather than a claim.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / "src" / "shiplock" / "_checks.py"
STYLE = ROOT / "src" / "shiplock" / "_style.py"
PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly"]

# (file, anchor, mutated, test node). Each anchor must appear once.
MUTATIONS = [
    (CHECKS, "if not (config.root / rel).is_file():",
     "if False and not (config.root / rel).is_file():",
     "tests/test_checks.py::test_docs_exist_fires_on_missing_doc"),
    (STYLE, "hits.append(BannedHit(line=i, word=match.group(1).lower()))",
     "pass",
     "tests/test_style.py::test_whole_word_is_a_hit"),
    (CHECKS, "if pattern.search(line):",
     "if False and pattern.search(line):",
     "tests/test_checks.py::test_internal_refs_fires_on_project_folder"),
    (CHECKS, "if not _is_absolute_link(target):",
     "if False and not _is_absolute_link(target):",
     "tests/test_checks.py::test_readme_links_fires_on_relative_link"),
    (CHECKS, "elif dunder != project_version:",
     "elif False and dunder != project_version:",
     "tests/test_checks.py::test_version_fires_on_mismatch"),
    (CHECKS, "if not _mentions(text, module_name):",
     "if False and not _mentions(text, module_name):",
     "tests/test_checks.py::test_architecture_fires_on_unnamed_module"),
    (CHECKS, "if not _mentions(text, member):",
     "if False and not _mentions(text, member):",
     "tests/test_checks.py::test_coverage_fires_on_undocumented_enum_member"),
    (CHECKS, "if then is not None and now.group(1) == then.group(1):",
     "if False and then is not None and now.group(1) == then.group(1):",
     "tests/test_checks.py::test_versioned_files_fires_when_marker_unmoved"),
    (CHECKS, "if not _manifest_lists(text, rel, path.name):",
     "if False and not _manifest_lists(text, rel, path.name):",
     "tests/test_checks.py::test_manifest_fires_on_unlisted_source_file"),
]


def main() -> int:
    originals = {path: path.read_text() for path in (CHECKS, STYLE)}
    all_caught = True
    try:
        for path, anchor, mutated, node in MUTATIONS:
            text = path.read_text()
            if text.count(anchor) != 1:
                print(f"BAD  {node.split('::')[-1]}: anchor not unique ({anchor!r})")
                all_caught = False
                continue
            path.write_text(text.replace(anchor, mutated))
            result = subprocess.run(
                PYTEST + [node], cwd=ROOT, capture_output=True, text=True
            )
            path.write_text(originals[path])  # restore before judging
            caught = result.returncode != 0
            label = "caught" if caught else "SURVIVED"
            print(f"{'OK ' if caught else 'BAD'} {node.split('::')[-1]}  {label}")
            all_caught = all_caught and caught
    finally:
        for path, text in originals.items():
            path.write_text(text)

    print("\nALL MUTATIONS CAUGHT" if all_caught else "\nSOME MUTATIONS SURVIVED")
    return 0 if all_caught else 1


if __name__ == "__main__":
    sys.exit(main())
