# Manifest

Last updated: 2026-09-18 13:19:11 UTC

Every current source file, what it does, and what it touches. A map for a
reader orienting in the codebase, kept current in the same change that adds,
removes, renames, or repurposes a file.

## Package (`src/shiplock/`)

| File | What it does |
|---|---|
| `__init__.py` | Public API re-exports (`load_config`, `run_checks`, `Config`, `Report`, `Finding`, `Notice`, `ConfigError`) and `__version__`. |
| `cli.py` | Command-line entry point. `main()` parses arguments (`check [path] [--json]`, `scan [path] [--json]`, `prompt [audit\|ablation]`, `needs-import [path]`, `--version`, bare welcome), falls back to the zero-config default run when no `shiplock.toml` exists, translates argparse errors to sentences with fuzzy suggestions, colors the finding/clean categories on a tty (`NO_COLOR` honored), renders findings to stdout and notices to stderr, owns the exit-code contract (0/1/2). `needs-import` prints `true`/`false` for a CI gate to read before installing the checked repo. Reads the prompts via `importlib.resources`. |
| `_compat.py` | Version-guarded imports defined once: `tomllib` (stdlib on 3.11+, the `tomli` backport on 3.10), imported from here by every TOML-parsing module. |
| `_config.py` | Loads and validates `shiplock.toml` into frozen dataclasses (`Config`, `DocsConfig`, `StyleConfig`, `VersionConfig`, `ArchitectureConfig`, `ManifestConfig`, `CoverageEntry`, `VersionedFile`, `DepsConfig`, `TestsConfig`); `default_config()` builds the zero-config run from detected doc names. Raises `ConfigError` on an unknown top-level section, a wrong-typed value, or a section missing a required field. Reads the filesystem only. |
| `_checks.py` | The twelve check functions (`docs-exist`, `banned-words`, `internal-refs`, `readme-links`, `scan`, `version`, `architecture`, `coverage`, `manifest`, `versioned-files`, `deps-declared-once`, `test-assertions`), `run_checks`, and `needs_import` (whether the config's checks import the repo's package: true when `version` names a package or any `coverage` entry exists, since only those introspect). Reads repo files; shells out to `git` for `versioned-files` and the manifest staleness compare; calls `_introspect` for `version` and `coverage`; parses the test tree with `ast` for `test-assertions`, resolving helper calls across modules; shares its internal-ref patterns with `_scan` and delegates the `scan` check to it. |
| `_scan.py` | The leak & internal-reference scan over the git-tracked set (`git ls-files`), behind `shiplock scan` and the opt-in `scan` check. Owns the house internal-ref patterns (`build_ref_patterns`, shared with `internal-refs`), the identity classes (code-names, home paths, personal emails) loaded from the caller's local gitignored blocklist files, output masking so a match never echoes, and an opt-in generic secret-pattern set. Refuses a git-tracked blocklist as a finding; skips binary files and the house-excluded `.gitignore`/`.gitattributes`. |
| `_report.py` | Result types: `Finding` (a disagreement, fails the run), `Notice` (a skip with its reason), `Report` (both, plus `ok`). |
| `_style.py` | The banned-word list (`BANNED_WORDS`) and the word-boundary, case-insensitive matcher (`find_banned`, `effective_words`). Excluded from shiplock's own sweep since it must name the words. |
| `_introspect.py` | `introspect()`: runs a subprocess that binds `sys.path` to the checked root, imports the target package there, confirms it resolved under root, and returns `__version__`, `__all__`, enum members, or callable parameters as JSON. Captures the child's stdout during imports so a package that prints can't corrupt the result. |
| `prompts/audit.md` | The semantic audit prompt (package data). Instructs a fresh agent to audit docs against code from state and end with an `AUDIT: PASS` / `AUDIT: FAIL` verdict line. |
| `prompts/ablation.md` | The advisory ablation prompt (package data). Instructs a fresh agent to report, with measured evidence, what the repo could remove, merge, or make cheaper, split into mechanical and decision findings. No verdict line; never a gate. |
| `py.typed` | PEP 561 marker so type checkers read the package's annotations. |

## Tests (`tests/`)

| File | What it does |
|---|---|
| `conftest.py` | Shared fixtures (`write_file`, `temp_module`, `git_repo`) and the per-run artifact writer (sorted CSV under `.test-runs/`). |
| `test_config.py` | Config loader: malformed inputs raise `ConfigError`; a valid file parses. |
| `test_style.py` | Banned-word matcher: word-boundary edges first, then hits. |
| `test_checks.py` | Each check's failing cases, skip cases, and clean case; `manifest` and `versioned-files` against a live temp git repo; `deps-declared-once` name canonicalization and line filtering; `test-assertions` expectation forms, helper resolution across import forms, and exemptions; `needs_import` true for version or coverage config, false for a docs-only app repo. |
| `test_scan.py` | The leak & internal-reference scan: each rule's fire and clean case over a live temp git repo (ref-patterns, code-names, home paths, personal emails, opt-in secrets), output masking, the git-tracked-blocklist refusal, the no-blocklist notice, house and configured excludes, binary-file skip, and the default-config scan a bare `shiplock scan` runs. |
| `test_introspect.py` | Introspection binds to the checked root: under-root reads, outside-root flagged, import errors as statuses, stdout-printing packages tolerated. |
| `test_cli.py` | Usage errors as a person would hit them (typos, unknown flags, bad paths, a mistyped prompt kind), the zero-config default run, `--json` shape, color discipline when piped, exit-code contract, welcome, the audit prompt's verdict lines and the ablation prompt's lack of one, the `needs-import` command's `true`/`false` output and its fail-safe `false` on a bad config or missing path. |
| `test_docs.py` | Consumer zero: runs the deterministic gate checks over this repo and fails on any finding. |
| `test_gate.py` | The gate workflow's shell orchestration: extracts the audit job's step scripts from `gate.yml` and runs them under bash against stub agent CLIs — key-format and provider validation, the provider↔model family check, the keycall key pre-flight, the catalog pick for an empty model input, the missing-key skip, case handling, the cross-provider failover continuation, the rates-priced usage table, and the Gemini adapter running read-only with its stats mapped to the usage keys; plus a structural check that both jobs gate `pip install .` on `shiplock needs-import`. |

## Scripts and CI

| File | What it does |
|---|---|
| `scripts/mutation_check.py` | Breaks each check in turn and requires its own test to fail; restores sources in a finally block. Run by the CI `mutation` job. |
| `.github/workflows/gate.yml` | Reusable release gate (`workflow_call`): `check` job runs the deterministic checks; both jobs install the checked-out repo only when `shiplock needs-import` reports `true`, so an app repo that configures no version or coverage check needn't be pip-installable; `audit` job reads the provider from the `provider/key` audit secret, runs the semantic audit through that provider's agent CLI (anthropic runs Claude Code, openai runs Codex, google runs Gemini CLI, restricted to read-only tools), refuses a model named in one provider's naming against another provider's key at configuration time, verifies each audit key through keycall before an attempt is spent, picks the model from the key's own live catalog when none is pinned, continues an interrupted run on an optional fallback key via the audit's progress log, skips the audit with a warning when no key secret is set, opens an issue on failure, and reports per-attempt token usage and a rates-priced USD cost in the job summary. |
| `.github/workflows/tests.yml` | Pytest across Python 3.10–3.13 plus the `mutation` job, on push and PR to main and by manual dispatch. |
| `.github/workflows/release-gate.yml` | Shiplock consuming its own `gate.yml` (consumer zero): deterministic checks on push and PR; the billable semantic audit only on manual dispatch. |
| `.github/workflows/release.yml` | Publishes to PyPI via trusted publishing when a GitHub Release is published; waits on the `release` environment's required-reviewer approval. |
| `.github/workflows/audit-eval.yml` | Dispatch-only harness that runs the gate's audit over this repo for one adapter, mapping an eval-key secret to the primary slot, kept apart from `release-gate.yml`. Used to eval a provider adapter end to end before it's trusted to gate. |
| `.github/dependabot.yml` | Weekly `github-actions` and `pip` version updates. |

## Repo root

| File | What it does |
|---|---|
| `pyproject.toml` | Package metadata, build config, test extras, pytest options. |
| `shiplock.toml` | Shiplock's own gate config: doc surfaces, sweep globs, version package, architecture doc, manifest sources, coverage entries, requirements globs, test globs. |
| `MANIFEST.in` | Prunes `tests/` from the sdist; the suite runs from the repo. |
| `.gitignore` | Excludes the internal planning folder, assistant tool directories, the venv, build artifacts, and the per-run test CSVs. |
| `README.md` / `USAGE.md` / `ARCHITECTURE.md` / `CHANGELOG.md` | Public docs: quick start, full manual, structure and components, release history. |
| `LICENSE` | MIT. |
