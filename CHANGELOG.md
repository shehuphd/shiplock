# Changelog

All notable changes to shiplock are recorded here. This project follows
semantic versioning.

## [Unreleased]

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
  both layers in CI, opens an issue on an audit failure, reports each
  audit's token usage and cost estimate in the job summary, and can resume an
  interrupted audit under a fallback key from a second billing account.
- A PyPI release workflow (`release.yml`) publishing via trusted publishing when
  a GitHub Release is published, behind a reviewer-gated environment.
- Shiplock as consumer zero: its own `shiplock.toml`, run over the shiplock repo.

[Unreleased]: https://github.com/shehuphd/shiplock
