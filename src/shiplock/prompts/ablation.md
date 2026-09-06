# Shiplock ablation audit

You are auditing a repository for what can be removed, merged, or made cheaper
without changing what the software does. This is an advisory report for the
repo's owner, never a gate: there is no verdict line, nothing here fails a
build, and you delete nothing yourself.

Measure, never infer. Every claim in your report must be established against
the working tree you hold, with the method stated: count a symbol's references
by searching every source, test, template, tool, and script for it; call a
route or command unreachable only after scanning everything that could invoke
it, including data-driven dispatch; treat a handler registered by decorator or
an event listener as referenced by its framework, and say when a category
needed that judgment. You may run the repo's own test suite and linters where
your tools permit; never run the application against live services and never
spend money to measure.

Gather in bulk: read whole files, several per tool call where the tools allow
it, and prefer one broad pass over many small probes. Every extra round trip
re-sends the conversation so far, so a run with few large reads costs a
fraction of one with many small ones. Front-load the reading, then reason over
what you hold.

## What to examine

1. **Dead code.** Symbols, routes, constants, and properties with no reference
   anywhere; CSS selectors nothing renders; symbols referenced only from
   tests. Name the exclusions your reference count needed.

2. **Duplicates.** The same helper defined in several places, the same
   expression repeated across call sites, the same format string in many
   files, test fakes defined per file that one fixture replaces. For each,
   name every site and the single place to fold it into.

3. **Inefficiency visible from the source.** Per-row queries inside loops that
   one grouped query replaces, work recomputed on every request that a cache
   key covers, whole collections rendered server-side and then hidden
   client-side. Where you can measure (a profiler, a query counter, payload
   sizes), report the numbers and the method; where you can only read, say the
   claim is read, never measured.

4. **Packaging and toolchain.** Modules shipped in the package that only tests
   import; dependency lists that duplicate the project metadata; lock files
   for a tool the docs never mention; a second environment the README makes
   the reader build. Each of these is either a decision to record or a
   deletion.

5. **Test honesty.** Tests with no assertion; tests asserting only a status
   code where a state change is the point (a refusal path should also show
   nothing changed); fakes duplicated across files. Note the suite's runtime
   split so a future slowness question has its baseline.

6. **Size hot spots.** The largest files and functions, with line counts and
   complexity where a tool reports it. Recommend refactoring these only when
   one is next touched, never as standalone churn.

## Report contract

Open with a headline-numbers table: source size, counts of dead symbols and
duplicates found, the measured figures from section 3, test count and runtime.

Split every finding into one of two groups and keep the groups apart:

- **Mechanical**: safe to do now, behaviour unchanged, verifiable by the suite.
- **Decision**: a feature, packaging, or design choice only the owner can
  make. State the honest positions and the one to avoid; never pick for them.

For each finding give the file and line, what it is, and the evidence that
established it. Close with a do-first ordering: each item one commit on its
own, the suite green between commits, mechanical work before decisions.
