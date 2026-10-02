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
exist) get checked for missing files and relative README links, and a note on
stderr says the run used defaults. The checks that need declarations skip with a
notice each, including banned words and internal references: shiplock ships no
word list and no patterns, so those run once you declare your own.

Add `--json` for one machine-readable object on stdout instead of the human
rendering:

```bash
shiplock check path/to/repo --json
```

`shiplock scan` and `shiplock judge` take `--json` too.

Set a repo up for the leak scan in one command:

```bash
shiplock init
```

It writes a starter `shiplock.toml` from the docs it finds, a gitignored
rules file, and the two git hooks that stop a leak before it leaves the
machine, then lists the folders the repo ignores so you can turn them into
rules. It never overwrites a file and never asks a question, so an assistant
can run it unattended. `shiplock init --no-hooks` skips the hooks. Then add
the names to keep out:

```bash
shiplock rules add code-name bluebird
shiplock rules add email ada@personal.example
shiplock rules add folder drafts/
```

The rules section below has the rest. Print the semantic audit prompt for a
fresh agent:

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

2. **No house rules are baked in.** The doc set this project happens to use is
   one team's house convention, not a shiplock requirement, and the same goes
   for banned words and internal-reference patterns: shiplock ships none. The
   default run detects common names as a convenience; a `shiplock.toml`
   replaces that guess entirely.
   Declare the docs you ship, under the names you use, and configure only the
   checks you want. A repo with just a `README.md` and no architecture doc simply
   omits `[architecture]`, and the architecture check skips.

### Start with one section

The smallest config checks a README for missing files and the words you ban:

```toml
[docs]
public = ["README.md"]

[style]
banned = ["leverage", "synergy"]
```

Run `shiplock check`: `docs-exist` and `banned-words` run over the README, and
every other check prints a skip notice. Grow the file one section at a time as
you want more coverage — nothing forces you to fill in the rest.

### What each section turns on

| Section | Turns on | Leave it out and |
|---|---|---|
| `[docs]` `public` | `docs-exist` over your docs, and the docs `banned-words` and `internal-refs` read | those three don't run |
| `[docs]` `changelog` | changelog-aware banned-word scoping, and the changelog half of `version` | the changelog gets no special handling |
| `[docs]` `readme` | `readme-links` | the absolute-link check doesn't run |
| `[style]` | `banned-words`: your word list (`banned`), and the source files it sweeps (`source_globs`) | no word list, so `banned-words` doesn't run |
| `[version]` | `version` (pyproject vs `__version__` vs changelog) | version alignment isn't checked |
| `[architecture]` | `architecture` (every module named in the doc) | the module-list check doesn't run |
| `[manifest]` | `manifest` (the per-file map exists, lists every source file, and moves with them) | a reminder notice suggests keeping one; `remind = false` silences it |
| `[[coverage]]` | `coverage` — one entry per documented object, repeatable | object coverage isn't checked |
| `[[versioned_files]]` | `versioned-files` — one entry per data file, repeatable | marker movement isn't checked |
| `[deps]` | `deps-declared-once` (requirements files vs pyproject) | duplicate dependency declarations aren't checked |
| `[tests]` | `test-assertions` (every test carries an expectation) | assertion-free tests aren't checked |
| `[scan]` | committed patterns (`refs`), the rules files to read, excludes, and the secrets switch for `scan` | `scan` still runs with your rules files alone, and skips with a notice when there are none |
| `[anchors]` | `doc-anchors` (every flag, env var, and config key a doc names exists in the code) and `doc-defaults` (a stated flag default matches the parser's) | neither runs |
| `[judge]` | `shiplock judge`: which docs it judges, what code surface stays undocumented on purpose, the provider and model | `judge` uses `[docs].public` and the defaults |

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

[style]                          # optional: omit to skip the banned-word check
banned = ["leverage", "synergy"] # your words; shiplock ships none
allow = []                       # words exempt in this repo
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

[anchors]                    # optional: doc-anchors and doc-defaults
exempt = ["--no-verify"]      # tokens another tool owns; the docs may name them
defaults = true              # compare stated flag defaults with the parser's

[judge]                      # optional: the model-judged layer (shiplock judge)
docs = []                    # docs to judge; empty means [docs].public
undocumented = []            # flags, commands, env vars kept out of the docs on purpose
provider = "typesafe"        # the judgment provider
model = "jev-1.13.0"         # pinned; a different resolved model is a finding

[scan]                       # optional: the leak & internal-reference scan
# Repo-level gitignored rules files to read. Omit to read shiplock.local.toml
# at the repo root. Your own user-level rules file is read either way. A rules
# file that git tracks is refused as a finding.
blocklist = ["shiplock.local.toml", "team-rules.toml"]
secrets = false              # opt in to generic secret-pattern detection
exclude = ["vendor/**"]      # tracked files to skip
# Patterns that name nothing internal, enforced in CI. Anything that would
# publish an internal name belongs in a rules file instead.
refs = [
  { label = "ticket id", pattern = 'ACME-\d+' },
]
```

## The checks

Fourteen deterministic checks, run in this order:

| Check | Asserts |
|---|---|
| `docs-exist` | Every declared public doc exists on disk. |
| `banned-words` | The words in `[style].banned` (word-boundary, case-insensitive) are absent from public docs and the configured source globs. The changelog is swept only above its first released-version heading. Skips with a notice when no words are declared. |
| `internal-refs` | No declared internal-reference pattern matches in public docs. Patterns come from `[scan].refs` and your rules files; shiplock ships none, and skips with a notice when there are none. |
| `readme-links` | Every markdown link in the README is absolute (`http`, `https`, `#`, or `mailto`), since PyPI resolves relative links against pypi.org. |
| `doc-anchors` | Every flag (two leading dashes), env var (upper case with underscores), and config key (`[section].key` form) a public doc names in backticks is declared somewhere in the code: an argparse call, an `os.environ` read, a parser reading the key, or a string literal. A token another tool owns goes in `[anchors].exempt`. Fenced code is skipped. |
| `doc-defaults` | A default a public doc states for a flag, in the forms "flag defaults to V" and "flag (default: V)", matches the literal `default=` the argparse call gives it, after a small phrasing table ("the current directory" is `.`). Flags with no literal default aren't compared. `[anchors].defaults = false` turns it off. |
| `scan` | Over the git-tracked files (`git ls-files`), not only the declared docs, whenever any rules exist, with or without `[scan]`: the same internal-reference patterns, plus the code-names, home usernames, and personal emails in your rules files. Private labels and matches are masked in the output. Runs whenever any rules exist; the `shiplock scan` command runs it directly on any repo. See below. |
| `version` | The pyproject version equals the package's `__version__`, and the changelog carries a heading for that version or an `[Unreleased]` section. |
| `architecture` | Every top-level module and subpackage under the source directory is named in the architecture doc, or listed as exempt. |
| `coverage` | Every member of a declared object appears in its declared doc. Three kinds: `enum` (member names), `params` (a callable's parameter names), `exports` (a module's `__all__`). |
| `manifest` | The per-file manifest exists, carries a `Last updated:` line, lists every source file matched by its globs (by path or file name), and changed whenever the sources changed since the last git tag. With no `[manifest]` declared, the check prints a reminder notice instead — see below. |
| `versioned-files` | A declared data file whose content differs from the last reachable git tag has moved its version marker. |
| `deps-declared-once` | No package is declared in both `pyproject.toml` (dependencies and optional groups) and a requirements file matched by `[deps].requirements`. Names compare in canonical form, so `Foo_Bar` and `foo-bar` are one package; comment, option, and URL lines are ignored. |
| `test-assertions` | Every test function in the files matched by `[tests].globs` (module-level `test_*`, and `test_*` methods of `Test*` classes) contains an expectation: an `assert`, a `raises`/`warns`/`deprecated_call` context, or a call whose name starts with `assert`. A shared helper counts for the tests that call it: any call resolving to a function elsewhere in the test tree is followed, through same-module definitions, imports in any form, and `conftest.py`. A call resolving outside that tree is the code under test, so it's never an expectation. A test that only relies on code not raising should assert the side effect it exists to pin, or be exempted by name. |

### Rules: what `internal-refs` and `scan` enforce

shiplock ships no rules. Every internal-reference pattern, code-name, home
username, and personal email these checks enforce is one you declare, and
where you declare it decides where it's enforced:

| Where | Committed | Runs | Holds |
|---|---|---|---|
| Your user rules file (`~/.config/shiplock/rules.toml`) | no | every repo on this machine | internal-reference patterns, code-names, home usernames, emails, `allow` |
| A repo rules file (`shiplock.local.toml`, or the files `[scan].blocklist` names) | no, gitignore it | that repo, locally | the same keys |
| `[scan].refs` in `shiplock.toml` | yes | locally and in CI | patterns that name nothing internal |
| `--rules PATH` | no | wherever you pass it | the same format, e.g. a file a CI gate writes from a secret |

A pattern that names one of your internal files would publish that name if you
committed it, so private rules live in the gitignored files. To enforce them
in CI too, see [Private rules in CI](#private-rules-in-ci).

`scan` reads the whole git-tracked set, so a code-name in a test fixture or a
README example, a file you never declared, doesn't reach a push. It runs
inside `shiplock check` whenever any rules exist, and skips with a notice when
none do. `internal-refs` holds the same patterns against your declared public
docs.

#### Editing rules with `shiplock rules`

Nobody has to write TOML or a regex. `shiplock rules` edits the files:

```bash
shiplock rules add code-name bluebird kestrel   # names that aren't public yet
shiplock rules add email ada@personal.example
shiplock rules add username ada                 # flags /Users/ada and /home/ada
shiplock rules add folder drafts/               # builds the pattern
shiplock rules add file NOTES.md                # builds the pattern
shiplock rules add ref "ticket ids" --pattern 'ACME-\d+'
shiplock rules allow ada@personal.example --in README.md --in docs/
shiplock rules remove code-name bluebird
shiplock rules remove allow ada@personal.example
shiplock rules list                             # masked; --unmask for full values
shiplock rules suggest                          # what the repo ignores and no rule covers
```

Where each command writes, unless `--user` or `--repo` says otherwise:

| Command | File | Why |
|---|---|---|
| `add` code-name, email, username, ref | your user file | These describe you, and apply to every repo you push from |
| `add` folder, file | the repo's `shiplock.local.toml` | An ignored folder or a private file belongs to one repo |
| `allow` | the repo's `shiplock.local.toml` | What a repo contains on purpose is specific to it |
| `remove` | whichever file holds the entry | |

Every `rules` subcommand takes `--path REPO` before the subcommand to work on
another repo (`shiplock rules --path ../other add folder drafts/`); the
default is the current directory. `push-secret` also takes `--rules PATH`,
repeatable, to fold an extra rules file into the secret.

`folder` and `file` build the pattern: `drafts/` matches `drafts/`, `./drafts/`,
and `repo/drafts/x` but not `@acme-drafts/` or `my_drafts/`; a dot folder
like `.notes` gets the form that skips domains such as `platform.notes.com`;
a nested path (`docs/internal/`) keeps every component; `NOTES.md` matches
that name and not `docs/NOTES.md`, `my-NOTES.md`, or `NOTES.mdx`. Output masks
the values, since a terminal log or an assistant transcript is one more place
a name can end up.

The writer rewrites a file in its own layout, which would drop any comment
you wrote in it by hand. When the target file holds one, the command refuses,
prints the entry to add yourself, and exits 2; `--rewrite` lets it proceed.
A file shiplock wrote carries only its own header, which the writer keeps.

`shiplock rules suggest` (which `init` also runs) lists the folders git
ignores in this repo and the files a `.gitignore` or `.git/info/exclude`
line names outright, minus shiplock's own files, minus
tool output such as `node_modules/` or `.venv/` and minus anything a rule
already covers, with how many tracked files name each. Nothing is written
until you run the `add` command it prints. Git only reports ignored folders
that exist on this machine.

#### Your user rules file

Rules about you, your code-names, your home username, your personal email,
apply to every repo you push from, so they live in one file per machine that
shiplock reads in every repo with no config:

| Platform | Default location |
|---|---|
| macOS, Linux | `$XDG_CONFIG_HOME/shiplock/rules.toml`, or `~/.config/shiplock/rules.toml` when `XDG_CONFIG_HOME` is unset |
| Windows | `%APPDATA%\shiplock\rules.toml` |
| Any | `SHIPLOCK_USER_RULES=/path/to/rules.toml` overrides the default |

The file is read when it exists and silent when it doesn't; a file named
through `SHIPLOCK_USER_RULES` reports when missing. Set
`SHIPLOCK_NO_USER_RULES=1` to run a repo against its own rules only, which
shiplock's own test suite does. When the user file contributes rules, the
scan says so in an info notice with the count and the path, so two people
running the same repo can explain why their results differ. `shiplock rules
list` prints the resolved path.

#### A repo's rules file

A repo's own rules, the folders it keeps private and the entries it allows,
go in `shiplock.local.toml` at the repo root, which shiplock reads when
`[scan].blocklist` names no files. Name other files there instead when you
need them. Add each to `.gitignore`; a rules file that git tracks is refused
as a finding, so the one mistake that would ship its contents fails the run.
Problems inside a rules file (an unknown key, a bad regex) print as notices,
since that file differs per machine.

Both files use the same format, shown below for anyone editing by hand;
`shiplock rules` writes the same thing. shiplock never loads these examples
on its own.

<!-- starter-rules:start -->
```toml
# shiplock.local.toml (gitignored)
refs = [
  # A private folder, but not the same word ending a longer name or an npm
  # scope like @acme-drafts/.
  { label = "drafts/", pattern = '(?<![\w@.-])drafts/' },
  # Assistant config directories, but not domains like platform.claude.com.
  { label = ".claude", pattern = '(?<![\w.])\.claude\b' },
  { label = ".codex", pattern = '(?<![\w.])\.codex\b' },
  { label = ".cursor", pattern = '(?<![\w.])\.cursor\b' },
  { label = ".grok", pattern = '(?<![\w.])\.grok\b' },
]
code_names = ["bluebird"]            # names that aren't public yet
home_usernames = ["ada"]             # flags /Users/ada and /home/ada
emails = ["ada@personal.example"]    # personal addresses
allow = []                           # entries this repo contains on purpose
```
<!-- starter-rules:end -->

Keep public names off the code-name list: a name already published on PyPI,
npm, crates.io, or a public GitHub repo isn't a secret, and listing it fails
every repo that depends on it.

#### Allowing what a repo contains on purpose

`allow` lists entries this repo contains deliberately: a contact address in
the README, a project's own name inside its own repo, a pattern label. An
entry is either a bare string, allowed anywhere in the repo, or a table that
names where it's allowed:

```toml
allow = [
  "bluebird",                                                      # anywhere
  { value = "ada@personal.example", paths = ["README.md", "docs/"] },
]
```

`paths` are repo-relative globs, matched the way `[scan].exclude` is; a
trailing slash names a folder and everything under it. `value` is what
`allow` matches: a code-name, username, or email string, or a pattern's
label. Two entries for one value combine their paths, and a bare entry
allows the value everywhere whatever else says.

Allowed matches never fail the run. Each allowed entry that matched prints
one notice with its count and the files it matched in, masked; an entry that
matched nothing prints nothing. A match outside an entry's paths is a
finding, and its message names the allowed paths so the fix is clear. A glob
that matches no tracked file prints a warning, so a renamed file doesn't
leave a scope pointing at nothing.

`allow` is read only from rules files, never from `shiplock.toml`: a
committed allow entry would publish part of your private list.

#### Output

Every finding names the file, the line, and the rule. A pattern's label from a
rules file, and every matched code-name, username, or email, shows only its
first character, so nothing private reaches a CI log. Labels from
`shiplock.toml` are already public and print in full. `.gitignore` and
`.gitattributes` are never scanned, since naming a private path there keeps it
out of the repo. The paths in `[scan].blocklist` are carved out of the pattern
match, and `shiplock.toml` never flags its own `refs` declarations. Binary
files are skipped, and `[scan].exclude` globs skip more.

#### Secrets

`secrets = true` opts into generic secret-pattern detection (private-key blocks,
common cloud and token formats). It's off by default: a dedicated scanner such
as gitleaks covers secrets more thoroughly, and the broad patterns can fire on
a fixture.

#### Running it

`shiplock scan` runs on any repo with whatever rules it finds: your user
file, the repo's rules file, any `--rules` files, plus `[scan]`'s committed
patterns when the repo declares them. With no rules at all it skips with a
notice. Three modes:

```bash
shiplock scan                              # every git-tracked file, as of now
shiplock scan --staged                     # the added lines of the staged diff
shiplock scan --range origin/main..HEAD    # the added lines and commit messages of a commit range
```

The tracked-file scan is the audit `shiplock check` and CI run. The other two
are for hooks. A push carries every commit in the range, so a name committed
and removed again before the push, or one in a commit message, still leaves
the machine: `--range` reads each commit's added lines and its message, and
takes any `git rev-list` expression. `--staged` reads only what's about to be
committed, so it's fast enough to run on every commit, and it catches a leak
while the fix is one amend rather than a history rewrite.

`shiplock init` installs both hooks. Each runs `python -m shiplock` through
the Python interpreter `init` ran under, so it works from a GUI git client or a shell
with no virtualenv active; if that interpreter has moved it falls back to
`shiplock` on PATH, and when neither can be found it blocks the commit or
push with a message saying so, since a hook that passed on its own would
remove the protection you think you have. `--no-verify` bypasses a hook
once. `init` never overwrites a hook that exists; it lists the line to add
to it instead. A `core.hooksPath` set in the repo's own config is used; one
set in your global git config is shared by every repo on the machine, so
`init` leaves it alone and lists the lines for you to add there.

Written by hand, the hooks are:

```bash
# .git/hooks/pre-commit
#!/bin/sh
shiplock scan --staged

# .git/hooks/pre-push
#!/bin/sh
while read -r local_ref local_sha remote_ref remote_sha; do
  if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
    range="$local_sha --not --remotes"
  else
    range="$remote_sha..$local_sha"
  fi
  shiplock scan --range "$range" || exit 1
done
```

Exit codes match the rest of the CLI: 0 clean, 1 a finding, 2 a usage or
config error.

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
| 2 | A config or usage error (malformed TOML, a bad flag). |

Findings print to stdout; notices and the summary print to stderr, so stdout
stays clean for a pipe. This makes `shiplock check` both a CI step and a pytest
assertion.

On a terminal, findings and the failing summary render red and a clean summary
renders green; piped output carries no escape codes, and setting the `NO_COLOR`
environment variable turns color off everywhere. Usage mistakes get a sentence,
not a parser dump: a mistyped command is answered with the valid commands and,
when one is close enough, a "Perhaps you meant" suggestion.

## The judge

Between the deterministic checks and the agent audit there's a second layer:
typed questions to a judgment model, which answers each with a probability
and generates no text. It runs only when invoked, and it's billable:

```bash
export TYPESAFE_API_KEY=...        # or JUDGE_API_KEY=typesafe/...
shiplock judge                     # coverage warnings and claim leads
shiplock judge --out judge.json    # keep the full result for the audit
shiplock judge --no-leads          # coverage only
shiplock judge --json              # the full result as one JSON object
shiplock judge --audit-output audit.md   # also check an agent audit's output
```

It asks two kinds of question:

- **Coverage.** For every CLI flag, command, and environment variable the
  code declares (from the same symbol index the anchor checks use), does any
  judged doc describe it? An item no doc describes prints as a warning with
  its code location. Surface that stays undocumented on purpose goes in
  `[judge].undocumented`.
- **Claims.** For every doc sentence that names something the index
  resolves, does the code behind those names support the sentence? These
  answers never fail a run. They're leads: with `--out`, they go into the
  JSON result, and `shiplock prompt audit --judgments judge.json` appends
  them to the audit prompt as items for the agent to confirm or overturn
  first. The eval that set this scope is described in the changelog; a
  sentence judged against a code excerpt is a place to look, not a verdict.

With `--audit-output FILE`, it also holds an agent audit's output against
the repo: the verdict line is present, every path it cites is a tracked
file, and (as judgments) the verdict follows from the findings and the
findings name specific places. A missing verdict, an untracked path, or a
verdict that doesn't follow is a finding.

A run that reaches the provider ends with an info notice: calls made, input
tokens, claims judged (none with `--no-leads`), and sentences it couldn't
anchor to code. A directory that isn't a git repo, or a repo with no docs to
judge, skips with a notice instead. The pinned model is compared with the
one the provider reports; a different one is a finding. The key comes from
`TYPESAFE_API_KEY`, or `JUDGE_API_KEY` in `provider/key` form, never from
an argument. Exit codes match `shiplock check`. The judge sends the judged
docs and the code excerpts behind their anchors to the provider; both are
git-tracked public content.

## The semantic audit

`shiplock check` covers what a machine can decide with certainty, and the
judge adds warnings and leads. The third layer, the audit, is a prompt for a fresh agent
to read the code and hold every doc claim against it, from state rather than
from what changed. Print it with:

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
    secrets:
      # "provider/key" — the prefix tells the gate which agent CLI to run
      AUDIT_API_KEY: ${{ secrets.YOUR_AUDIT_KEY }}
```

The `check` job runs the deterministic checks on every push and pull request.
The `audit` job reads the provider from the key, picks the audit's model from
that key's own live catalog (see `audit-model` below for pinning one instead),
runs the semantic layer through that provider's agent CLI, and
opens an issue if the audit returns `AUDIT: FAIL` (or produces no verdict line,
which fails closed). Each audit's token usage — input, output, cache traffic,
and a cost in USD priced by [rates](https://pypi.org/project/rates/) from the
attempt's own provider and model — is written to the run's job summary, and to
the issue footer when one is opened, so the gate's spend stays visible per run.
Input is the whole prompt on every provider, cached tokens included; the cache
read and cache write columns are parts of it. Anthropic reports those three
parts beside each other, so the gate adds them up before the table. The price
comes from rates' bundled offline snapshot (no extra network call), keyed on
the model id the CLI billed when it reports a single one (a run pinned to an alias such
as `sonnet` is priced on the dated id behind it), with 1-hour cache writes at
their own rate. When rates carries no price for the model, the cell shows the
CLI's own cost figure marked `CLI-reported` if the CLI gives one, and `n/a`
otherwise.

The workflow's inputs:

| Input | Default | What it controls |
|---|---|---|
| `python-version` | `"3.12"` | The Python the checks run on. |
| `shiplock-spec` | `"shiplock"` | The pip requirement for shiplock itself (`"."` in shiplock's own repo). |
| `run-audit` | `true` | Whether the semantic audit job runs at all. |
| `audit-model` | `""` | The audit's model, in the key's provider's own naming. Empty picks from the key's own live catalog: keycall verifies the key with one bounded generation and the model that answered runs the audit. |
| `audit-fallback-model` | `""` | The fallback attempt's model, in the fallback key's provider's naming. Empty reuses the resolved audit model when both keys name the same provider, and is picked from the fallback key's own catalog when the providers differ. |
| `audit-permission-mode` | `"dontAsk"` | The Claude Code permission mode for the audit run (`anthropic` keys only). |
| `audit-effort` | `"high"` | Reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`). An `anthropic` key maps it to Claude Code's `--effort`; a `google` key maps it to Gemini's `thinkingLevel` (`low`, `medium`, `high`, with `xhigh`/`max` clamped; `medium` is gemini-3.1-pro only, and Gemini 3 defaults to high). An `openai` key maps it to Codex's `model_reasoning_effort`. Lower it to cut cost when a blocked release is all you need from a failed audit; raise it if audits miss drift your own review would have caught. |

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
prompt is plain markdown, the checklist only needs to read files, and the
verdict contract is one greppable line, so any agent CLI that can read files and
print text can run the audit. The verdict's authority comes from the checklist, never from which
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
prefix and runs the matching adapter. Three adapters ship today, `anthropic`
(runs Claude Code), `openai` (runs Codex), and `google` (runs Gemini CLI), but
the `provider/key` format admits any provider: `AUDIT_API_KEY` and
`AUDIT_FALLBACK_API_KEY` each map to whichever provider their own prefix names.
Adding a provider is a change inside the gate's adapter, not to the shape of
anyone's secrets: when another vendor ships a headless agent CLI, wiring it in
means teaching the gate that prefix, and a consumer then reaches it by changing
the prefix and the model name.

The audit only needs to read files, but the tools each adapter is given differ.
Gemini CLI ships shell and file-write tools, so the gate restricts it to a
read-only allowlist. Claude runs with read and grep tools plus one scoped write,
to `audit-progress.md` (the failover log). Codex runs under
`--dangerously-bypass-approvals-and-sandbox`, since its own sandbox can't nest
in the CI runner, so the gate doesn't restrict its tool scope. One consequence
for Gemini: without a write tool it can't keep a progress log, so a Gemini
primary that dies mid-run restarts on the fallback rather than continuing, where
Claude and Codex continue.

Because the provider names the model namespace, `audit-model` has no fixed
default. Left empty, the gate routes from the key itself: keycall walks the
key's live catalog in its own candidate order, makes one bounded generation,
and the model that answered runs the audit. That keeps the key slots
provider-agnostic (swap in a key from any supported provider and the gate
still runs) and means no one ever writes a model name for a key whose catalog
they haven't listed. Pin `audit-model` when you want a specific model; declare
it in your key's provider's own naming, and the gate refuses a name from
another provider's naming before any call is made. The `check` job needs no
key.

Pick the cheapest model among reasoning equals. The audit has to end with a
greppable `AUDIT: PASS` or `AUDIT: FAIL` line, and a run with no verdict line
fails closed, so the model needs enough reasoning to work the checklist and
hold the format. Among the models that clear that bar, the cheapest is the
right pick, and the newest is often among the cheapest: price doesn't track
release date, so compare current prices rather than assuming the latest model
costs more. Where a provider exposes a reasoning-effort dial (see `audit-effort`,
which reaches all three adapters), raise the effort for a more thorough audit before reaching for a
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
credit balance — is continued rather than redone. The mechanism is a shared
progress log: as the audit settles each question the adapter appends a line to a
log in the workspace, and the second attempt reads that log, keeps the settled
answers, and works on from the first uncovered question. This needs the
interrupted adapter to hold a write tool — Claude and Codex do; a Gemini primary
runs read-only, so a Gemini interruption restarts on the fallback rather than
continuing. The job summary then shows both attempts' usage.

The fallback belongs on a **different provider or billing account** than the
primary — credit exhaustion is an account-level event, so a sibling key from the
same account is just as empty as the one that failed:

```yaml
    secrets:
      AUDIT_API_KEY: ${{ secrets.MY_ANTHROPIC_AUDIT_KEY }}    # anthropic/sk-ant-...
      AUDIT_FALLBACK_API_KEY: ${{ secrets.MY_OPENAI_AUDIT_KEY }}  # openai/sk-...
```

Each key's model is picked from its own catalog unless pinned, so a
cross-provider fallback needs no `audit-fallback-model` of its own.

With no fallback configured, a failed first attempt fails the job, and a re-run
starts the audit from scratch.

Wire it in dormant first: start with `on: workflow_dispatch`, run it once by
hand, then switch to the push and pull-request triggers above once a manual run
passes.

### Private rules in CI

A rules file is gitignored, so CI never sees it. To enforce the same rules in
CI, store them in a repository secret named `SHIPLOCK_RULES`:

```bash
shiplock rules push-secret
```

It merges your user file and the repo's rules file into one document and
hands it to `gh secret set` on stdin (never as an argument, so it never
appears in a process list or shell history). It needs the
[GitHub CLI](https://cli.github.com/), logged in with rights to set secrets.
The secret is a copy: rerun the command after changing any rules file. On a
repo where other people can edit workflows, a workflow edit can read a
secret, so your code-names from unrelated projects would reach them;
`--repo-only` sends only the repo's own rules file for that case.

Or set it by hand from one file:

```bash
gh secret set SHIPLOCK_RULES < shiplock.local.toml
```

Then pass it to the gate:

```yaml
    secrets:
      AUDIT_API_KEY: ${{ secrets.YOUR_AUDIT_KEY }}
      SHIPLOCK_RULES: ${{ secrets.SHIPLOCK_RULES }}
```

The `check` job writes the secret to a file readable only by the runner's
user, runs `shiplock check --rules` on it, and deletes it when the run ends.
Findings mask private labels and matched values, so the rules themselves stay
out of the job log. Without the secret, the job runs only the committed
`[scan].refs`. GitHub withholds secrets from workflows a fork's pull request
triggers, so the secret covers pushes by collaborators and cloud agents, not
outside contributors. And a CI run starts after the commit is already on
GitHub: the hooks above are what stop a leak before it leaves the machine,
and CI is the backstop for a machine that ran without them.


## Python API

Everything the CLI does is callable. The public surface:

| Name | Role |
|---|---|
| `load_config` | Read and validate a repo's `shiplock.toml`, returning a `Config`. |
| `run_checks` | Run every check over a `Config`, returning a `Report`. |
| `Config` | The parsed config, rooted at a path. |
| `Report` | The result of a run: `findings`, `notices`, and `ok`. |
| `Finding` | One disagreement between a doc surface and the code. |
| `Notice` | One note about the run: a skipped check (`kind="skip"`), a warning such as an allowed match or a deprecated key (`kind="warning"`), or context such as where the rules came from (`kind="info"`). |
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
