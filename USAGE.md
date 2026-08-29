# Shiplock usage

The full manual. For a one-minute overview, read the README first; this document
covers the config schema, every check, the exit codes, and the Python API.

## Install

```bash
pip install shiplock
```

Shiplock needs Python 3.10 or newer and has no other runtime dependency (the
`tomli` backport is pulled in only on 3.10, where `tomllib` isn't in the standard
library yet).

## Quick start

Add a `shiplock.toml` to your repo root, then run the checks:

```bash
shiplock check
```

Point it at a repo elsewhere with `--root`:

```bash
shiplock check --root ../some-other-repo
```

Print the semantic audit prompt for a fresh agent:

```bash
shiplock prompt
```

## Setup

Setup is one file: `shiplock.toml` at the repo root. Read this section once and
you'll know what to put in it.

### Two rules that make the rest obvious

1. **Shiplock runs only the checks you configure.** Every section is optional and
   independent. Declare a section and its check runs; leave it out and the check
   prints a skip notice and moves on. A skip is never a pass — the notice says
   plainly that the check didn't run, so an undeclared surface can't pass by
   staying silent.

2. **No file names are baked in.** The four-file doc set this project happens to
   use (`README.md`, `USAGE.md`, `ARCHITECTURE.md`, `CHANGELOG.md`) is one team's
   house convention, not a shiplock requirement. Shiplock requires none of them
   by name. Declare the docs you ship, under the names you use, and configure
   only the checks you want. A repo with just a `README.md` and no
   architecture doc simply omits `[architecture]`, and the architecture check
   skips.

### Start with one section

The smallest useful config checks a README for missing files and banned words:

```toml
[docs]
public = ["README.md"]
```

Run `shiplock check`: `docs-exist` and `banned-words` run over the README, and
every other check prints a skip notice. Grow the file one section at a time as
you want more coverage — nothing forces you to fill in the rest.

### What each section turns on

| Section | Turns on | Leave it out and |
|---|---|---|
| `[docs]` `public` | `docs-exist`, `banned-words`, `internal-refs` over your docs | those three don't run |
| `[docs]` `changelog` | changelog-aware banned-word scoping, and the changelog half of `version` | the changelog gets no special handling |
| `[docs]` `readme` | `readme-links` | the absolute-link check doesn't run |
| `[style]` | the source-code half of `banned-words` (`source_globs`) | only docs are swept, not source |
| `[version]` | `version` (pyproject vs `__version__` vs changelog) | version alignment isn't checked |
| `[architecture]` | `architecture` (every module named in the doc) | the module-list check doesn't run |
| `[[coverage]]` | `coverage` — one entry per documented object, repeatable | object coverage isn't checked |
| `[[versioned_files]]` | `versioned-files` — one entry per data file, repeatable | marker movement isn't checked |

### The complete config, annotated

Every section shiplock understands. Copy what you need and delete the rest:

```toml
[docs]
# The docs shiplock treats as public. Name the files you ship — this
# list is yours, not a fixed set.
public = ["README.md", "USAGE.md", "ARCHITECTURE.md", "CHANGELOG.md"]
changelog = "CHANGELOG.md"   # optional: enables changelog-aware checks
readme = "README.md"         # optional: enables readme-links

[style]                          # optional: omit to sweep docs only
extra_banned = []                # add words to the house list
allow = []                       # exempt house words in this repo
source_globs = ["src/**/*.py"]   # shipped sources swept for banned words
exclude = ["src/pkg/_words.py"]  # files carved out of the sweep

[version]                    # optional: omit to skip version alignment
package = "pkg"              # your top-level import name

[architecture]              # optional: omit if you keep no architecture doc
doc = "ARCHITECTURE.md"
source_dir = "src/pkg"
exempt = ["__init__"]        # module stems that need not be named in the doc

[[coverage]]                 # optional, repeatable: one table per documented object
object = "pkg:ErrorCode"     # "module:Attr.path", or a bare "module" for exports
doc = "USAGE.md"
kind = "enum"                # enum | params | exports
exempt = []

[[versioned_files]]          # optional, repeatable: one table per versioned data file
path = "src/pkg/data.json"
pattern = '"data_version":\s*"([^"]+)"'   # a regex with a single capture group
```

## The checks

Eight deterministic checks, run in this order:

| Check | Asserts |
|---|---|
| `docs-exist` | Every declared public doc exists on disk. |
| `banned-words` | The house banned-word list (word-boundary, case-insensitive) is absent from public docs and the configured source globs. The changelog is swept only above its first released-version heading. |
| `internal-refs` | No reference to an internal-only artifact appears in public docs: the gitignored planning folder, the coding-standards file, roadmap files, or assistant tool directories. A planning-folder path inside a `pypi.org` URL is carved out. |
| `readme-links` | Every markdown link in the README is absolute (`http`, `https`, `#`, or `mailto`), since PyPI resolves relative links against pypi.org. |
| `version` | The pyproject version equals the package's `__version__`, and the changelog carries a heading for that version or an `[Unreleased]` section. |
| `architecture` | Every top-level module and subpackage under the source directory is named in the architecture doc, or listed as exempt. |
| `coverage` | Every member of a declared object appears in its declared doc. Three kinds: `enum` (member names), `params` (a callable's parameter names), `exports` (a module's `__all__`). |
| `versioned-files` | A declared data file whose content differs from the last reachable git tag has moved its version marker. |

`version` and `coverage` learn about the code by introspecting the package in a
subprocess with the checked root's source on `sys.path`, and confirm the module
resolved under root before reading it — so they never compare against a stale
copy installed elsewhere. The package must be importable (its dependencies
present) for these two to run.

When a check can't run — a source directory that isn't there, a package that
won't import or resolves outside the root, no git tag to diff against — it prints
a notice naming the reason and the fix, and the run continues.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean: no check produced a finding. |
| 1 | One or more checks found a problem. |
| 2 | A config or usage error (no `shiplock.toml`, malformed TOML, a bad flag). |

Findings print to stdout; notices and the summary print to stderr, so stdout
stays clean for a pipe. This makes `shiplock check` both a CI step and a pytest
assertion.

## The semantic audit

`shiplock check` covers what a machine can decide with certainty. The second
layer is a prompt for a fresh agent to read the code and hold every doc claim
against it, from state rather than from what changed. Print it with:

```bash
shiplock prompt
```

The prompt ends with a machine-greppable verdict line, `AUDIT: PASS` or
`AUDIT: FAIL`, so a CI job can act on the outcome. The prompt ships inside the
package and is centrally versioned, so every repo picks up updates on the next
install rather than copying a snapshot.

## Use it in CI

Shiplock ships a reusable GitHub Actions workflow. Call it from your repo:

```yaml
name: release gate
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
permissions:
  contents: read
  issues: write
jobs:
  gate:
    uses: shehuphd/shiplock/.github/workflows/gate.yml@main
    with:
      shiplock-spec: "shiplock"   # the pip requirement for shiplock itself
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

The `check` job runs the deterministic checks on every push and pull request.
The `audit` job runs the semantic layer through the Claude Code CLI and opens an
issue if the audit returns `AUDIT: FAIL` (or produces no verdict line, which
fails closed).

### The audit key

The `audit` job calls the Claude API, so each consuming repo supplies its own
key. The `check` job needs nothing. To enable the audit:

1. Create an Anthropic API key at
   [platform.claude.com/settings/keys](https://platform.claude.com/settings/keys).
2. Add it to the consuming repo as a repository secret named `ANTHROPIC_API_KEY`,
   at `https://github.com/<owner>/<repo>/settings/secrets/actions` → **New
   repository secret**.

Don't want the audit? Set `run-audit: false` and skip the key; the deterministic
`check` job still runs.

Wire it in dormant first: start with `on: workflow_dispatch`, run it once by
hand, then switch to the push and pull-request triggers above once a manual run
passes.

## Python API

Everything the CLI does is callable. The public surface:

| Name | Role |
|---|---|
| `load_config` | Read and validate a repo's `shiplock.toml`, returning a `Config`. |
| `run_checks` | Run every check over a `Config`, returning a `Report`. |
| `Config` | The parsed config, rooted at a path. |
| `Report` | The result of a run: `findings`, `notices`, and `ok`. |
| `Finding` | One disagreement between a doc surface and the code. |
| `Notice` | One skipped check, with its reason. |
| `ConfigError` | Raised on a missing or malformed config. |

```python
from pathlib import Path
from shiplock import load_config, run_checks

report = run_checks(load_config(Path(".")))
if not report.ok:
    for finding in report.findings:
        print(finding.check, finding.location(), finding.message)
    raise SystemExit(1)
```

Wire it into pytest so the gate runs with the suite:

```python
from pathlib import Path
from shiplock import load_config, run_checks

def test_docs_match_code():
    report = run_checks(load_config(Path(__file__).parent.parent))
    assert report.ok, [f.message for f in report.findings]
```

By [Mo Shehu](https://mohammedshehu.com)
