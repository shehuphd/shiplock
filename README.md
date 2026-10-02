# Shiplock

Shiplock is a release gate that checks a repository's documentation against its
own code. It catches the drift that shows up at ship time: a doc describing a
provider set the code no longer has, a README missing a shipped feature, a
version string that moved in one file and not another, an example file that fell
behind the API it demonstrates.

It runs the same way in three places — your terminal, your test suite, and CI —
off one config file per repo, so the check that blocks a release is the check you
ran locally a minute earlier.

## Before you start

Shiplock needs Python 3.10 or newer. Check with:

```bash
python3 --version
```

If that fails, install Python from [python.org/downloads](https://www.python.org/downloads/)
(or your platform package manager: `apt install python3-pip`, `dnf install python3-pip`).

## Install

```bash
pip install shiplock
```

## See it work

Point it at any repo — no config file, no setup:

```bash
shiplock check path/to/your/repo
```

(or `shiplock check` from inside one). Shiplock sweeps whichever docs it
recognizes and reports what it finds, one finding per problem:

```
docs-exist  USAGE.md
    declared public doc is missing: USAGE.md
readme-links  README.md:31
    relative link 'USAGE.md' (PyPI resolves it against pypi.org, not the repo)
```

Findings print to stdout; notices for the checks that need configuration, and
the run summary, print to stderr — so stdout stays clean for a pipe. Exit code 0
means clean, 1 means a check found a problem, 2 means a config or usage error.
That makes `shiplock check` a drop-in CI step and a pytest assertion alike.
`--json` swaps the human output for one machine-readable object. On a terminal,
findings render red and a clean run green (`NO_COLOR` turns that off); piped
output stays plain.

To set a repo up, run `shiplock init`: it writes a starter `shiplock.toml`,
a gitignored rules file, and the git hooks that stop a leak before it leaves
the machine, then add the names to keep out with `shiplock rules add
code-name NAME` (or `email`, `folder`, `file`). When you want the rest of the
checks — version alignment, architecture and manifest coverage, object
documentation, versioned-file markers — extend the `shiplock.toml` with your
repo's surfaces. The full schema, section by
section, is in
[USAGE.md](https://github.com/shehuphd/shiplock/blob/main/USAGE.md).

## Three layers

Shiplock checks in three layers:

1. **Deterministic checks** (`shiplock check`) — fast, exact, no model. Missing
   docs, the words you ban, internal references in public docs, absolute README
   links, the flags, env vars, and config keys the docs name existing in the
   code, stated flag defaults matching the parser's, a leak scan of the
   git-tracked files, version alignment, the architecture module list, object
   coverage, the per-file manifest, versioned-file markers, dependency
   declarations duplicated between pyproject and requirements files, tests
   that carry no assertion.
2. **A typed judge** (`shiplock judge`) — questions to a judgment model
   (TypeSafe's Jev) that answers each with a probability: which flags,
   commands, and env vars the docs don't describe, and which doc sentences the
   code behind them doesn't obviously support. Uncovered surface is a warning;
   doubted sentences become leads for the audit. Runs only when invoked, and
   it's billable.
3. **A semantic audit** (`shiplock prompt`) — the prompt for a fresh agent to
   read the code and hold every doc claim against it, from state rather than
   from what changed, with the judge's leads first when there are any.
   Centrally versioned inside the package, so every repo gets prompt updates on
   the next install.

The leak scan also runs on its own, and in the git hooks `shiplock init`
installs (`pre-commit` over the staged diff, `pre-push` over the pushed
range):

```bash
shiplock scan
```

It reads the git-tracked files, not only the declared docs, and flags the
internal-reference patterns, project code-names, home paths, and personal
emails you declare. shiplock ships no rules of its own: rules about you live
in one file per machine (`~/.config/shiplock/rules.toml`), read in every repo;
a repo's own rules in a gitignored `shiplock.local.toml`; and patterns that
name nothing private in `shiplock.toml`. `shiplock rules add` writes them
and `shiplock rules suggest` lists the folders the repo already ignores, so
nobody writes TOML or a regex. Private labels and matched values are masked
in the output, so a code-name never echoes into a CI log.

Print the audit prompt with:

```bash
shiplock prompt
```

There's also a second, advisory prompt, outside the gate: `shiplock prompt
ablation` prints a prompt for an agent to report what the repo could remove,
merge, or make cheaper, with findings split into mechanical folds and decisions
for the owner. It renders a report, never a verdict.

## In Python

Everything the CLI does is callable — `load_config` and `run_checks` return a
typed report, so the gate can run inside a test suite:

```python
from pathlib import Path
from shiplock import load_config, run_checks

def test_docs_match_code():
    report = run_checks(load_config(Path(__file__).parent.parent))
    assert report.ok, [f.message for f in report.findings]
```

The full API surface is in
[USAGE.md](https://github.com/shehuphd/shiplock/blob/main/USAGE.md).

## In CI

Shiplock ships a reusable GitHub Actions workflow that runs the deterministic
checks on every push and pull request and the semantic audit when asked, and
opens an issue when the audit fails:

```yaml
jobs:
  gate:
    uses: shehuphd/shiplock/.github/workflows/gate.yml@main
```

The gate installs the checked-out repo only when its config uses a check that
imports the package (version or coverage), which it decides by running
`shiplock needs-import`. An app repo that configures neither runs the gate
without being an installable package.

The full wiring — inputs, the audit's API key, the dormant-first rollout — is in
[USAGE.md](https://github.com/shehuphd/shiplock/blob/main/USAGE.md).

## Documentation

- [USAGE.md](https://github.com/shehuphd/shiplock/blob/main/USAGE.md) — the full manual: config schema, every check, exit codes.
- [ARCHITECTURE.md](https://github.com/shehuphd/shiplock/blob/main/ARCHITECTURE.md) — how the package is put together.
- [MANIFEST.md](https://github.com/shehuphd/shiplock/blob/main/MANIFEST.md) — a per-file map of the codebase.
- [CHANGELOG.md](https://github.com/shehuphd/shiplock/blob/main/CHANGELOG.md) — dated release notes.

## License

MIT. See [LICENSE](https://github.com/shehuphd/shiplock/blob/main/LICENSE).

By [Mo Shehu](https://mohammedshehu.com)
