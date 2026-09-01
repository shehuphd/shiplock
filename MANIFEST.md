# Manifest

Last updated: 2026-09-01 11:19:46 UTC

Every current source file, what it does, and what it touches. A map for a
reader orienting in the codebase, kept current in the same change that adds,
removes, renames, or repurposes a file.

## Package (`src/shiplock/`)

| File | What it does |
|---|---|
| `__init__.py` | Public API re-exports (`load_config`, `run_checks`, `Config`, `Report`, `Finding`, `Notice`, `ConfigError`) and `__version__`. |
| `cli.py` | Command-line entry point. `main()` parses arguments (`check [path] [--json]`, `prompt`, bare welcome), falls back to the zero-config default run when no `shiplock.toml` exists, translates argparse errors to sentences with fuzzy suggestions, colors the finding/clean categories on a tty (`NO_COLOR` honored), renders findings to stdout and notices to stderr, owns the exit-code contract (0/1/2). Reads `prompts/audit.md` via `importlib.resources`. |
| `_config.py` | Loads and validates `shiplock.toml` into frozen dataclasses (`Config`, `DocsConfig`, `StyleConfig`, `VersionConfig`, `ArchitectureConfig`, `ManifestConfig`, `CoverageEntry`, `VersionedFile`); `default_config()` builds the zero-config run from detected doc names. Raises `ConfigError` on anything malformed. Reads the filesystem only. |
| `_checks.py` | The nine check functions (`docs-exist`, `banned-words`, `internal-refs`, `readme-links`, `version`, `architecture`, `coverage`, `manifest`, `versioned-files`) and `run_checks`. Reads repo files; shells out to `git` for `versioned-files` and the manifest staleness compare; calls `_introspect` for `version` and `coverage`. |
| `_report.py` | Result types: `Finding` (a disagreement, fails the run), `Notice` (a skip with its reason), `Report` (both, plus `ok`). |
| `_style.py` | The banned-word list (`BANNED_WORDS`) and the word-boundary, case-insensitive matcher (`find_banned`, `effective_words`). Excluded from shiplock's own sweep since it must name the words. |
| `_introspect.py` | `introspect()`: runs a subprocess that binds `sys.path` to the checked root, imports the target package there, confirms it resolved under root, and returns `__version__`, `__all__`, enum members, or callable parameters as JSON. Captures the child's stdout during imports so a package that prints can't corrupt the result. |
| `prompts/audit.md` | The semantic audit prompt (package data). Instructs a fresh agent to audit docs against code from state and end with an `AUDIT: PASS` / `AUDIT: FAIL` verdict line. |
| `py.typed` | PEP 561 marker so type checkers read the package's annotations. |

## Tests (`tests/`)

| File | What it does |
|---|---|
| `conftest.py` | Shared fixtures (`write_file`, `temp_module`, `git_repo`) and the per-run artifact writer (sorted CSV under `.test-runs/`). |
| `test_config.py` | Config loader: malformed inputs raise `ConfigError`; a valid file parses. |
| `test_style.py` | Banned-word matcher: word-boundary edges first, then hits. |
| `test_checks.py` | Each check's failing cases, skip cases, and clean case; `manifest` and `versioned-files` against a live temp git repo. |
| `test_introspect.py` | Introspection binds to the checked root: under-root reads, outside-root flagged, import errors as statuses, stdout-printing packages tolerated. |
| `test_cli.py` | Usage errors as a person would hit them (typos, unknown flags, bad paths), the zero-config default run, `--json` shape, color discipline when piped, exit-code contract, welcome, prompt verdict lines. |
| `test_docs.py` | Consumer zero: runs the full gate over this repo and fails on any finding. |

## Scripts and CI

| File | What it does |
|---|---|
| `scripts/mutation_check.py` | Breaks each check in turn and requires its own test to fail; restores sources in a finally block. Run by the CI `mutation` job. |
| `.github/workflows/gate.yml` | Reusable release gate (`workflow_call`): `check` job runs the deterministic checks, `audit` job runs the semantic audit through the Claude Code CLI and opens an issue on failure. |
| `.github/workflows/tests.yml` | Pytest across Python 3.10–3.13 plus the `mutation` job, on push and PR to main. |
| `.github/workflows/release-gate.yml` | Shiplock consuming its own `gate.yml` (consumer zero); `workflow_dispatch` only until a manual run passes. |
| `.github/workflows/release.yml` | Publishes to PyPI via trusted publishing when a GitHub Release is published; waits on the `release` environment's required-reviewer approval. |
| `.github/dependabot.yml` | Weekly `github-actions` and `pip` version updates. |

## Repo root

| File | What it does |
|---|---|
| `pyproject.toml` | Package metadata, build config, test extras, pytest options. |
| `shiplock.toml` | Shiplock's own gate config: doc surfaces, sweep globs, version package, architecture doc, coverage entries. |
| `MANIFEST.in` | Prunes `tests/` from the sdist; the suite runs from the repo. |
| `.gitignore` | Excludes the internal planning folder, assistant tool directories, the venv, build artifacts, and the per-run test CSVs. |
| `README.md` / `USAGE.md` / `ARCHITECTURE.md` / `CHANGELOG.md` | Public docs: quick start, full manual, structure and components, release history. |
| `LICENSE` | MIT. |
