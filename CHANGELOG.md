# Changelog

All notable changes to shiplock are recorded here. This project follows
semantic versioning.

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

[0.1.0]: https://github.com/shehuphd/shiplock/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/shehuphd/shiplock/releases/tag/v0.0.1
