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

When you want the rest of the checks — version alignment, architecture and
manifest coverage, object documentation, versioned-file markers — add a
`shiplock.toml` declaring your repo's surfaces. The full schema, section by
section, is in
[USAGE.md](https://github.com/shehuphd/shiplock/blob/main/USAGE.md).

## Two layers

Shiplock checks in two layers:

1. **Deterministic checks** (`shiplock check`) — fast, exact, no model. Missing
   docs, banned words, internal references in public docs, absolute README
   links, version alignment, the architecture module list, object coverage,
   the per-file manifest, versioned-file markers.
2. **A semantic audit** (`shiplock prompt`) — the prompt for a fresh agent to
   read the code and hold every doc claim against it, from state rather than
   from what changed. Centrally versioned inside the package, so every repo gets
   prompt updates on the next install.

Print the audit prompt with:

```bash
shiplock prompt
```

## In CI

Shiplock ships a reusable GitHub Actions workflow that runs both layers on
every push and pull request, and opens an issue when the audit fails:

```yaml
jobs:
  gate:
    uses: shehuphd/shiplock/.github/workflows/gate.yml@main
```

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
