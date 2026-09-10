# Changelog

All notable changes to shiplock are recorded here. This project follows
semantic versioning.

## [0.5.0] - 2026-09-09

### Added
- A third audit adapter, `google` (runs Google's Gemini CLI), alongside
  `anthropic` and `openai`. An `AUDIT_API_KEY` or `AUDIT_FALLBACK_API_KEY` of
  the form `google/<key>` now resolves to Gemini CLI, installed and driven by
  the gate. The gate restricts Gemini's built-in toolset to read-only tools for
  the run (its default set includes shell and file writes), and maps its
  `--output-format json` token stats to the shared usage keys so `rates` prices
  the run like the others. Gemini isn't given a scoped write tool, so a Gemini
  primary that dies mid-run restarts on the fallback rather than continuing from
  a progress log. Adding it was one registry row plus its invocation block, the
  extension point 0.4.0's adapter registry opened; no consumer input changes.
- The shared `audit-effort` input now maps to Gemini's `thinkingLevel`
  (`low`/`medium`/`high`) and to Codex's `model_reasoning_effort` as well as
  Claude's `--effort`, so one input sets reasoning effort across all three
  adapters. Empty leaves each its own default (Gemini 3 defaults to high).

### Fixed
- The `openai`/Codex adapter no longer masks its own exit code. The mapper that
  reshapes Codex's output into the shared envelope always exits clean, so a
  Codex primary that died after writing partial output reported success and the
  gate never failed over. The branch now captures Codex's exit and returns it,
  matching the Gemini branch's fix; the mapper also tolerates a missing
  last-message file from a failed run.

## [0.4.0] - 2026-09-09

### Added
- A `needs-import` command: `shiplock needs-import [path]` prints `true` or
  `false` for whether the repo's config makes any check import the checked
  package. Only `version` and `coverage` introspect the package; a repo that
  configures neither prints `false`. It fails safe to `false` (exit 0) on a
  missing path or a broken config, so a CI step can read a usable value and
  let the following `shiplock check` surface the error.

### Changed
- The reusable gate installs the checked-out repo only when
  `shiplock needs-import` reports `true`. An app repo that configures no
  `version` or `coverage` check now runs the gate without a `pyproject.toml`
  or any packaging, where before both jobs' unconditional `pip install .`
  failed on a repo with nothing to install. Consumers pinned to `@main` pick
  this up on their next run; no input changes.

## [0.3.1] - 2026-09-08

### Fixed
- `test-assertions` no longer reports a test whose expectation lives in a
  shared helper. It now follows a call into any function elsewhere in the
  test tree, resolving same-module definitions, imports in every form
  (relative, absolute, renamed, module-attribute), and `conftest.py`, and
  recursing through chains of helpers. A call resolving outside the test
  tree is the code under test and still counts as no expectation, so a bare
  constructor call or an implicit didn't-raise is reported as before. Over a
  720-test suite this took the check from ten findings to five: the five
  cleared all routed through one `pytest.raises` helper, the five kept were
  each assertion-free in fact, and no new finding appeared.

## [0.3.0] - 2026-09-06

### Added
- Two deterministic checks. `deps-declared-once` (a new `[deps]` section) flags
  a package declared in both `pyproject.toml` and a requirements file, since
  two declarations of one dependency drift apart the day one of them is
  edited. `test-assertions` (a new `[tests]` section) flags a test function
  carrying no expectation: no `assert`, no `raises`/`warns` context, no
  `assert_*` call. Both skip with a notice when their section is absent.
- An advisory ablation prompt, printed by `shiplock prompt ablation`: it asks
  an agent to report, with measured evidence, what the repo could remove,
  merge, or make cheaper, split into mechanical folds and owner decisions,
  ending in a do-first ordering rather than a verdict line. `shiplock prompt`
  unchanged; `shiplock prompt audit` names the default explicitly.

### Changed
- The semantic audit prompt gains three questions (docstring claims held
  against callers, documented-but-unreachable features, packaging claims held
  against packaging config) and widens the twin-surfaces question to toolchain
  files: lock files, second environments, and requirements files the docs
  never mention.

### Fixed
- `TestsConfig` no longer triggers pytest's collection warning when imported
  into a test module (pytest read the `Test*` prefix as a test class).

## [0.2.0] - 2026-09-03

### Changed
- The `audit-effort` default is now `high`, in both `gate.yml` and shiplock's
  own `release-gate.yml`. In a planted-defect comparison (five doc-only
  defects seeded on one commit, audited once per tier), `low` and `medium`
  each reported three of the five while `high` reported all five, at $0.64
  against `medium`'s $0.52. Every tier still returned `AUDIT: FAIL` on that
  commit, so the higher default buys a more complete finding list per run
  rather than a stricter verdict.

## [0.1.0] - 2026-09-02

### Changed
- `gate.yml`'s `audit-effort` input now defaults to `medium` instead of the
  Claude Code CLI's own default (`high`), with guidance in the input
  description and USAGE.md to raise it to `high` or `xhigh` if the audit
  keeps missing drift a manual review would catch. Shiplock's own
  `release-gate.yml` defaults its own dispatches to `low`, provisionally,
  based on a same-repo comparison where `low` and `medium` agreed.

## [0.0.1] - 2026-09-01

### Added
- Nine deterministic docs-vs-code checks: `docs-exist`, `banned-words`,
  `internal-refs`, `readme-links`, `version`, `architecture`, `coverage`,
  `manifest`, and `versioned-files`.
- `shiplock check [path]`, running the deterministic checks over a repo, with a
  contractual exit code (0 clean, 1 findings, 2 config or usage error). Without
  a `shiplock.toml` it runs a default pass over the docs it recognizes, so a
  first run needs no setup; `--json` emits the report as one machine-readable
  object.
- `shiplock prompt`, printing the semantic audit prompt for a fresh agent, ended
  by a machine-greppable `AUDIT: PASS` / `AUDIT: FAIL` verdict line.
- A Python API: `load_config`, `run_checks`, and the `Config`, `Report`,
  `Finding`, `Notice`, and `ConfigError` types.
- Plain-sentence CLI errors with a fuzzy suggestion for a mistyped command, and
  category color on a terminal (findings red, clean green), off when piped or
  under `NO_COLOR`.
- A mutation check (`scripts/mutation_check.py`, run as a CI job) that breaks
  each check in turn and requires its own test to fail.
- A reusable GitHub Actions workflow (`gate.yml`, `on: workflow_call`) that runs
  both layers in CI, opens an issue on an audit failure, reports each audit's
  token usage and a [rates](https://pypi.org/project/rates/)-priced USD cost
  in the job summary, runs the audit through the agent CLI of the provider
  named in the `provider/key` audit secret (anthropic or openai), takes an
  optional `audit-effort` input setting Claude's reasoning effort, skips the
  audit with a warning when no key secret is set, and can continue an
  interrupted audit on a fallback key from a second provider via the audit's
  own progress log.
- A PyPI release workflow (`release.yml`) publishing via trusted publishing when
  a GitHub Release is published, behind a reviewer-gated environment.
- Shiplock as consumer zero: its own `shiplock.toml`, run over the shiplock repo.

[0.5.0]: https://github.com/shehuphd/shiplock/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/shehuphd/shiplock/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/shehuphd/shiplock/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/shehuphd/shiplock/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/shehuphd/shiplock/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shehuphd/shiplock/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/shehuphd/shiplock/releases/tag/v0.0.1
