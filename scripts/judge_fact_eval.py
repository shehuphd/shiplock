#!/usr/bin/env python3
"""Second judge eval: atomic value facts only.

Extracts value facts from public docs deterministically: a flag's or key's
default, an exit code's meaning, a numeric limit tied to a named thing, a
`true`/`false` or `stdout`/`stderr` statement about a named thing. Pairs each
with the code lines that carry that value, flips the value for a synthetic
contradiction, and asks Jev one Noul pair per fact (true as stated? false, a
different value?). Existence facts (does `--flag` exist) are left out on
purpose: a backticked token either resolves in the code or it doesn't, which
is a deterministic check, not a judgment.

    python scripts/judge_fact_eval.py --repo PATH [--repo PATH ...] --repeats 15

Reuses the API, source, and sentence helpers from judge_eval.py. Live and
billable; the report totals the spend.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge_eval as je  # noqa: E402

PIN = je.PIN


@dataclass
class Fact:
    id: str
    repo: str
    doc: str
    line: int
    kind: str          # default | exit | number | bool | stream
    anchor: str        # the named thing
    value: str         # the value the doc states
    statement: str     # the fact in one sentence
    sentence: str      # the doc sentence it came from
    label: str         # supported | contradicted
    evidence: str = ""


_DEFAULT_PATTERNS = [
    re.compile(r"`(?P<anchor>[^`]+)`[^.`]{0,80}?\bdefaults? (?:to|is|of) `?(?P<value>[^`.,;)]+?)`?(?=[.,;)]|\s(?:when|if|unless|so|and|or)\b|$)"),
    re.compile(r"`(?P<anchor>[^`]+)`[^.`]{0,80}?\(default:? `?(?P<value>[^`)]+?)`?\)"),
    re.compile(r"\(default:? `?(?P<value>[^`)]+?)`?\)[^`]{0,40}`(?P<anchor>[^`]+)`"),
]
_EXIT = re.compile(r"\b(?:exit(?:s|ed)?(?: code| status)?|status)\s+(?:code\s+)?(?P<value>[0-9])\b")
_NUMBER = re.compile(r"`(?P<anchor>[^`]+)`[^.`]{0,60}?(?<![\d.])(?P<value>\d{1,4})(?![\d.]| ?%)")
_LIMIT_WORDS = re.compile(r"\b(up to|at most|at least|maximum|max|minimum|min|limit|every|within|per|attempts?|retries|times|seconds?|ms|minutes?|bytes?|MB|KB|files?|lines?|options?|questions?|characters?|entries|items|repeats?|workers|first|last)\b", re.I)
_BOOL = re.compile(r"`(?P<anchor>[^`]+)`[^.`]{0,60}?\b(?P<value>true|false)\b")
_STREAM = re.compile(r"`(?P<anchor>[^`]+)`[^.`]{0,60}?\b(?P<value>stdout|stderr)\b")


def extract_facts(repo: str, doc: str, text: str) -> list[Fact]:
    out: list[Fact] = []
    seen: set[tuple] = set()
    for line, sent in je.sentences(text):
        found: list[tuple[str, str, str]] = []
        for pat in _DEFAULT_PATTERNS:
            for m in pat.finditer(sent):
                found.append(("default", m.group("anchor"), m.group("value").strip()))
        m = _EXIT.search(sent)
        if m:
            found.append(("exit", "exit code", m.group("value")))
        for pat, kind in ((_BOOL, "bool"), (_STREAM, "stream")):
            for m in pat.finditer(sent):
                found.append((kind, m.group("anchor"), m.group("value")))
        if not any(k == "default" for k, _, _ in found):
            for m in _NUMBER.finditer(sent):
                v = m.group("value")
                if v in ("2025", "2026") or (len(v) == 4 and v.startswith(("19", "20"))) or int(v) < 2:
                    continue
                if not _LIMIT_WORDS.search(sent):
                    continue
                found.append(("number", m.group("anchor"), v))
        for kind, anchor, value in found:
            key = (doc, line, kind, anchor, value)
            if key in seen or len(anchor) > 60 or not anchor.strip():
                continue
            seen.add(key)
            statement = {
                "default": f"The default of `{anchor}` is {value}.",
                "exit": f"Exit code {value} is used for: {sent}",
                "number": f"`{anchor}` uses the value {value} as stated: {sent}",
                "bool": f"For `{anchor}`, the value is {value}, as stated: {sent}",
                "stream": f"`{anchor}` writes to {value}, as stated: {sent}",
            }[kind]
            out.append(Fact(f"{repo}:{doc}:{line}:{kind}:{anchor[:20]}:{value[:12]}", repo, doc, line, kind, anchor, value, statement, sent, "supported"))
    return out


def flip_value(kind: str, value: str, rng: random.Random) -> str | None:
    if kind in ("exit", "number") or value.isdigit():
        n = int(value)
        return str(n + 1 if n < 9 else n + 10)
    if value in ("true", "false"):
        return "false" if value == "true" else "true"
    if value in ("stdout", "stderr"):
        return "stderr" if value == "stdout" else "stdout"
    swaps = {"current directory": "home directory", "high": "low", "low": "high", "medium": "high", ".": "src", "audit": "ablation",
             "3.12": "3.11", "README.md": "USAGE.md", "dontAsk": "acceptEdits"}
    if value in swaps:
        return swaps[value]
    m = re.search(r"\d+", value)
    if m:
        n = int(m.group(0))
        return value[: m.start()] + str(n + 1) + value[m.end():]
    if len(value) > 3 and value.replace("-", "").replace("_", "").isalnum():
        return value + "-x"
    return None


def flipped(fact: Fact, rng: random.Random) -> Fact | None:
    new = flip_value(fact.kind, fact.value, rng)
    if new is None or new == fact.value:
        return None
    statement = fact.statement.replace(fact.value, new, 1)
    sentence = fact.sentence.replace(fact.value, new, 1)
    if statement == fact.statement:
        return None
    return Fact(fact.id + "#flip", fact.repo, fact.doc, fact.line, fact.kind, fact.anchor, new, statement, sentence, "contradicted", fact.evidence)


# Code-side evidence per fact kind.


def evidence_for(source: je.Source, fact: Fact) -> str:
    anchor = fact.anchor.strip("`")
    stem = re.sub(r"^\[([\w-]+)\]\.(\w+)$", r"\2", anchor.split()[0] if anchor.startswith("shiplock ") else anchor)
    stem = stem.strip("()[]{}.,;:").split("(")[0]
    if fact.kind == "exit":
        # Definition sites first (with the docstring above them), then uses.
        defs = _collect(source, re.compile(r"^\s*EXIT_\w+\s*(?::\s*\w+)?\s*=\s*\d"), before=12, after=1, cap_lines=45)
        uses = _collect(source, re.compile(r"sys\.exit\(\s*\d|std::process::exit\(\s*\d|process\.exit\(\s*\d|return\s+EXIT_\w+|exit code \d|exit status \d", re.I), before=2, after=1, cap_lines=25)
        return "\n\n".join(x for x in (defs, uses) if x)
    if fact.kind == "default":
        parts = []
        if stem.startswith("--"):
            parts.append(_collect(source, re.compile(re.escape(f'"{stem}"')), before=1, after=8, cap_lines=30))
        else:
            last = stem.split(".")[-1]
            parts.append(_collect(source, re.compile(rf"\b{re.escape(last)}\b.*\b(?:default|DEFAULT)\b|\b(?:default|DEFAULT)\w*\b.*\b{re.escape(last)}\b|\"{re.escape(last)}\""), before=2, after=6, cap_lines=40))
        return "\n\n".join(p for p in parts if p)
    last = stem.split(".")[-1]
    if stem.startswith("--"):
        return _collect(source, re.compile(re.escape(f'"{stem}"')), before=1, after=8, cap_lines=30)
    if len(last) < 3:
        return ""
    return _collect(source, re.compile(rf"(?<![\w-]){re.escape(last)}(?![\w-])"), before=3, after=6, cap_lines=45)


def _ordered(source: je.Source) -> list[tuple[str, list[str]]]:
    """Package sources before scripts, tests, and CI files."""
    def rank(rel: str) -> int:
        if rel.startswith(("tests/", "test/", "scripts/", ".github/", "eval/", "bench/")):
            return 2
        return 0 if rel.endswith((".py", ".rs", ".ts", ".js", ".go")) else 1
    return sorted(source.files.items(), key=lambda kv: (rank(kv[0]), kv[0]))


def _collect(source: je.Source, pattern: re.Pattern, before: int, after: int, cap_lines: int) -> str:
    chunks: list[str] = []
    total = 0
    for rel, lines in _ordered(source):
        hits = [i for i, line in enumerate(lines, start=1) if pattern.search(line)]
        if not hits:
            continue
        spans: list[list[int]] = []
        for i in hits[:6]:
            s, e = max(1, i - before), min(len(lines), i + after)
            if spans and s <= spans[-1][1] + 1:
                spans[-1][1] = max(spans[-1][1], e)
            else:
                spans.append([s, e])
        for s, e in spans:
            chunk = lines[s - 1 : e]
            total += len(chunk)
            chunks.append(f"# {rel}:{s}-{e}\n" + "\n".join(chunk))
            if total >= cap_lines:
                return "\n\n".join(chunks)
    return "\n\n".join(chunks)


def build_facts(root: Path, repo: str, rng: random.Random, limit: int) -> list[Fact]:
    tracked = je.git_files(root)
    docs = je.public_docs(root)
    source = je.Source(root, tracked, docs)
    facts: list[Fact] = []
    for doc in docs:
        text = (root / doc).read_text(encoding="utf-8", errors="replace")
        facts.extend(extract_facts(repo, doc, text))
    kept = []
    for f in facts:
        f.evidence = evidence_for(source, f)
        if f.evidence and len(f.evidence) > 40:
            kept.append(f)
    rng.shuffle(kept)
    kept = kept[:limit]
    flips = [g for g in (flipped(f, rng) for f in kept) if g is not None]
    return kept + flips


# Questions


def request(batch: list[Fact], form: str) -> dict:
    state = [{"statement": f.statement, "anchor": f.anchor, "value": f.value, "evidence": je.cap_text(f.evidence, 1500)} for f in batch]
    q: dict[str, dict] = {}
    for i in range(len(batch)):
        ref = f"`facts[{i}]`"
        if form == "noul_pair":
            q[f"s{i}"] = {"type": "noul", "instructions": f"Does {ref}.evidence (source code) show that {ref}.statement is true: that {ref}.anchor has the value {ref}.value as the statement says?"}
            q[f"k{i}"] = {"type": "noul", "instructions": f"Does {ref}.evidence (source code) show that {ref}.statement is false: that {ref}.anchor has a different value from {ref}.value, or that the stated value belongs to something else?"}
        else:
            q[f"c{i}"] = {"type": "choice", "instructions": f"{ref}.statement is a fact the documentation states about {ref}.anchor. {ref}.evidence is the source code that carries that value. Does the evidence support the statement as written?", "criteria": je.CRITERIA}
    return {"state": {"facts": state}, "model": PIN, "questions": q}


def batches(facts: list[Fact]) -> list[list[Fact]]:
    out, cur, size = [], [], 0
    for f in facts:
        n = len(f.statement) + min(len(f.evidence), 6000) + 500
        if cur and size + n > je.STATE_BUDGET_CHARS // 2:
            out.append(cur)
            cur, size = [], 0
        cur.append(f)
        size += n
    if cur:
        out.append(cur)
    return out


def interpret(form: str, answers: dict, i: int) -> dict | None:
    return je.interpret("choice_raw" if form == "choice" else "noul_pair", answers, i)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", required=True)
    ap.add_argument("--repeats", type=int, default=15)
    ap.add_argument("--facts-per-repo", type=int, default=60)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="eval-runs/judge-eval", help="where the raw calls, facts, summary, and report go")
    ap.add_argument("--keys-file", help="a TOML keys file to read the typesafe key from when TYPESAFE_API_KEY is unset")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    key = je.api_key(args.keys_file)
    rng = random.Random(args.seed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()

    sets: dict[str, list[Fact]] = {}
    for r in args.repo:
        root = Path(r).resolve()
        sets[root.name] = build_facts(root, root.name, rng, args.facts_per_repo)
        fs = sets[root.name]
        kinds = {}
        for f in fs:
            if f.label == "supported":
                kinds[f.kind] = kinds.get(f.kind, 0) + 1
        print(f"{root.name}: {sum(f.label == 'supported' for f in fs)} facts ({kinds}), {sum(f.label == 'contradicted' for f in fs)} flipped", flush=True)

    spend = je.Spend()
    records: list[je.Record] = []
    raw_path = out_dir / f"{stamp}-fact-calls.jsonl"
    with raw_path.open("w", encoding="utf-8") as raw, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for form in ("noul_pair", "choice"):
            jobs = []
            for repo, facts in sets.items():
                for repeat in range(args.repeats if form == "noul_pair" else 1):
                    for batch in batches(facts):
                        jobs.append((repo, repeat, batch))

            def one(job):
                repo, repeat, batch = job
                return repo, repeat, batch, je.call(key, request(batch, form))

            for repo, repeat, batch, resp in pool.map(one, jobs):
                spend.calls += 1
                spend.input_tokens += int(resp.get("usage", {}).get("input_tokens") or 0)
                if resp.get("server_ms"):
                    spend.server_ms.append(resp["server_ms"])
                spend.wall_ms.append(resp.get("wall_ms", 0))
                if resp.get("model"):
                    spend.models.add(resp["model"])
                raw.write(json.dumps({"form": form, "repo": repo, "repeat": repeat, "ids": [f.id for f in batch], "status": resp.get("status"),
                                      "error": resp.get("error"), "usage": resp.get("usage"), "server_ms": resp.get("server_ms"), "answers": resp.get("answers")}) + "\n")
                if resp.get("status") != 200:
                    spend.errors += 1
                    continue
                for i, f in enumerate(batch):
                    a = interpret(form, resp["answers"], i)
                    if a:
                        records.append(je.Record("fact", repo, form, repeat, f.id, f.label, f.kind, a["pick"], a["p_contradicted"], a["p_supported"], a["p_insufficient"], a["confidence"]))
            print(f"{form}: done ({spend.calls} calls)", flush=True)

    noul = [r for r in records if r.config == "noul_pair"]
    choice = [r for r in records if r.config == "choice"]
    by_kind = {}
    for kind in sorted({r.flip for r in noul}):
        rs = [r for r in noul if r.flip == kind]
        by_kind[kind] = next(iter(je.summarise(rs).values()), {})
    per_repo = {repo: {**next(iter(je.summarise([r for r in noul if r.repo == repo]).values()), {}), **je.stability([r for r in noul if r.repo == repo])} for repo in sets}
    cost = spend.input_tokens * je.PRICE_PER_M / 1e6
    report = {
        "date": stamp, "model_pin": PIN, "models_seen": sorted(spend.models),
        "facts": {repo: {"supported": sum(f.label == "supported" for f in fs), "contradicted": sum(f.label == "contradicted" for f in fs)} for repo, fs in sets.items()},
        "noul_pair_pooled": {**next(iter(je.summarise(noul).values()), {}), **je.stability(noul)},
        "choice_one_pass": next(iter(je.summarise(choice).values()), {}),
        "by_kind_noul": by_kind, "per_repo_noul": per_repo,
        "thresholds_pooled": je.threshold_table(noul),
        "thresholds_per_repo": {repo: je.threshold_table([r for r in noul if r.repo == repo]) for repo in sets},
        "spend": {"calls": spend.calls, "errors": spend.errors, "input_tokens": spend.input_tokens, "usd": round(cost, 4),
                  "server_ms_median": je.pct(spend.server_ms, 0.5), "server_ms_p99": je.pct(spend.server_ms, 0.99)},
    }
    (out_dir / f"{stamp}-fact-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / f"{stamp}-facts.json").write_text(json.dumps({r: [asdict(f) for f in fs] for r, fs in sets.items()}, indent=2), encoding="utf-8")
    write_markdown(out_dir / f"{stamp}-judge-fact-eval.md", report)
    print(json.dumps(report["spend"]))
    return 0


def write_markdown(path: Path, rep: dict) -> None:
    L = [f"# Judge eval 2: atomic facts, {rep['date']}", "",
         f"Model pin `{rep['model_pin']}`; seen: {', '.join(rep['models_seen'])}. Harness `scripts/judge_fact_eval.py`; raw calls and facts beside this report.", "",
         "## Labeled set", "", "| Repo | Supported facts | Flipped |", "|---|---|---|"]
    for repo, c in rep["facts"].items():
        L.append(f"| {repo} | {c['supported']} | {c['contradicted']} |")
    p = rep["noul_pair_pooled"]
    L += ["", "## Noul pair, 15 repeats, pooled", "", "```json", json.dumps(p, indent=2), "```", "",
          "## Choice form, one pass, pooled (for comparison)", "", "```json", json.dumps(rep["choice_one_pass"], indent=2), "```", "",
          "## Noul pair by fact kind", "", "| Kind | n | Supported correct | Contradicted caught | Supported called contradicted | Insufficient | Brier |", "|---|---|---|---|---|---|---|"]
    for kind, s in rep["by_kind_noul"].items():
        L.append(f"| {kind} | {s.get('n')} | {s.get('acc_supported')} | {s.get('acc_contradicted')} | {s.get('false_contradicted')} | {s.get('insufficient_rate')} | {s.get('brier')} |")
    L += ["", "## Noul pair per repo", "", "| Repo | Supported correct | Contradicted caught | Supported called contradicted | Insufficient | Brier | Stable picks | p range median | p range max |", "|---|---|---|---|---|---|---|---|---|"]
    for repo, s in rep["per_repo_noul"].items():
        L.append(f"| {repo} | {s.get('acc_supported')} | {s.get('acc_contradicted')} | {s.get('false_contradicted')} | {s.get('insufficient_rate')} | {s.get('brier')} | {s.get('stable_fraction')} | {s.get('p_range_median')} | {s.get('p_range_max')} |")
    L += ["", "## Thresholds on P(false), pooled", "", "| Threshold | Catch rate | False-fail rate |", "|---|---|---|"]
    for row in rep["thresholds_pooled"]:
        L.append(f"| {row['threshold']} | {row['catch_rate']} | {row['false_fail_rate']} |")
    L += ["", "Per repo (false-fail / catch):", "", "| Repo | " + " | ".join(str(r["threshold"]) for r in rep["thresholds_pooled"]) + " |", "|---|" + "---|" * len(rep["thresholds_pooled"])]
    for repo, rows in rep["thresholds_per_repo"].items():
        L.append(f"| {repo} | " + " | ".join(f"{r['false_fail_rate']} / {r['catch_rate']}" for r in rows) + " |")
    s = rep["spend"]
    L += ["", "## Spend", "", f"{s['calls']} calls ({s['errors']} non-200), {s['input_tokens']:,} input tokens, ${s['usd']}. Server ms median {s['server_ms_median']}, p99 {s['server_ms_p99']}.", ""]
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
