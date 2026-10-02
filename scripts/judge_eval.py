#!/usr/bin/env python3
"""Eval harness for the judge layer: does a judgment model catch doc-vs-code drift?

Extracts anchored claims from each repo's public docs, gathers code evidence
for them deterministically, makes synthetic contradictions by flipping a value
in the claim, and runs the labeled set through Jev under several claim and
evidence forms (experiment one), then the winning form under repeats
(experiment two). Coverage questions (experiment three) ask, per CLI flag the
code declares, whether the docs describe it.

    python scripts/judge_eval.py --repo PATH [--repo PATH ...] --repeats 15 --out DIR

Reads the key from TYPESAFE_API_KEY, or from a TOML keys file passed with
--keys-file (a ``[[targets]]`` list with ``provider = "typesafe"``). Never
prints it. Writes raw call records as JSONL and a markdown report under --out.
Every call is live and billable; the report totals the spend.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # 3.10
    import tomli as tomllib  # type: ignore[no-redef]

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
PIN = "jev-1.13.0"
PRICE_PER_M = 0.042
STATE_BUDGET_CHARS = 28_000 * 4
SOURCE_SUFFIXES = (".py", ".rs", ".js", ".ts", ".go", ".toml", ".yml", ".yaml", ".sh")
CAPS = {"small": 300, "large": 1500}

CRITERIA = {
    "supported": {"what": "the evidence shows the claim holds as written"},
    "contradicted": {
        "what": "the evidence shows the claim is false as written: a different value, name, flag, default, or behaviour",
        "not_for": "evidence that simply doesn't mention what the claim says",
    },
    "insufficient": {"what": "the evidence doesn't settle the claim either way"},
}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def api_key(keys_file: str | None = None) -> str:
    env = os.environ.get("TYPESAFE_API_KEY")
    if env:
        return env
    keys = Path(keys_file) if keys_file else None
    if keys and keys.is_file():
        for t in tomllib.loads(keys.read_text(encoding="utf-8")).get("targets", []):
            if t.get("provider") == "typesafe" and not str(t.get("key", "")).endswith("REPLACE-ME"):
                return t["key"]
    sys.exit("no TypeSafe key: set TYPESAFE_API_KEY")


def call(key: str, body: dict, retries: int = 4) -> dict:
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            ENDPOINT, data=data, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode())
                headers = dict(resp.headers)
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
            headers = dict(exc.headers)
            try:
                payload = json.loads(exc.read().decode())
            except Exception:
                payload = {}
            if status in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(float(headers.get("Retry-After", 0) or 0) or 1.5 * (attempt + 1))
                continue
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"status": 0, "error": str(exc), "answers": {}, "usage": {}, "wall_ms": 0}
        wall = round((time.perf_counter() - start) * 1000)
        return {
            "status": status,
            "model": payload.get("model"),
            "answers": payload.get("answers") or {},
            "usage": payload.get("usage") or {},
            "error": payload.get("detail") if status != 200 else None,
            "request_id": headers.get("x-typesafe-request-id"),
            "server_ms": int(headers.get("x-envoy-upstream-service-time") or 0),
            "wall_ms": wall,
        }
    return {"status": 0, "error": "retries exhausted", "answers": {}, "usage": {}, "wall_ms": 0}


# --------------------------------------------------------------------------
# Claims and evidence
# --------------------------------------------------------------------------


@dataclass
class Claim:
    id: str
    repo: str
    doc: str
    line: int
    text: str
    anchors: list[str]
    label: str  # supported | contradicted
    flip: str = ""
    evidence_bare: str = ""
    evidence_prose: str = ""


def git_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def public_docs(root: Path) -> list[str]:
    cfg = root / "shiplock.toml"
    if cfg.is_file():
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        docs = data.get("docs", {}).get("public")
        if docs:
            return [d for d in docs if (root / d).is_file()]
    return [d for d in ("README.md", "USAGE.md", "ARCHITECTURE.md") if (root / d).is_file()]


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'(])")
_TOKEN = re.compile(r"`([^`\n]{1,80})`")


def sentences(text: str) -> list[tuple[int, str]]:
    """(line, sentence) for prose sentences outside fenced code blocks."""
    out: list[tuple[int, str]] = []
    in_fence = False
    para: list[tuple[int, str]] = []

    def flush() -> None:
        if not para:
            return
        first = para[0][0]
        joined = " ".join(s for _, s in para)
        for sent in _SENTENCE.split(joined):
            sent = sent.strip()
            if len(sent) >= 25:
                out.append((first, sent))
        para.clear()

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence or not line or line.startswith("|") or line.startswith("#"):
            flush()
            continue
        para.append((i, re.sub(r"^[-*\d.]+\s+", "", line)))
    flush()
    return out


class Source:
    """The repo's source files, read once, with simple lookups."""

    def __init__(self, root: Path, tracked: list[str], docs: list[str]) -> None:
        self.root = root
        self.tracked = set(tracked)
        self.files: dict[str, list[str]] = {}
        for rel in tracked:
            if rel in docs or not rel.endswith(SOURCE_SUFFIXES):
                continue
            try:
                data = (root / rel).read_bytes()
            except OSError:
                continue
            if b"\x00" in data:
                continue
            self.files[rel] = data.decode("utf-8", errors="replace").splitlines()

    def resolve(self, token: str) -> list[tuple[str, int, int]]:
        """Evidence locations (path, start, end) for one backticked token."""
        token = token.strip()
        if token in self.tracked and not token.endswith(".md"):
            lines = self.files.get(token)
            return [(token, 1, min(len(lines), 40))] if lines else []
        if token.endswith(".md") and token in self.tracked:
            return []  # a doc naming a doc: no code evidence
        word = token.split()[0] if token.startswith("shiplock ") or " " in token else token
        word = re.sub(r"^\[([\w-]+)\]\.(\w+)$", r"\2", word)  # [scan].refs -> refs
        word = word.strip("()[]{}.,;:").split("(")[0]
        if not word or len(word) < 3:
            return []
        if word.startswith("--"):
            needle = f'"{word}"'
            pattern = re.compile(re.escape(needle))
        elif re.fullmatch(r"[A-Za-z_][\w.]*", word):
            last = word.split(".")[-1]
            pattern = re.compile(rf"^\s*(?:def |class |async def |pub fn |fn |const |static |let |export (?:const|function) |function )?{re.escape(last)}\b\s*[(:=]|^\s*{re.escape(last)}\s*[:=]|\"{re.escape(last)}\"")
        else:
            return []
        found: list[tuple[str, int, int]] = []
        for rel, lines in self.files.items():
            for i, line in enumerate(lines, start=1):
                if pattern.search(line):
                    found.append((rel, max(1, i - 3), min(len(lines), i + 15)))
                    break
            if len(found) >= 3:
                break
        return found

    def excerpt(self, loc: tuple[str, int, int], prose: bool) -> str:
        rel, start, end = loc
        lines = self.files[rel][start - 1 : end]
        if not prose:
            kept = []
            in_doc = False
            for line in lines:
                s = line.strip()
                if s.startswith(('"""', "'''")):
                    if s.count('"""') == 2 or s.count("'''") == 2:
                        continue
                    in_doc = not in_doc
                    continue
                if in_doc or s.startswith("#") or s.startswith("//"):
                    continue
                kept.append(re.sub(r"\s+#.*$", "", line))
            lines = kept
        return f"# {rel}:{start}-{end}\n" + "\n".join(lines)


def build_claims(root: Path, repo: str, limit: int, rng: random.Random) -> list[Claim]:
    tracked = git_files(root)
    docs = public_docs(root)
    source = Source(root, tracked, docs)
    claims: list[Claim] = []
    for doc in docs:
        text = (root / doc).read_text(encoding="utf-8", errors="replace")
        for line, sent in sentences(text):
            tokens = [t for t in _TOKEN.findall(sent)]
            if not tokens:
                continue
            locs: list[tuple[str, int, int]] = []
            anchors: list[str] = []
            for tok in tokens:
                r = source.resolve(tok)
                if r:
                    anchors.append(tok)
                    for loc in r:
                        if loc not in locs:
                            locs.append(loc)
            if not locs:
                continue
            claims.append(
                Claim(
                    id=f"{repo}:{doc}:{line}",
                    repo=repo, doc=doc, line=line, text=sent, anchors=anchors, label="supported",
                    evidence_bare="\n\n".join(source.excerpt(l, prose=False) for l in locs),
                    evidence_prose="\n\n".join(source.excerpt(l, prose=True) for l in locs),
                )
            )
    rng.shuffle(claims)
    claims = claims[:limit]
    flipped: list[Claim] = []
    for c in claims:
        f = flip(c.text, rng)
        if f is not None:
            new_text, how = f
            flipped.append(Claim(
                id=c.id + "#flip", repo=c.repo, doc=c.doc, line=c.line, text=new_text, anchors=c.anchors,
                label="contradicted", flip=how, evidence_bare=c.evidence_bare, evidence_prose=c.evidence_prose,
            ))
    return claims + flipped


_FLIPS: list[tuple[str, str, str]] = [
    ("stdout", "stderr", "stream"), ("stderr", "stdout", "stream"),
    ("true", "false", "bool"), ("false", "true", "bool"),
    ("never", "always", "polarity"), ("always", "never", "polarity"),
    ("default", "only", "wording"),
]


def flip(text: str, rng: random.Random) -> tuple[str, str] | None:
    """One synthetic contradiction: a number, a stream, a bool, a flag, or a polarity."""
    m = re.search(r"(?<![\w.])(\d+)(?![\w.])", text)
    if m and m.group(1) not in ("2026", "2025"):
        n = int(m.group(1))
        return text[: m.start()] + str(n + 1 if n < 9 else n + 10) + text[m.end():], "number"
    for a, b, how in _FLIPS:
        if re.search(rf"\b{a}\b", text):
            return re.sub(rf"\b{a}\b", b, text, count=1), how
    m = re.search(r"`(--[a-z][\w-]*)`", text)
    if m:
        flag = m.group(1)
        new = flag[:-1] if len(flag) > 4 else flag + "-x"
        return text.replace(f"`{flag}`", f"`{new}`", 1), "flag"
    return None


# --------------------------------------------------------------------------
# Question forms
# --------------------------------------------------------------------------


def cap_text(text: str, cap_tokens: int) -> str:
    limit = cap_tokens * 4
    return text if len(text) <= limit else text[:limit] + "\n# [cut]"


def build_request(claims: list[Claim], form: str, evidence: str, cap: str) -> tuple[dict, list[str]]:
    """One call's body for a batch of claims under one configuration."""
    state_claims = []
    questions: dict[str, dict] = {}
    ids = []
    for i, c in enumerate(claims):
        ev = cap_text(c.evidence_prose if evidence == "prose" else c.evidence_bare, CAPS[cap])
        entry = {"text": c.text, "evidence": ev}
        if form != "choice_raw":
            entry["anchors"] = c.anchors
        state_claims.append(entry)
        ref = f"`claims[{i}]`"
        if form == "choice_raw":
            questions[f"c{i}"] = {"type": "choice", "instructions": f"Does {ref}.evidence support {ref}.text as written?", "criteria": CRITERIA}
        elif form == "choice_anchored":
            questions[f"c{i}"] = {
                "type": "choice",
                "instructions": f"{ref}.text is a sentence from the documentation; it refers to the code named in {ref}.anchors. {ref}.evidence is that code. Does the evidence support the sentence as written?",
                "criteria": CRITERIA,
            }
        else:  # noul_pair
            questions[f"s{i}"] = {"type": "noul", "instructions": f"Does {ref}.evidence (the code named in {ref}.anchors) show that {ref}.text is true as written?"}
            questions[f"k{i}"] = {"type": "noul", "instructions": f"Does {ref}.evidence (the code named in {ref}.anchors) show that {ref}.text is false as written: a different value, name, flag, default, or behaviour than the sentence states?"}
        ids.append(c.id)
    return {"state": {"claims": state_claims}, "model": PIN, "questions": questions}, ids


def batches(claims: list[Claim], evidence: str, cap: str) -> list[list[Claim]]:
    out: list[list[Claim]] = []
    cur: list[Claim] = []
    size = 0
    for c in claims:
        ev = cap_text(c.evidence_prose if evidence == "prose" else c.evidence_bare, CAPS[cap])
        n = len(c.text) + len(ev) + 400
        if cur and size + n > STATE_BUDGET_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(c)
        size += n
    if cur:
        out.append(cur)
    return out


def interpret(form: str, answers: dict, i: int) -> dict | None:
    """Normalise one claim's answer to {pick, p_contradicted, p_supported, p_insufficient, confidence}."""
    if form.startswith("choice"):
        a = answers.get(f"c{i}")
        if not a:
            return None
        p = a.get("probabilities", {})
        return {"pick": a.get("choice"), "p_contradicted": p.get("contradicted", 0.0), "p_supported": p.get("supported", 0.0),
                "p_insufficient": p.get("insufficient", 0.0), "confidence": a.get("confidence")}
    s, k = answers.get(f"s{i}"), answers.get(f"k{i}")
    if not s or not k:
        return None
    ps, pk = s.get("noul", 0.0), k.get("noul", 0.0)
    if pk >= 0.5 and pk >= ps:
        pick = "contradicted"
    elif ps >= 0.5:
        pick = "supported"
    else:
        pick = "insufficient"
    return {"pick": pick, "p_contradicted": pk, "p_supported": ps, "p_insufficient": max(0.0, 1 - max(ps, pk)), "confidence": None}


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


@dataclass
class Record:
    experiment: str
    repo: str
    config: str
    repeat: int
    claim_id: str
    label: str
    flip: str
    pick: str | None
    p_contradicted: float
    p_supported: float
    p_insufficient: float
    confidence: float | None


@dataclass
class Spend:
    calls: int = 0
    input_tokens: int = 0
    server_ms: list[int] = field(default_factory=list)
    wall_ms: list[int] = field(default_factory=list)
    errors: int = 0
    models: set = field(default_factory=set)


def run_config(key: str, claims: list[Claim], experiment: str, form: str, evidence: str, cap: str,
               repeats: int, spend: Spend, records: list[Record], pool: ThreadPoolExecutor, raw_out) -> None:
    config = f"{form}/{evidence}/{cap}"
    jobs = []
    for repeat in range(repeats):
        for batch in batches(claims, evidence, cap):
            body, ids = build_request(batch, form, evidence, cap)
            jobs.append((repeat, batch, body, ids))

    def one(job):
        repeat, batch, body, ids = job
        return repeat, batch, ids, call(key, body)

    for repeat, batch, ids, resp in pool.map(one, jobs):
        spend.calls += 1
        spend.input_tokens += int(resp.get("usage", {}).get("input_tokens") or 0)
        if resp.get("server_ms"):
            spend.server_ms.append(resp["server_ms"])
        spend.wall_ms.append(resp.get("wall_ms", 0))
        if resp.get("model"):
            spend.models.add(resp["model"])
        raw_out.write(json.dumps({"experiment": experiment, "config": config, "repeat": repeat, "ids": ids,
                                  "status": resp.get("status"), "error": resp.get("error"), "request_id": resp.get("request_id"),
                                  "usage": resp.get("usage"), "server_ms": resp.get("server_ms"), "wall_ms": resp.get("wall_ms"),
                                  "answers": resp.get("answers")}) + "\n")
        if resp.get("status") != 200:
            spend.errors += 1
            continue
        for i, c in enumerate(batch):
            r = interpret(form, resp["answers"], i)
            if r is None:
                continue
            records.append(Record(experiment, c.repo, config, repeat, c.id, c.label, c.flip, r["pick"],
                                  r["p_contradicted"], r["p_supported"], r["p_insufficient"], r["confidence"]))


def coverage_items(root: Path, docs: list[str]) -> list[tuple[str, bool]]:
    """CLI flags the code declares, each with whether any public doc names it literally."""
    flags: set[str] = set()
    for rel in git_files(root):
        if not rel.endswith(".py"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"add_argument\(\s*\"(--[a-z][\w-]*)\"", text):
            flags.add(m.group(1))
    doc_text = "\n".join((root / d).read_text(encoding="utf-8", errors="replace") for d in docs)
    return sorted((f, f in doc_text) for f in flags)


def run_coverage(key: str, root: Path, repo: str, docs: list[str], spend: Spend, raw_out) -> list[dict]:
    items = coverage_items(root, docs)
    if not items:
        return []
    out = []
    for doc in docs:
        text = (root / doc).read_text(encoding="utf-8", errors="replace")
        if len(text) > STATE_BUDGET_CHARS:
            text = text[:STATE_BUDGET_CHARS]
        questions = {f"f{i}": {"type": "noul", "instructions": f"Does `doc` describe the command-line flag `{flag}`: what it does or when to use it, not merely a string that happens to contain it?"}
                     for i, (flag, _) in enumerate(items)}
        resp = call(key, {"state": {"doc": text}, "model": PIN, "questions": questions})
        spend.calls += 1
        spend.input_tokens += int(resp.get("usage", {}).get("input_tokens") or 0)
        if resp.get("server_ms"):
            spend.server_ms.append(resp["server_ms"])
        raw_out.write(json.dumps({"experiment": "coverage", "repo": repo, "doc": doc, "status": resp.get("status"),
                                  "usage": resp.get("usage"), "answers": resp.get("answers")}) + "\n")
        if resp.get("status") != 200:
            spend.errors += 1
            continue
        for i, (flag, present) in enumerate(items):
            a = resp["answers"].get(f"f{i}")
            if a:
                out.append({"repo": repo, "doc": doc, "flag": flag, "literal": present, "p": a.get("noul", 0.0)})
    # per flag: documented anywhere = max over docs
    by_flag: dict[str, dict] = {}
    for r in out:
        cur = by_flag.setdefault(r["flag"], {"repo": repo, "flag": r["flag"], "literal": False, "p": 0.0})
        cur["literal"] = cur["literal"] or r["literal"]
        cur["p"] = max(cur["p"], r["p"])
    return list(by_flag.values())


# --------------------------------------------------------------------------
# Metrics and report
# --------------------------------------------------------------------------


def brier(records: list[Record]) -> float | None:
    if not records:
        return None
    return round(statistics.mean((r.p_contradicted - (1.0 if r.label == "contradicted" else 0.0)) ** 2 for r in records), 4)


def summarise(records: list[Record]) -> dict:
    by = {}
    for r in records:
        by.setdefault(r.config, []).append(r)
    out = {}
    for config, rs in by.items():
        sup = [r for r in rs if r.label == "supported"]
        con = [r for r in rs if r.label == "contradicted"]
        out[config] = {
            "n": len(rs),
            "acc_supported": round(sum(r.pick == "supported" for r in sup) / len(sup), 3) if sup else None,
            "acc_contradicted": round(sum(r.pick == "contradicted" for r in con) / len(con), 3) if con else None,
            "false_contradicted": round(sum(r.pick == "contradicted" for r in sup) / len(sup), 3) if sup else None,
            "insufficient_rate": round(sum(r.pick == "insufficient" for r in rs) / len(rs), 3),
            "brier": brier(rs),
        }
    return out


def stability(records: list[Record]) -> dict:
    by: dict[str, list[Record]] = {}
    for r in records:
        by.setdefault(r.claim_id, []).append(r)
    stable = 0
    ranges = []
    for rs in by.values():
        picks = {r.pick for r in rs}
        stable += len(picks) == 1
        ps = [r.p_contradicted for r in rs]
        ranges.append(max(ps) - min(ps))
    n = len(by)
    return {"claims": n, "stable_fraction": round(stable / n, 3) if n else None,
            "p_range_median": round(statistics.median(ranges), 3) if ranges else None,
            "p_range_max": round(max(ranges), 3) if ranges else None}


def threshold_table(records: list[Record]) -> list[dict]:
    rows = []
    for t in (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95):
        sup = [r for r in records if r.label == "supported"]
        con = [r for r in records if r.label == "contradicted"]
        fp = sum(r.p_contradicted >= t for r in sup) / len(sup) if sup else 0
        tp = sum(r.p_contradicted >= t for r in con) / len(con) if con else 0
        rows.append({"threshold": t, "catch_rate": round(tp, 3), "false_fail_rate": round(fp, 3)})
    return rows


def pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", action="append", required=True, help="repo root (repeatable)")
    ap.add_argument("--repeats", type=int, default=15)
    ap.add_argument("--claims-per-repo", type=int, default=100, help="supported claims sampled per repo (flips add up to as many)")
    ap.add_argument("--repeat-claims", type=int, default=60, help="claims per repo carried into the repeat run")
    ap.add_argument("--out", default="eval-runs/judge-eval", help="where the raw calls, claims, summary, and report go")
    ap.add_argument("--keys-file", help="a TOML keys file to read the typesafe key from when TYPESAFE_API_KEY is unset")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    key = api_key(args.keys_file)
    rng = random.Random(args.seed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    raw_path = out_dir / f"{stamp}-calls.jsonl"
    spend = Spend()
    records: list[Record] = []
    coverage: list[dict] = []
    claim_sets: dict[str, list[Claim]] = {}

    # Pre-flight: one minimal Noul, so a dead key fails here.
    pre = call(key, {"state": "ok", "model": PIN, "questions": {"q": {"type": "noul", "instructions": "Is the state the word ok?"}}})
    if pre.get("status") != 200:
        sys.exit(f"pre-flight failed: status {pre.get('status')}")
    spend.calls += 1
    spend.input_tokens += int(pre.get("usage", {}).get("input_tokens") or 0)

    for repo_path in args.repo:
        root = Path(repo_path).resolve()
        repo = root.name
        claim_sets[repo] = build_claims(root, repo, args.claims_per_repo, rng)
        print(f"{repo}: {sum(c.label == 'supported' for c in claim_sets[repo])} supported claims, "
              f"{sum(c.label == 'contradicted' for c in claim_sets[repo])} flipped", flush=True)

    forms = ("choice_raw", "choice_anchored", "noul_pair")
    with raw_path.open("w", encoding="utf-8") as raw_out, ThreadPoolExecutor(max_workers=args.workers) as pool:
        # Experiment one: form × evidence × cap, one pass.
        for repo, claims in claim_sets.items():
            for form in forms:
                for evidence in ("bare", "prose"):
                    for cap in CAPS:
                        run_config(key, claims, "form", form, evidence, cap, 1, spend, records, pool, raw_out)
            print(f"{repo}: form experiment done ({spend.calls} calls so far)", flush=True)

        form_records = [r for r in records if r.experiment == "form"]
        summary = summarise(form_records)
        # Winner: highest (acc_contradicted + acc_supported) with false_contradicted penalised.
        def score(cfg: str) -> float:
            s = summary[cfg]
            return (s["acc_contradicted"] or 0) + (s["acc_supported"] or 0) - 2 * (s["false_contradicted"] or 0)
        winner = max(summary, key=score)
        w_form, w_evidence, w_cap = winner.split("/")
        print(f"winning config: {winner}", flush=True)

        # Experiment two: repeats on the winner, a subset per repo.
        for repo, claims in claim_sets.items():
            subset = claims[: args.repeat_claims // 2] + [c for c in claims if c.label == "contradicted"][: args.repeat_claims // 2]
            run_config(key, subset, "repeat", w_form, w_evidence, w_cap, args.repeats, spend, records, pool, raw_out)
            print(f"{repo}: repeats done ({spend.calls} calls so far)", flush=True)

        # Experiment three: coverage.
        for repo_path in args.repo:
            root = Path(repo_path).resolve()
            coverage.extend(run_coverage(key, root, root.name, public_docs(root), spend, raw_out))

    # Report
    repeat_records = [r for r in records if r.experiment == "repeat"]
    per_repo_repeat = {}
    for repo in claim_sets:
        rs = [r for r in repeat_records if r.repo == repo]
        per_repo_repeat[repo] = {**next(iter(summarise(rs).values()), {}), **stability(rs)} if rs else {}
    cov_rows = []
    for t in (0.5, 0.7, 0.9):
        doc = [c for c in coverage if c["literal"]]
        undoc = [c for c in coverage if not c["literal"]]
        cov_rows.append({
            "threshold": t,
            "documented_recognised": round(sum(c["p"] >= t for c in doc) / len(doc), 3) if doc else None,
            "undocumented_flagged": round(sum(c["p"] < t for c in undoc) / len(undoc), 3) if undoc else None,
            "n_documented": len(doc), "n_undocumented": len(undoc),
        })
    cost = spend.input_tokens * PRICE_PER_M / 1_000_000
    report = {
        "date": stamp, "model_pin": PIN, "models_seen": sorted(spend.models), "repos": list(claim_sets),
        "claims": {repo: {"supported": sum(c.label == "supported" for c in cs), "contradicted": sum(c.label == "contradicted" for c in cs),
                          "flip_kinds": sorted({c.flip for c in cs if c.flip})} for repo, cs in claim_sets.items()},
        "experiment_one": summary, "winner": winner,
        "experiment_two_pooled": {**next(iter(summarise(repeat_records).values()), {}), **stability(repeat_records)} if repeat_records else {},
        "experiment_two_per_repo": per_repo_repeat,
        "thresholds_pooled": threshold_table(repeat_records),
        "thresholds_per_repo": {repo: threshold_table([r for r in repeat_records if r.repo == repo]) for repo in claim_sets},
        "coverage": cov_rows,
        "spend": {"calls": spend.calls, "errors": spend.errors, "input_tokens": spend.input_tokens, "usd": round(cost, 4),
                  "server_ms_median": pct(spend.server_ms, 0.5), "server_ms_p99": pct(spend.server_ms, 0.99),
                  "wall_ms_median": pct(spend.wall_ms, 0.5), "wall_ms_p99": pct(spend.wall_ms, 0.99)},
    }
    (out_dir / f"{stamp}-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / f"{stamp}-claims.json").write_text(json.dumps({r: [asdict(c) for c in cs] for r, cs in claim_sets.items()}, indent=2), encoding="utf-8")
    write_markdown(out_dir / f"{stamp}-judge-eval.md", report)
    print(json.dumps(report["spend"]))
    return 0


def write_markdown(path: Path, rep: dict) -> None:
    L = [f"# Judge eval, {rep['date']}", "",
         f"Model pin `{rep['model_pin']}`; models seen in responses: {', '.join(rep['models_seen']) or 'none'}. "
         f"Repos: {', '.join(rep['repos'])}. Harness: `scripts/judge_eval.py`; raw calls and claims beside this report.", "",
         "## Labeled set", "", "| Repo | Supported | Contradicted (synthetic) | Flip kinds |", "|---|---|---|---|"]
    for repo, c in rep["claims"].items():
        L.append(f"| {repo} | {c['supported']} | {c['contradicted']} | {', '.join(c['flip_kinds'])} |")
    L += ["", "## Experiment one: form × evidence × cap (one pass)", "",
          "| Config | n | Supported correct | Contradicted caught | Supported called contradicted | Insufficient rate | Brier |", "|---|---|---|---|---|---|---|"]
    for cfg, s in sorted(rep["experiment_one"].items()):
        L.append(f"| {cfg} | {s['n']} | {s['acc_supported']} | {s['acc_contradicted']} | {s['false_contradicted']} | {s['insufficient_rate']} | {s['brier']} |")
    L += ["", f"Winner by (caught + supported correct − 2 × false contradicted): `{rep['winner']}`.", "",
          "## Experiment two: repeats on the winner", "", "Pooled:", "", "```json", json.dumps(rep["experiment_two_pooled"], indent=2), "```", "",
          "| Repo | Supported correct | Contradicted caught | Supported called contradicted | Insufficient | Brier | Stable picks | p range median | p range max |", "|---|---|---|---|---|---|---|---|---|"]
    for repo, s in rep["experiment_two_per_repo"].items():
        if s:
            L.append(f"| {repo} | {s.get('acc_supported')} | {s.get('acc_contradicted')} | {s.get('false_contradicted')} | {s.get('insufficient_rate')} | {s.get('brier')} | {s.get('stable_fraction')} | {s.get('p_range_median')} | {s.get('p_range_max')} |")
    L += ["", "## Thresholds on P(contradicted), pooled", "", "| Threshold | Catch rate | False-fail rate |", "|---|---|---|"]
    for row in rep["thresholds_pooled"]:
        L.append(f"| {row['threshold']} | {row['catch_rate']} | {row['false_fail_rate']} |")
    L += ["", "Per repo, false-fail rate at each threshold:", "", "| Repo | " + " | ".join(str(r["threshold"]) for r in rep["thresholds_pooled"]) + " |",
          "|---|" + "---|" * len(rep["thresholds_pooled"])]
    for repo, rows in rep["thresholds_per_repo"].items():
        L.append(f"| {repo} | " + " | ".join(f"{r['false_fail_rate']} / {r['catch_rate']}" for r in rows) + " |")
    L += ["", "(cells are false-fail / catch)", "", "## Experiment three: coverage (flags the code declares vs the docs)", "",
          "| Threshold | Documented flags recognised | Undocumented flags flagged | n documented | n undocumented |", "|---|---|---|---|---|"]
    for row in rep["coverage"]:
        L.append(f"| {row['threshold']} | {row['documented_recognised']} | {row['undocumented_flagged']} | {row['n_documented']} | {row['n_undocumented']} |")
    s = rep["spend"]
    L += ["", "## Spend and latency", "",
          f"{s['calls']} calls ({s['errors']} non-200), {s['input_tokens']:,} input tokens, ${s['usd']} at ${PRICE_PER_M}/M. "
          f"Server ms median {s['server_ms_median']}, p99 {s['server_ms_p99']}; wall ms median {s['wall_ms_median']}, p99 {s['wall_ms_p99']}.", ""]
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
