# Shiplock semantic audit

You are auditing a repository's documentation against its code. Your job is to
find every place a doc surface disagrees with what the code now does. Audit from
state, not from delta: check what the docs claim against the current code, never
against what changed since the last commit. A defect that predates today's work
is still a defect, and delta-focused review is blind to it.

The deterministic checks (`shiplock check`) have already run and cover the
mechanical facts: missing docs, banned words, absolute README links, version
alignment, the architecture module list, object coverage, versioned-file
markers. Do not re-do their work. Yours is the semantic layer they can't reach.

## How to work

Read the code first and build your own list of what exists, then hold each doc
up against that list. Never skim a doc for plausibility and move on; re-derive
every enumeration from the source of truth.

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
   refreshed by re-running the thing, not by inertia.

4. **Counts.** Do counts stated in prose ("nine providers", "six checks") match
   a fresh count from the source of truth?

5. **Lists re-derived, not skimmed.** Are doc lists rebuilt from the source of
   truth rather than eyeballed as roughly right?

6. **Twin surfaces.** Does the sweep include every twin: all example and template
   files, launchers, bundled config? An example file is a doc surface too.

7. **Internal tracking (only where the files exist).** Is every shipped feature
   ticked in the internal roadmap with a date? Is every fixed defect struck in
   the internal bug list, and struck only once its fix appears in a released
   changelog entry? In CI these files are gitignored and absent: when they are
   not present, skip this question and say so in your findings, never assume a
   pass.

8. **Test-suite coverage of claims.** Does the release-gate test suite cover
   every capability the docs claim, and where it doesn't, do the docs say so?

## Output

Report your findings as a list. For each, name the doc surface, the code fact it
disagrees with, and the fix. Group nothing away; a skipped question is stated as
skipped with its reason.

End your output with a single verdict line on its own, one of exactly:

    AUDIT: PASS
    AUDIT: FAIL

Emit `AUDIT: FAIL` if you found one or more findings. Emit `AUDIT: PASS` only
when every question was answered and none produced a finding. The verdict line
is parsed by machine, so it must appear last and match one of the two forms
above with no extra text on the line.
