# Changelog

All notable changes to shiplock are recorded here. This project follows
semantic versioning.

## [Unreleased]

### Added
- Eight deterministic docs-vs-code checks: `docs-exist`, `banned-words`,
  `internal-refs`, `readme-links`, `version`, `architecture`, `coverage`, and
  `versioned-files`.
- `shiplock check`, running the deterministic checks over a repo from a
  `shiplock.toml` config, with a contractual exit code (0 clean, 1 findings,
  2 config or usage error).
- `shiplock prompt`, printing the semantic audit prompt for a fresh agent, ended
  by a machine-greppable `AUDIT: PASS` / `AUDIT: FAIL` verdict line.
- A Python API: `load_config`, `run_checks`, and the `Config`, `Report`,
  `Finding`, `Notice`, and `ConfigError` types.
- A reusable GitHub Actions workflow (`gate.yml`, `on: workflow_call`) that runs
  both layers in CI and opens an issue on an audit failure.
- Shiplock as consumer zero: its own `shiplock.toml`, run over the shiplock repo.

[Unreleased]: https://github.com/shehuphd/shiplock
