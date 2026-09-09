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

Point the checks at any repo — no config needed:

```bash
shiplock check path/to/repo
```

The path can be relative or absolute, and defaults to the current directory
(`shiplock check` inside a repo). With no `shiplock.toml` present, shiplock runs
its default pass: the docs it recognizes by name (`README.md`, `USAGE.md`,
`ARCHITECTURE.md`, `CHANGELOG.md`, `MANIFEST.md`, `CONTRIBUTING.md` — whichever
exist) get swept for missing files, banned words, internal references, and
relative README links, and a note on stderr says the run used defaults. The
checks that need declarations skip with a notice each.

Add `--json` for one machine-readable object on stdout instead of the human
rendering:

```bash
shiplock check path/to/repo --json
```

Print the semantic audit prompt for a fresh agent:

```bash
shiplock prompt
```

One more command, meant for CI rather than day-to-day use:

```bash
shiplock needs-import path/to/repo
```

It prints `true` or `false` for whether the repo's config makes any check
import the package (only `version` and `coverage` do). The path defaults to the
current directory. The gate reads it to decide whether to install the repo
before checking it; see [Use it in CI](#use-it-in-ci). It exits 0 either way,
and prints `false` on a missing path or a broken config so a CI step reads a
usable value rather than an error.

`shiplock --version` prints the installed version, and `shiplock` alone prints a
short welcome with these commands.

## Setup

Configuration is one file: `shiplock.toml` at the repo root. The default run
above needs none of it; the config unlocks the checks that can't guess their
inputs — version alignment, the architecture and manifest maps, object coverage,
versioned-file markers — and lets you name your own doc set. Read this section
once and you'll know what to put in it.

### Two rules that make the rest obvious

1. **Shiplock runs only the checks you configure.** Every section is optional and
   independent. Declare a section and its check runs; leave it out and the check
   prints a skip notice and moves on. A skip is never a pass — the notice says
   plainly that the check didn't run, so an undeclared surface can't pass by
   staying silent. (The zero-config default run is the one exception, and it says
   so on stderr when it happens.)

2. **No file names are baked in.** The doc set this project happens to use is one
   team's house convention, not a shiplock requirement. The default run detects
   common names as a convenience; a `shiplock.toml` replaces that guess entirely.
   Declare the docs you ship, under the names you use, and configure only the
   checks you want. A repo with just a `README.md` and no architecture doc simply
   omits `[architecture]`, and the architecture check skips.

### Start with one section

The smallest config checks a README for missing files and banned words:

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
| `[manifest]` | `manifest` (the per-file map exists, lists every source file, and moves with them) | a reminder notice suggests keeping one; `remind = false` silences it |
| `[[coverage]]` | `coverage` — one entry per documented object, repeatable | object coverage isn't checked |
| `[[versioned_files]]` | `versioned-files` — one entry per data file, repeatable | marker movement isn't checked |
| `[deps]` | `deps-declared-once` (requirements files vs pyproject) | duplicate dependency declarations aren't checked |
| `[tests]` | `test-assertions` (every test carries an expectation) | assertion-free tests aren't checked |

### The complete config, annotated

Every section shiplock understands. Copy what you need and delete the rest,
keeping the header comment: it tells anyone reading your repo's config (a
teammate, an agent scanning the tree) what checks it, and where to get it.

```toml
# Shiplock config: docs-vs-code release checks, deterministic and
# agent-audited (semantic and ablation prompts included).
# Install: pip install shiplock. Docs: https://github.com/shehuphd/shiplock

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

[manifest]                   # optional: omit for a reminder, declare to check
doc = "MANIFEST.md"
sources = ["src/**/*.py"]    # files the manifest must list
exempt = []
# Or, to silence the reminder in a repo that keeps no manifest:
# [manifest]
# remind = false

[[coverage]]                 # optional, repeatable: one table per documented object
object = "pkg:ErrorCode"     # "module:Attr.path", or a bare "module" for exports
doc = "USAGE.md"
kind = "enum"                # enum | params | exports
exempt = []

[[versioned_files]]          # optional, repeatable: one table per versioned data file
path = "src/pkg/data.json"
pattern = '"data_version":\s*"([^"]+)"'   # a regex with a single capture group

[deps]                       # optional: omit to skip duplicate-declaration checks
requirements = ["requirements*.txt", "tools/requirements*.txt"]
exempt = []                  # names allowed in both places

[tests]                      # optional: omit to skip the assertion check
globs = ["tests/**/*.py"]
exempt = []                  # test names, or "path::test_name", allowed without one
```

## The checks

Eleven deterministic checks, run in this order:

| Check | Asserts |
|---|---|
| `docs-exist` | Every declared public doc exists on disk. |
| `banned-words` | The house banned-word list (word-boundary, case-insensitive) is absent from public docs and the configured source globs. The changelog is swept only above its first released-version heading. |
| `internal-refs` | No reference to an internal-only artifact appears in public docs: the gitignored planning folder, the coding-standards file, roadmap files, or assistant tool directories. A planning-folder path inside a `pypi.org` URL is carved out. |
| `readme-links` | Every markdown link in the README is absolute (`http`, `https`, `#`, or `mailto`), since PyPI resolves relative links against pypi.org. |
| `version` | The pyproject version equals the package's `__version__`, and the changelog carries a heading for that version or an `[Unreleased]` section. |
| `architecture` | Every top-level module and subpackage under the source directory is named in the architecture doc, or listed as exempt. |
| `coverage` | Every member of a declared object appears in its declared doc. Three kinds: `enum` (member names), `params` (a callable's parameter names), `exports` (a module's `__all__`). |
| `manifest` | The per-file manifest exists, carries a `Last updated:` line, lists every source file matched by its globs (by path or file name), and changed whenever the sources changed since the last git tag. With no `[manifest]` declared, the check prints a reminder notice instead — see below. |
| `versioned-files` | A declared data file whose content differs from the last reachable git tag has moved its version marker. |
| `deps-declared-once` | No package is declared in both `pyproject.toml` (dependencies and optional groups) and a requirements file matched by `[deps].requirements`. Names compare in canonical form, so `Foo_Bar` and `foo-bar` are one package; comment, option, and URL lines are ignored. |
| `test-assertions` | Every test function in the files matched by `[tests].globs` (module-level `test_*`, and `test_*` methods of `Test*` classes) contains an expectation: an `assert`, a `raises`/`warns`/`deprecated_call` context, or a call whose name starts with `assert`. A shared helper counts for the tests that call it: any call resolving to a function elsewhere in the test tree is followed, through same-module definitions, imports in any form, and `conftest.py`. A call resolving outside that tree is the code under test, so it's never an expectation. A test that only relies on code not raising should assert the side effect it exists to pin, or be exempted by name. |

### The manifest reminder

A per-file manifest (a `MANIFEST.md` mapping each source file to what it does)
gives readers a file index without opening the code. It's optional: with no
`[manifest]` section, `shiplock check` prints a notice suggesting one — generate
it by hand or with an AI tool, then declare it to keep it checked. A repo that
keeps no manifest silences the reminder for good with:

```toml
[manifest]
remind = false
```

The reminder is a notice, so it never fails a run either way.

`version` and `coverage` learn about the code by introspecting the package in a
subprocess with the checked root's source on `sys.path`, and confirm the module
resolved under root before reading it — so they never compare against a stale
copy installed elsewhere. The package must be importable (its dependencies
present) for these two to run.

When introspection can't answer, it comes back with a status naming why, and
the check turns that into a notice instead of a finding:

| Status | Meaning | Which check |
|---|---|---|
| `not_under_root` | The target resolved to source outside the checked root (a stale installed copy) | `version`, `coverage` |
| `error` | Importing or reading the target raised an exception | `version`, `coverage` |
| `no_all` | An `exports` target has no `__all__` | `coverage` |
| `not_enum` | An `enum` target isn't an `Enum` | `coverage` |
| `not_callable` | A `params` target isn't callable | `coverage` |

When a check can't run for a reason outside introspection — a source directory
that isn't there, no git tag to diff against — it prints a notice naming the
reason and the fix, and the run continues either way.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean: no check produced a finding. |
| 1 | One or more checks found a problem. |
| 2 | A config or usage error (no `shiplock.toml`, malformed TOML, a bad flag). |

Findings print to stdout; notices and the summary print to stderr, so stdout
stays clean for a pipe. This makes `shiplock check` both a CI step and a pytest
assertion.

On a terminal, findings and the failing summary render red and a clean summary
renders green; piped output carries no escape codes, and setting the `NO_COLOR`
environment variable turns color off everywhere. Usage mistakes get a sentence,
not a parser dump: a mistyped command is answered with the valid commands and,
when one is close enough, a "Perhaps you meant" suggestion.

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

A second prompt ships alongside it, outside the gate:

```bash
shiplock prompt ablation
```

This one asks an agent for an advisory report on what the repo could remove,
merge, or make cheaper: dead code, duplicates, inefficiency visible from the
source, packaging and toolchain redundancy, assertion-free or status-code-only
tests, and size hot spots. Every claim must be measured against the tree, and
findings split into mechanical folds (safe now) and decisions (the owner's
call). It ends with a do-first ordering instead of a verdict line, so nothing
wires it into CI; run it when you want the report.

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
      audit-model: "sonnet"       # in your key's provider's own naming
    secrets:
      # "provider/key" — the prefix tells the gate which agent CLI to run
      AUDIT_API_KEY: ${{ secrets.YOUR_AUDIT_KEY }}
```

The `check` job runs the deterministic checks on every push and pull request.
The `audit` job reads the provider from the key, runs the semantic layer
through that provider's agent CLI, and
opens an issue if the audit returns `AUDIT: FAIL` (or produces no verdict line,
which fails closed). Each audit's token usage — input, output, cache traffic,
and a cost in USD priced by [rates](https://pypi.org/project/rates/) from the
attempt's own provider and model — is written to the run's job summary, and to
the issue footer when one is opened, so the gate's spend stays visible per run.
The price comes from rates' bundled offline snapshot (no extra network call);
an attempt on a model rates doesn't carry a price for shows `n/a` rather than
guessing.

The workflow's inputs:

| Input | Default | What it controls |
|---|---|---|
| `python-version` | `"3.12"` | The Python the checks run on. |
| `shiplock-spec` | `"shiplock"` | The pip requirement for shiplock itself (`"."` in shiplock's own repo). |
| `run-audit` | `true` | Whether the semantic audit job runs at all. |
| `audit-model` | `""` | The audit's model, in the key's provider's own naming. Required when `run-audit` is true. |
| `audit-fallback-model` | `""` | The fallback attempt's model, in the fallback key's provider's naming. Empty reuses `audit-model` when both keys name the same provider; a cross-provider fallback must declare its own. |
| `audit-permission-mode` | `"dontAsk"` | The permission mode for the read-only run (`anthropic` keys only). |
| `audit-effort` | `"high"` | Claude's reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`; `anthropic` keys only). Lower it to cut cost when a blocked release is all you need from a failed audit; raise it to `xhigh` if audits still miss drift your own review would have caught. Effort trades cost for how much the model reasons before answering, not just how much it writes. |

### App repos and other non-packages

Both jobs install your checked-out repo only when its config needs the package
importable: when `[version]` names a package, or a `[[coverage]]` entry exists.
Those are the two checks that import the repo; every other check reads files. A
repo that configures neither (an application, a docs set, a config bundle) is
never installed, so it needs no `pyproject.toml` or packaging to run the gate.
The gate asks `shiplock needs-import`, which prints `true` or `false` for the
repo it's run in, and installs only on `true`.

### The audit key declares its provider

Shiplock is provider-agnostic by default. Pick your own audit provider: the
prompt is plain markdown, the tools are read-only, and the verdict contract is
one greppable line, so any agent CLI that can read files and print text can run
the audit. The verdict's authority comes from the checklist, never from which
vendor executed it.

Don't hardcode a provider. A vendor name baked into a consuming repo, a secret,
or an adapter's calling code is a design defect: it pins work that should stay
portable to one billing account and one CLI. Treat the provider as data. It's
the prefix on the key, read at run time and dispatched to the matching adapter,
so switching or adding a vendor is a config change, never a code rewrite.

The `AUDIT_API_KEY` secret carries both the provider and the key as one string:

```
provider/key
```

For example `anthropic/sk-ant-...` or `openai/sk-...`. The provider part (before
the first slash) is case-insensitive; the key part (after it) is passed to the
provider's CLI byte for byte, so its case is preserved. The gate reads the
prefix and runs the matching adapter. Two adapters ship today, `anthropic` (runs
Claude Code) and `openai` (runs Codex), but the `provider/key` format admits any
provider: `AUDIT_API_KEY` and `AUDIT_FALLBACK_API_KEY` each map to whichever
provider their own prefix names. Adding a provider is a change inside the gate's
adapter, not to the shape of anyone's secrets: when another vendor ships a
headless agent CLI, wiring it in means teaching the gate that prefix, and a
consumer then reaches it by changing the prefix and the model name.

Because the provider names the model namespace, `audit-model` has no default:
declare it in your provider's own naming. The `check` job needs no key.

Pick the cheapest model among reasoning equals. The audit has to end with a
greppable `AUDIT: PASS` or `AUDIT: FAIL` line, and a run with no verdict line
fails closed, so the model needs enough reasoning to work the checklist and
hold the format. Among the models that clear that bar, the cheapest is the
right pick, and the newest is often among the cheapest: price doesn't track
release date, so compare current prices rather than assuming the latest model
costs more. Where a provider exposes a reasoning-effort dial (see `audit-effort`
for Claude), raise the effort for a more thorough audit before reaching for a
larger, pricier model. Avoid a bottom-tier model that runs but drops or
malforms the verdict line, since that turns a small saving into spurious gate
failures.

Add the key to the consuming repo at
`https://github.com/<owner>/<repo>/settings/secrets/actions` → **New repository
secret**, storing the whole `provider/key` string as the value (the secret in
your repo can carry any name; the workflow's `secrets:` block maps it to
`AUDIT_API_KEY`).

Don't want the audit? Set `run-audit: false` and skip the key; the deterministic
`check` job still runs. And if `run-audit` is on but no `AUDIT_API_KEY` secret
is set, the audit is skipped with a warning rather than failing the run — the
deterministic checks still gate it, and the warning keeps the skip visible.

### Failover to a second provider

Add an `AUDIT_FALLBACK_API_KEY` secret (same `provider/key` format) and an
audit whose first attempt dies mid-run — a revoked key, an exhausted
credit balance — is continued rather than redone. The mechanism is
provider-neutral: as the audit settles each question it appends a line to a
progress log in the workspace, and the second attempt reads that log, keeps the
settled answers, and works on from the first uncovered question. The job summary
then shows both attempts' usage.

The fallback belongs on a **different provider or billing account** than the
primary — credit exhaustion is an account-level event, so a sibling key from the
same account is just as empty as the one that failed:

```yaml
    with:
      audit-model: "sonnet"
      audit-fallback-model: "<a model in the fallback provider's naming>"
    secrets:
      AUDIT_API_KEY: ${{ secrets.MY_ANTHROPIC_AUDIT_KEY }}    # anthropic/sk-ant-...
      AUDIT_FALLBACK_API_KEY: ${{ secrets.MY_OPENAI_AUDIT_KEY }}  # openai/sk-...
```

With no fallback configured, a failed first attempt fails the job, and a re-run
starts the audit from scratch.

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
