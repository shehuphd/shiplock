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

## How it reads a repo

Shiplock does nothing a repo hasn't declared. Every check reads its inputs from
`shiplock.toml`; a check whose section is absent prints a skip notice and moves
on. A skip is never a pass — the notice tells you the check didn't run, so an
undeclared surface can't hide behind a clean result.

## The config file

`shiplock.toml` lives at the repo root. Every section is optional. This is the
complete schema:

```toml
[docs]
public = ["README.md", "USAGE.md", "ARCHITECTURE.md", "CHANGELOG.md"]
changelog = "CHANGELOG.md"
readme = "README.md"

[style]
extra_banned = []                 # words to add to the house list
allow = []                        # house words to exempt in this repo
source_globs = ["src/**/*.py"]    # shipped sources swept for banned words
exclude = ["src/pkg/_words.py"]   # files carved out of the sweep

[version]
package = "pkg"                   # top-level import name

[architecture]
doc = "ARCHITECTURE.md"
source_dir = "src/pkg"
exempt = ["__init__"]

[[coverage]]
object = "pkg:ErrorCode"          # "module:Attr.path"; bare "module" for exports
doc = "USAGE.md"
kind = "enum"                     # enum | params | exports
exempt = []

[[versioned_files]]
path = "src/pkg/data.json"
pattern = '"data_version":\s*"([^"]+)"'   # one capture group
```

### Sections

| Section | Purpose |
|---|---|
| `[docs]` | The public docs. `public` lists them; `changelog` and `readme` name two of them for the checks that treat them specially. |
| `[style]` | Banned-word sweep inputs: words to add or exempt, which sources to sweep, which files to leave out. |
| `[version]` | The top-level import name, so the version check can read the package's `__version__`. |
| `[architecture]` | The architecture doc and the source directory whose modules it must name. |
| `[[coverage]]` | One entry per object whose members must appear in a doc. Repeatable. |
| `[[versioned_files]]` | One entry per data file that must move a version marker when its content changes. Repeatable. |

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

When a check can't run — a source directory that isn't there, a package that
won't import, no git tag to diff against — it prints a notice naming the reason
and the fix, and the run continues.

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
fails closed). Turn the audit off with `run-audit: false`; it needs the
`ANTHROPIC_API_KEY` secret when on.

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
