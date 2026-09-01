# Architecture

How shiplock is put together. The package is small and dependency-free by
design: standard library only, with the `tomli` backport pulled in on Python
3.10 alone.

## Project structure

```
shiplock/
├── src/shiplock/
│   ├── __init__.py       # public API re-exports and __version__
│   ├── cli.py            # command-line entry point (check, prompt)
│   ├── _config.py        # shiplock.toml loader and typed config model
│   ├── _checks.py        # the nine checks and the runner
│   ├── _report.py        # Finding, Notice, Report result types
│   ├── _style.py         # the banned-word list and matcher
│   ├── _introspect.py    # subprocess introspection bound to the checked root
│   ├── py.typed          # PEP 561 marker
│   └── prompts/
│       └── audit.md      # the semantic audit prompt (shipped as package data)
├── .github/
│   ├── workflows/        # gate.yml (reusable), tests.yml, release-gate.yml, release.yml
│   └── dependabot.yml
├── scripts/
│   └── mutation_check.py # breaks each check to confirm its test guards it
├── tests/                # the suite (see MANIFEST.md for the per-file map)
├── shiplock.toml         # shiplock's own config (consumer zero)
├── pyproject.toml
├── MANIFEST.in           # prunes tests/ from the sdist
├── MANIFEST.md           # per-file map of the codebase
├── .gitignore
├── LICENSE
├── README.md
├── USAGE.md
├── ARCHITECTURE.md
└── CHANGELOG.md
```

## High-level flow

```
shiplock check
      │
      ▼
  cli.main ──► _config.load_config ──► Config
      │                                   │
      ▼                                   ▼
  _checks.run_checks ───────────────► Report(findings, notices)
      │                                   │
      ▼                                   ▼
  cli._render ──► stdout (findings) + stderr (notices, summary)
      │
      ▼
   exit 0 / 1 / 2
```

## Core components

| Module | Responsibility |
|---|---|
| `cli` | Parses arguments, dispatches `check` and `prompt`, renders the report, owns the exit-code contract. Greets a bare invocation, translates argparse errors into sentences with fuzzy command suggestions, and colors the finding/clean categories on a tty (`NO_COLOR` honored). |
| `_config` | Reads `shiplock.toml`, validates it, and returns a frozen `Config` of typed sections. Raises `ConfigError` on anything malformed. |
| `_checks` | Holds the nine check functions and `run_checks`, which calls them in a fixed order and folds their output into one report. |
| `_report` | Defines `Finding` (a disagreement), `Notice` (a skip with a reason), and `Report` (both, plus `ok`). |
| `_style` | Defines the house banned-word list and the word-boundary matcher. Carved out of shiplock's own sweep, since it has to name the words. |
| `_introspect` | Reads a package's `__version__`, `__all__`, enum members, and callable signatures in a subprocess that binds `sys.path` to the checked root, so `version` and `coverage` never read a stale installed copy. |

## The check registry

`_checks._CHECKS` is a fixed tuple of check functions, each taking a `Config`
and returning `(findings, notices)`. The order in that tuple is the order
findings are reported in. Adding a check means adding a function and one tuple
entry; nothing else in the runner changes.

The nine checks: `docs-exist`, `banned-words`, `internal-refs`,
`readme-links`, `version`, `architecture`, `coverage`, `manifest`,
`versioned-files`. Each is documented in [USAGE.md](USAGE.md).

## Data stores

None. Shiplock holds no state between runs. It reads a repo's files and git
metadata (via the `git` CLI, for the `versioned-files` and `manifest` checks)
and writes only to stdout and stderr.

## External integrations

- **git** — `versioned-files` shells out to `git describe` and `git show` to
  compare a data file against its content at the last reachable tag, and
  `manifest` uses `git diff` against the same tag to see whether sources moved
  without the manifest. Absent git or absent tags produce a notice, not a
  failure.
- **The consuming package** — `version` and `coverage` read the repo's own
  package (`__version__`, `__all__`, enum members, callable signatures) through
  `_introspect`, which runs a subprocess with the checked root's source
  prepended to `sys.path` and confirms the module resolved under root before
  reading it. A target that resolves to a copy outside root, or won't import,
  skips with a notice naming the fix rather than comparing the wrong code.

## Deployment

Published to PyPI as `shiplock`, MIT-licensed. The package ships `py.typed` and
`prompts/audit.md` as package data.

CI lives in `.github/workflows/`:

- `gate.yml` — the reusable gate (`on: workflow_call`). Job `check` runs the
  deterministic checks; job `audit` runs the semantic layer through the Claude
  Code CLI in JSON output mode, opens an issue on an `AUDIT: FAIL` verdict,
  fails closed when no verdict line is present, and writes each run's token
  usage and CLI cost estimate to the job summary (and the issue footer). Any
  repo consumes it with
  `uses: shehuphd/shiplock/.github/workflows/gate.yml@main`.
- `tests.yml` — the pytest suite across Python 3.10 through 3.13, plus a
  `mutation` job that runs `scripts/mutation_check.py`.
- `release-gate.yml` — shiplock calling its own `gate.yml` over itself (consumer
  zero). Ship-inactive: `workflow_dispatch` only until a manual run passes, then
  the push and pull-request triggers get uncommented.
- `release.yml` — publishes to PyPI via trusted publishing (OIDC) when a GitHub
  Release is published, behind the `release` environment's required-reviewer
  approval.

`dependabot.yml` keeps the action and pip versions current.

## Security considerations

Shiplock reads files and runs read-only git commands over a repo it's pointed
at. It executes no code from the repo beyond importing the declared package for
the `version` and `coverage` checks — the same import the repo's own test suite
performs — and that import runs in a separate subprocess (`_introspect`), so it
can't disturb the tool's own process or a caller's pytest session.

## Development and testing

Shiplock is consumer zero: its own `shiplock.toml` runs the checks over the
shiplock repo, wired into the test suite so the gate runs with every test.
Tests are adversarial-first (failing cases before happy paths). A committed
mutation check (`scripts/mutation_check.py`, run by the CI `mutation` job) breaks
each check in turn and confirms its own test fails, so a test that guards nothing
can't pass unnoticed.

## Future considerations

Known debt only, not a roadmap. The `internal-refs` pattern set is fixed; a repo
with a differently-named internal folder would need the pattern list widened.

## Glossary

| Term | Meaning |
|---|---|
| Finding | A concrete disagreement between a doc and the code. Fails the run. |
| Notice | A check that didn't run, with the reason stated. Doesn't fail the run. |
| Consumer zero | Shiplock checking itself with the package it ships. |
