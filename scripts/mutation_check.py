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
SCAN = ROOT / "src" / "shiplock" / "_scan.py"
INIT = ROOT / "src" / "shiplock" / "_init.py"
RULES = ROOT / "src" / "shiplock" / "_rules.py"
SUGGEST = ROOT / "src" / "shiplock" / "_suggest.py"
JUDGE = ROOT / "src" / "shiplock" / "_judge.py"
PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly"]

# (file, anchor, mutated, test node). Each anchor must appear once.
MUTATIONS = [
    (CHECKS, "if not (config.root / rel).is_file():",
     "if False and not (config.root / rel).is_file():",
     "tests/test_checks.py::test_docs_exist_fires_on_missing_doc"),
    (STYLE, "hits.append(BannedHit(line=i, word=match.group(1).lower()))",
     "pass",
     "tests/test_style.py::test_whole_word_is_a_hit"),
    (SCAN, "if ref.pattern.search(probe):",
     "if False and ref.pattern.search(probe):",
     "tests/test_checks.py::test_internal_refs_fires_on_declared_folder"),
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
    (CHECKS, "if requirement in declared and requirement not in exempt:",
     "if False and requirement in declared and requirement not in exempt:",
     "tests/test_checks.py::test_deps_fires_when_a_requirement_duplicates_pyproject"),
    (CHECKS, "if not _has_expectation(func, module, index):",
     "if False and not _has_expectation(func, module, index):",
     "tests/test_checks.py::test_test_assertions_fires_on_a_test_with_no_expectation"),
    (SCAN, "self.findings.extend(ref_findings(self.check, self.rules, rel, i, probe, self.tally))",
     "pass",
     "tests/test_scan.py::test_scan_reads_tracked_files_not_only_docs"),
    (SCAN, "for cn in self.rules.code_names",
     "for cn in []",
     "tests/test_scan.py::test_scan_fires_on_code_name"),
    (SCAN, "if tracked is not None and rel is not None and rel in tracked:",
     "if False:",
     "tests/test_scan.py::test_scan_refuses_a_tracked_rules_file"),
    (SCAN, 'return value[0] + "*" * (len(value) - 1)',
     "return value",
     "tests/test_scan.py::test_scan_masks_the_matched_string"),
    (SCAN, 'line = line.replace(substring, " " * len(substring))',
     "line = line",
     "tests/test_scan.py::test_scan_exempts_a_configured_blocklist_path_from_refs"),
    (SCAN, "if scope is not None and self.rules.allows(value, rel):",
     "if False:",
     "tests/test_scan.py::test_allowed_code_name_never_fails_and_reports_a_masked_count"),
    (SCAN, "        if self.private:",
     "        if False:",
     "tests/test_scan.py::test_private_ref_label_is_masked"),
    (SCAN, "line_exempt = self.config_exempt if rel == CONFIG_FILENAME else self.exempt",
     "line_exempt = self.exempt",
     "tests/test_scan.py::test_shiplock_toml_never_flags_its_own_declarations"),
    (SCAN, "        return rel is not None and _excluded(rel, list(scope))",
     "        return True",
     "tests/test_scan.py::test_scoped_allow_outside_its_paths_is_a_finding_naming_the_paths"),
    (SCAN, "        for glob in scope:",
     "        for glob in ():",
     "tests/test_scan.py::test_scoped_allow_glob_matching_no_tracked_file_is_a_warning"),
    (SCAN, '            scanner.line(f"commit {short}", None, line)',
     "            pass",
     "tests/test_scan.py::test_range_scan_reads_commit_messages"),
    (SCAN, '    if os.environ.get(NO_USER_RULES_ENV):',
     '    if True:',
     "tests/test_scan.py::test_user_rules_file_is_read_in_any_repo"),
    (CHECKS, "    return run_scan(config, config.scan or default_scan_config())",
     '    return run_scan(config, config.scan) if config.scan else ([], [])',
     "tests/test_scan.py::test_check_scan_runs_without_section_when_a_rules_file_exists"),
    (INIT, "    if path.exists():\n        return (",
     "    if False:\n        return (",
     "tests/test_init.py::test_init_is_idempotent_and_never_overwrites"),
    (INIT, "        if local.returncode != 0:",
     "        if False:",
     "tests/test_init.py::test_init_never_writes_into_a_shared_hooks_path"),
    (INIT, "    return 1\n  fi",
     "    return 0\n  fi",
     "tests/test_init.py::test_hook_fails_closed_when_shiplock_cannot_be_found"),
    (RULES, "        if has_foreign_comments(text):",
     "        if False:",
     "tests/test_rules.py::test_save_refuses_to_drop_a_persons_comments_unless_rewrite"),
    (RULES, '            ["gh", "secret", "set", SECRET_NAME],\n            input=document,',
     '            ["gh", "secret", "set", SECRET_NAME, document],\n            input="",',
     "tests/test_rules.py::test_push_secret_sends_the_document_on_stdin_only"),
    (SUGGEST, "        if _is_tool_dir(name):\n            continue",
     "        if False:\n            continue",
     "tests/test_suggest.py::test_suggests_ignored_folders_with_mention_counts"),
    (CHECKS, "                if _anchor_resolves(index, kind, token):\n                    continue",
     "                if True:\n                    continue",
     "tests/test_judge.py::test_doc_anchors_fires_on_an_undeclared_flag_env_and_key"),
    (CHECKS, "                if _defaults_agree(stated, actual):\n                    continue",
     "                if True:\n                    continue",
     "tests/test_judge.py::test_doc_defaults_fires_on_a_wrong_default_and_accepts_phrasing"),
    (JUDGE, "                if status in (429, 500, 502, 503, 504, 529) and attempt < self.retries:",
     "                if status in (403, 429, 500, 502, 503, 504, 529) and attempt < self.retries:",
     "tests/test_judge.py::test_typesafe_adapter_retries_429_and_never_403"),
    (JUDGE, "        if p < COVERAGE_WARN:",
     "        if False:",
     "tests/test_judge.py::test_run_judge_warns_on_uncovered_surface_and_collects_leads"),
    (JUDGE, "    if drift:",
     "    if False:",
     "tests/test_judge.py::test_run_judge_flags_model_drift"),
    (CHECKS, "    if not banned:",
     "    if False:",
     "tests/test_checks.py::test_banned_words_skips_when_no_words_declared"),
]


def main() -> int:
    originals = {path: path.read_text() for path in (CHECKS, STYLE, SCAN, INIT, RULES, SUGGEST, JUDGE)}
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
