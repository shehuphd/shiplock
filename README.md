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

Add a `shiplock.toml` to your repo root declaring what to check. Every section
is optional, so start with the one doc you know you ship:

```toml
# shiplock.toml
[docs]
public = ["README.md"]
```

Then run:

```bash
shiplock check
```

That checks the README for existence and banned words; every other check skips
with a notice until you declare its section. Grow the config as you want more
coverage — the full schema, section by section, is in
[USAGE.md](https://github.com/shehuphd/shiplock/blob/main/USAGE.md).

Declare a doc you haven't written yet, and `shiplock check` reports it. Findings
print to stdout, one per problem:

```
docs-exist  USAGE.md
    declared public doc is missing: USAGE.md
```

Notices for the checks you haven't configured yet, and the run summary
(`shiplock: 1 finding`), print to stderr — so stdout stays clean for a pipe. The
run exits 1.

Exit code 0 means clean, 1 means a check found a problem, 2 means a config or
usage error. That makes `shiplock check` a drop-in CI step and a pytest
assertion alike.

## Two layers

Shiplock checks in two layers:

1. **Deterministic checks** (`shiplock check`) — fast, exact, no model. Missing
   docs, banned words, absolute README links, version alignment, the
   architecture module list, object coverage, versioned-file markers.
2. **A semantic audit** (`shiplock prompt`) — the prompt for a fresh agent to
   read the code and hold every doc claim against it, from state rather than
   from what changed. Centrally versioned inside the package, so every repo gets
   prompt updates on the next install.

Print the audit prompt with:

```bash
shiplock prompt
```

## Documentation

- [USAGE.md](https://github.com/shehuphd/shiplock/blob/main/USAGE.md) — the full manual: config schema, every check, exit codes.
- [ARCHITECTURE.md](https://github.com/shehuphd/shiplock/blob/main/ARCHITECTURE.md) — how the package is put together.
- [CHANGELOG.md](https://github.com/shehuphd/shiplock/blob/main/CHANGELOG.md) — dated release notes.

## License

MIT. See [LICENSE](https://github.com/shehuphd/shiplock/blob/main/LICENSE).

By [Mo Shehu](https://mohammedshehu.com)
