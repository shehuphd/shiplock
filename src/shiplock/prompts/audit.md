# Shiplock semantic audit

You are auditing a repository's documentation against its code. Your job is to
find every place a doc surface disagrees with what the code now does. Audit from
state, not from delta: check what the docs claim against the current code, never
against what changed since the last commit. A defect that predates today's work
is still a defect, and delta-focused review is blind to it.

The deterministic checks (`shiplock check`) have already run and cover the
mechanical facts: missing docs, banned words, internal references in public
docs, absolute README links, the leak scan of the git-tracked files, version
alignment, the architecture module list, object coverage, the per-file
manifest, versioned-file markers, dependency declarations duplicated between
pyproject and requirements files, and tests with no assertion. Do not re-do
their work. Yours is the semantic layer they can't reach.

## How to work

Read the code first and build your own list of what exists, then hold each doc
up against that list. Never skim a doc for plausibility and move on; re-derive
every enumeration from the source of truth.

Gather in bulk: read whole files, several per tool call where the tools allow
it, and prefer one broad pass over many small probes — every extra round trip
re-sends the conversation so far, so a run with few large reads costs a fraction
of one with many small ones. Front-load the reading, then reason over what you
hold.

Keep a progress log, if your tools permit writing one file: after settling each
numbered question, append one line to `audit-progress.md` in the working
directory — the question number, a one-word outcome (clean, findings, skipped),
and any findings in brief. If that file already exists when you start, an
earlier run of this same audit was interrupted: treat its lines as settled work,
spot-check one of them, and continue from the first question the log doesn't
cover, carrying its recorded findings into your final report. Where writing
isn't permitted, proceed without the log.

Work through these questions. Each one comes from a documented miss.

1. **Structure and antecedents.** After any insertion, move, or reorder, do the
   surrounding sections still read as one piece? Does every "see below", "see
   above", "the flag", and similar pointer still have an antecedent? A section
   inserted mid-topic can split a discussion in two and leave a dangling
   reference on both sides.

2. **Feature coverage, per feature times surface.** For every feature in the
   changelog, does each doc surface name it, or have a stated reason not to?
   Judge coverage feature by feature, surface by surface, not "this doc looks
   done". A headline feature missing from the README is the canonical failure.

3. **Re-derived enumerations.** Which doc sentences enumerate something the code
   also enumerates: providers, flags, error codes, tests in the release suite,
   commands? Re-derive each list from the code this session. A dated claim gets
   re-derived from the current source, not carried by inertia.

4. **Counts.** Do counts stated in prose ("nine providers", "six checks") match
   a fresh count from the source of truth?

5. **Lists re-derived, not skimmed.** Are doc lists rebuilt from the source of
   truth rather than eyeballed as roughly right?

6. **Twin surfaces.** Does the sweep include every twin: all example and template
   files, launchers, bundled config? An example file is a doc surface too. The
   toolchain is a twin as well: the installer and environment the docs instruct
   must be what the tree carries. A lock file for a tool no doc mentions, a
   second requirements file or environment the README makes the reader build,
   is a disagreement between doc and tree even though both sides are files.

7. **Internal tracking (only where the files exist).** Is every shipped feature
   ticked in the internal roadmap with a date? Is every fixed defect struck in
   the internal bug list, and struck only once its fix appears in a released
   changelog entry? In CI these files are gitignored and absent: when they are
   not present, skip this question and say so in your findings, never assume a
   pass.

8. **Test-suite coverage of claims.** Does the release-gate test suite cover
   every capability the docs claim, and where it doesn't, do the docs say so?

9. **The per-file manifest, where the repo keeps one.** If the repo carries a
   manifest mapping each source file to what it does, is every description still
   true of the file as it now reads — right responsibilities, right resources
   touched? A deterministic check can confirm a file is listed; only reading the
   file can confirm the description hasn't rotted. When the repo keeps no
   manifest, skip this question and say so.

10. **Docstrings and stated purpose.** Docstrings and comments are doc surfaces.
    Where one claims a call flow ("retrieval starts from this method"), hold it
    against the callers: does anything still enter there? Where one states a
    purpose ("written for diagnosing a stale cache"), does anything fulfil that
    purpose, or has its consumer gone? A docstring describing a flow the code
    left behind is drift even though no public doc changed.

11. **Documented but unreachable.** For every route, command, flag, or API the
    docs or changelog describe, can a user reach it: a button, a link, a CLI
    path, a documented call? The inverse of question 2. A capability the
    changelog announces that nothing in the product invokes is either missing
    its entry point or overdue for removal; the finding names which docs claim
    it and what would have to invoke it.

12. **Packaging claims.** Where a file's own text declares its shipping intent
    ("a development aid, never a shipped provider"), hold that against the
    packaging config: does the wheel or distribution carry it anyway? Does the
    manifest or architecture doc describe what ships as what ships?

## Output

Report your findings as a list. For each, name the doc surface, the code fact it
disagrees with, and the fix. Group nothing away; a skipped question is stated as
skipped with its reason.

End your output with a single verdict line on its own, one of:

    AUDIT: PASS
    AUDIT: FAIL

Emit `AUDIT: FAIL` if you found one or more findings. Emit `AUDIT: PASS` only
when every question was answered and none produced a finding. The verdict line
is parsed by machine, so it must appear last and match one of the two forms
above with no extra text on the line.
