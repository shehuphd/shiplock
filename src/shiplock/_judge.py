"""The judge: typed questions to a judgment provider, and what comes back.

A judgment provider answers atomic questions about a state with calibrated
probabilities; it doesn't generate text. The registry holds one adapter per
provider (``typesafe`` today), so the layer stays provider-agnostic and a
judgment task never routes to a chat provider. The adapter is one HTTP POST
over ``urllib``; the runtime stays standard-library only.

Two question sets run. Coverage asks, per piece of code surface, whether the
docs describe it; an item no doc describes is a warning with its code
location. Claims ask, per anchored doc sentence, whether the code behind its
anchors supports it; those answers never gate and are written to the JSON
result as leads for the agent audit (``shiplock prompt --judgments``). A
third set, run on request, holds an agent audit's own output against the
repo: the verdict line, the paths it cites, and whether its verdict follows
from its findings.

Every call records its spend: input tokens, wall and server milliseconds,
the request id, and the resolved model, which is compared to the pinned one.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from shiplock._claims import DocSurface, extract
from shiplock._config import Config, JudgeConfig
from shiplock._report import Finding, Notice
from shiplock._scan import git_tracked_files
from shiplock._symbols import build_index

STATE_BUDGET_CHARS = 28_000 * 4
COVERAGE_WARN = 0.7  # P(described) below this is a warning
LEAD_FLOOR = 0.3     # P(contradicted) at or above this makes a lead


class JudgeError(Exception):
    """The judge couldn't run: no key, a dead key, an unknown provider."""


@dataclass
class CallRecord:
    purpose: str
    status: int
    model: str | None
    input_tokens: int
    wall_ms: int
    server_ms: int | None
    request_id: str | None
    error: str | None = None


@dataclass
class Answer:
    """One question's answer, normalised across providers."""

    probability: float               # a Noul's yes probability, or a Choice pick's probability
    choice: str | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None


class Adapter:
    """What a provider adapter offers the judge."""

    name = ""

    def judge(self, state: object, questions: dict[str, dict], model: str) -> tuple[dict[str, Answer], CallRecord]:
        raise NotImplementedError


class TypeSafeAdapter(Adapter):
    """TypeSafe's System One endpoint over ``urllib``.

    Retries 429 and 5xx with backoff, honouring ``Retry-After``. A 403 is
    not retried: on this API it has been a deterministic block on the
    request's content, so the caller reports the chunk and moves on. No
    temperature, seed, or max-output-tokens parameter exists for this API,
    so none is sent.
    """

    name = "typesafe"
    endpoint = "https://api.typesafe.ai/v1/systemone"

    def __init__(self, key: str, opener: Callable | None = None, retries: int = 3) -> None:
        self.key = key
        self.opener = opener or urllib.request.urlopen
        self.retries = retries

    def judge(self, state, questions, model):
        body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
        attempt = 0
        while True:
            req = urllib.request.Request(
                self.endpoint, data=body, method="POST",
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            )
            start = time.perf_counter()
            try:
                with self.opener(req, timeout=120) as resp:
                    status = resp.status
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    payload = json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                status = exc.code
                headers = {k.lower(): v for k, v in exc.headers.items()}
                try:
                    payload = json.loads(exc.read().decode() or "{}")
                except ValueError:
                    payload = {}
                if status in (429, 500, 502, 503, 504, 529) and attempt < self.retries:
                    attempt += 1
                    time.sleep(float(headers.get("retry-after") or 0) or 1.5 * attempt)
                    continue
            wall = round((time.perf_counter() - start) * 1000)
            record = CallRecord(
                purpose="", status=status, model=payload.get("model"),
                input_tokens=int((payload.get("usage") or {}).get("input_tokens") or 0),
                wall_ms=wall,
                server_ms=int(headers["x-envoy-upstream-service-time"]) if headers.get("x-envoy-upstream-service-time") else None,
                request_id=headers.get("x-typesafe-request-id"),
                error=None if status == 200 else _error_text(status, payload),
            )
            answers: dict[str, Answer] = {}
            for qid, a in (payload.get("answers") or {}).items():
                if a.get("type") == "noul":
                    answers[qid] = Answer(float(a.get("noul", 0.0)))
                else:
                    probs = {k: float(v) for k, v in (a.get("probabilities") or {}).items()}
                    pick = a.get("choice")
                    answers[qid] = Answer(probs.get(pick, 0.0), pick, probs, a.get("confidence"))
            return answers, record


def _error_text(status: int, payload: dict) -> str:
    detail = payload.get("detail", payload)
    if isinstance(detail, dict):
        return f"{status} {detail.get('error_type') or ''} {detail.get('message') or ''}".strip()
    if isinstance(detail, list):
        return f"{status} validation error"
    return f"{status} {str(detail)[:120]}".strip()


ADAPTERS: dict[str, Callable[[str], Adapter]] = {"typesafe": TypeSafeAdapter}


def adapter_for(provider: str, key: str) -> Adapter:
    """The adapter for a provider name; a key of the form ``provider/key`` sets both."""
    if "/" in key and not key.startswith("http"):
        prefix, _, rest = key.partition("/")
        if prefix.lower() in ADAPTERS:
            provider, key = prefix.lower(), rest
    factory = ADAPTERS.get(provider)
    if factory is None:
        raise JudgeError(f"'{provider}' isn't a judgment provider; the providers are {', '.join(sorted(ADAPTERS))}.")
    if not key:
        raise JudgeError(f"no key for the {provider} judge; set TYPESAFE_API_KEY, or JUDGE_API_KEY as provider/key.")
    return factory(key)


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


@dataclass
class JudgeResult:
    """Everything one ``shiplock judge`` run produced."""

    model: str
    findings: list[Finding] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    leads: list[dict] = field(default_factory=list)
    unanchored: dict[str, int] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    audit: dict | None = None

    def spend(self) -> dict:
        ok = [c for c in self.calls if c.status == 200]
        server = sorted(c.server_ms for c in ok if c.server_ms is not None)
        return {
            "calls": len(self.calls),
            "failed": len(self.calls) - len(ok),
            "input_tokens": sum(c.input_tokens for c in self.calls),
            "wall_ms": sum(c.wall_ms for c in self.calls),
            "server_ms_median": server[len(server) // 2] if server else None,
            "models": sorted({c.model for c in ok if c.model}),
        }

    def to_json(self) -> dict:
        return {
            "model": self.model,
            "coverage": self.coverage,
            "leads": self.leads,
            "unanchored": self.unanchored,
            "refused": self.refused,
            "audit": self.audit,
            "spend": self.spend(),
            "calls": [asdict(c) for c in self.calls],
        }


def run_judge(config: Config, adapter: Adapter, judge: JudgeConfig | None = None,
              audit_output: str | None = None, leads: bool = True) -> JudgeResult:
    """Run coverage, the claim leads, and (optionally) the audit-output check."""
    judge = judge or config.judge or JudgeConfig()
    result = JudgeResult(model=judge.model)
    tracked = git_tracked_files(config.root)
    if tracked is None:
        result.notices.append(Notice("judge", "not a git repo, or git unavailable; skipped"))
        return result
    docs = judge.docs or (config.docs.public if config.docs else [])
    docs = [d for d in docs if (config.root / d).is_file()]
    if not docs:
        result.notices.append(Notice("judge", "no docs to judge ([judge].docs or [docs].public); skipped"))
        return result

    _preflight(adapter, judge.model, result)
    index = build_index(config.root, tracked, set(docs))
    surface = extract(config, index, docs)
    result.unanchored = surface.unanchored

    _coverage(adapter, judge, surface, result)
    if leads:
        _leads(adapter, judge, surface, result)
    if audit_output is not None:
        _audit_output(adapter, judge, config, docs, tracked, audit_output, result)

    spend = result.spend()
    refused = f", {len(result.refused)} call(s) refused by the provider" if result.refused else ""
    result.notices.append(
        Notice(
            "judge",
            f"{spend['calls']} call(s), {spend['input_tokens']} input tokens, "
            f"{len(surface.claims)} claim(s) judged as leads, "
            f"{sum(surface.unanchored.values())} sentence(s) unanchored{refused}",
            kind="info",
        )
    )
    drift = [m for m in spend["models"] if m != judge.model]
    if drift:
        result.findings.append(Finding("judge", f"the provider resolved model {', '.join(drift)}, not the pinned {judge.model}"))
    return result


def _call(adapter: Adapter, model: str, purpose: str, state, questions, result: JudgeResult) -> dict[str, Answer] | None:
    answers, record = adapter.judge(state, questions, model)
    record.purpose = purpose
    result.calls.append(record)
    if record.status != 200:
        result.refused.append(f"{purpose}: {record.error}")
        return None
    return answers


def _preflight(adapter: Adapter, model: str, result: JudgeResult) -> None:
    answers = _call(adapter, model, "pre-flight", "ok", {"q": {"type": "noul", "instructions": "Is the state the word ok?"}}, result)
    if answers is None:
        raise JudgeError(f"the judge's key failed its pre-flight: {result.calls[-1].error}")


def _coverage(adapter: Adapter, judge: JudgeConfig, surface: DocSurface, result: JudgeResult) -> None:
    items = surface.coverage
    if not items:
        result.notices.append(Notice("judge", "no code surface to check coverage for (no flags, commands, or env vars found)"))
        return
    best: dict[int, tuple[float, str]] = {}
    questions = {
        f"i{n}": {"type": "noul", "instructions": f"Does `doc` describe the {item.kind} `{item.name}`: what it does or when to use it, not merely a string that happens to contain it?"}
        for n, item in enumerate(items)
    }
    for doc, text in surface.doc_text.items():
        for part, chunk in _doc_chunks(adapter, judge.model, doc, text[:STATE_BUDGET_CHARS], questions, result):
            answers = chunk
            for n, item in enumerate(items):
                a = answers.get(f"i{n}")
                if a is None:
                    continue
                if n not in best or a.probability > best[n][0]:
                    best[n] = (a.probability, f"{doc}{part}")
    for n, item in enumerate(items):
        p, doc = best.get(n, (0.0, ""))
        row = {"kind": item.kind, "name": item.name, "path": item.path, "line": item.line, "p_described": round(p, 3), "best_doc": doc}
        result.coverage.append(row)
        if p < COVERAGE_WARN:
            result.notices.append(
                Notice("judge", f"coverage: {item.kind} `{item.name}` ({item.path}:{item.line}) isn't described in any judged doc (p={p:.2f})", kind="warning")
            )


def _doc_chunks(adapter: Adapter, model: str, doc: str, text: str, questions: dict, result: JudgeResult, depth: int = 0):
    """Ask over a doc; on a 403 (a content block) split it in two and ask each half.

    Yields ``(part label, answers)`` for each chunk that answered. Two levels
    of splitting bound the retries at three extra calls per doc.
    """
    purpose = f"coverage:{doc}" if depth == 0 else f"coverage:{doc} (part)"
    answers = _call(adapter, model, purpose, {"doc": text}, questions, result)
    if answers is not None:
        yield "", answers
        return
    if result.calls[-1].status != 403 or depth >= 2 or len(text) < 2000:
        return
    half = len(text) // 2
    cut = text.rfind("\n", 0, half)
    cut = cut if cut > 0 else half
    for label, piece in ((" (first half)", text[:cut]), (" (second half)", text[cut:])):
        for _, chunk in _doc_chunks(adapter, model, doc, piece, questions, result, depth + 1):
            yield label, chunk


def _leads(adapter: Adapter, judge: JudgeConfig, surface: DocSurface, result: JudgeResult) -> None:
    criteria = {
        "supported": {"what": "the evidence shows the claim holds as written"},
        "contradicted": {"what": "the evidence shows the claim is false as written: a different value, name, flag, default, or behaviour", "not_for": "evidence that simply doesn't mention what the claim says"},
        "insufficient": {"what": "the evidence doesn't settle the claim either way"},
    }
    batch: list = []
    size = 0
    batches: list[list] = []
    for c in surface.claims:
        n = len(c.text) + len(c.evidence) + 400
        if batch and size + n > STATE_BUDGET_CHARS:
            batches.append(batch)
            batch, size = [], 0
        batch.append(c)
        size += n
    if batch:
        batches.append(batch)
    for k, claims in enumerate(batches):
        state = {"claims": [{"text": c.text, "anchors": list(c.anchors), "evidence": c.evidence} for c in claims]}
        questions = {
            f"c{n}": {
                "type": "choice",
                "instructions": f"`claims[{n}]`.text is a sentence from the documentation; it refers to the code named in `claims[{n}]`.anchors. `claims[{n}]`.evidence is that code. Does the evidence support the sentence as written?",
                "criteria": criteria,
            }
            for n in range(len(claims))
        }
        answers = _call(adapter, judge.model, f"leads:{k + 1}/{len(batches)}", state, questions, result)
        if answers is None:
            continue
        for n, c in enumerate(claims):
            a = answers.get(f"c{n}")
            if a is None:
                continue
            p_con = a.probabilities.get("contradicted", 0.0)
            if a.choice == "contradicted" or (a.choice == "insufficient" and p_con >= LEAD_FLOOR):
                result.leads.append({
                    "doc": c.doc, "line": c.line, "text": c.text, "anchors": list(c.anchors),
                    "pick": a.choice, "p_contradicted": round(p_con, 3), "p_insufficient": round(a.probabilities.get("insufficient", 0.0), 3),
                    "evidence_head": c.evidence.split("\n", 1)[0],
                })
    result.leads.sort(key=lambda r: -r["p_contradicted"])


_VERDICT = re.compile(r"^AUDIT:\s*(PASS|FAIL)\s*$", re.M)
_PATH_RE = re.compile(r"`([\w./-]+\.(?:py|md|toml|yml|yaml|rs|ts|js|json|txt))(?::(\d+))?`")


def _audit_output(adapter: Adapter, judge: JudgeConfig, config: Config, docs: list[str], tracked: list[str],
                  audit_output: str, result: JudgeResult) -> None:
    """Hold an agent audit's output against the repo: verdict, paths, consistency."""
    try:
        text = Path(audit_output).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.notices.append(Notice("judge", f"audit output {audit_output} unreadable: {exc}"))
        return
    checks: dict = {"verdict_line": None, "missing_paths": [], "consistent": None, "findings_concrete": None}
    verdicts = _VERDICT.findall(text)
    checks["verdict_line"] = verdicts[-1] if verdicts else None
    if not verdicts:
        result.findings.append(Finding("judge", f"the audit output {audit_output} has no AUDIT: PASS or AUDIT: FAIL verdict line"))
    tracked_set = set(tracked)
    for m in _PATH_RE.finditer(text):
        path = m.group(1)
        if path not in tracked_set:
            checks["missing_paths"].append(path)
            result.findings.append(Finding("judge", f"the audit cites `{path}`, which isn't a tracked file", path=audit_output))
    state = {"audit": text[:STATE_BUDGET_CHARS]}
    questions = {
        "consistent": {"type": "noul", "instructions": "Does the verdict line at the end of `audit` follow from the findings it lists: FAIL when it reports at least one docs-vs-code disagreement, PASS when it reports none?"},
        "concrete": {"type": "noul", "instructions": "Does every finding in `audit` name a specific documentation claim and a specific place in the code, rather than a general impression?"},
    }
    answers = _call(adapter, judge.model, "audit-output", state, questions, result)
    if answers:
        checks["consistent"] = round(answers["consistent"].probability, 3)
        checks["findings_concrete"] = round(answers["concrete"].probability, 3)
        if answers["consistent"].probability < 0.5:
            result.findings.append(Finding("judge", f"the audit's verdict doesn't follow from its findings (p={answers['consistent'].probability:.2f})", path=audit_output))
        if answers["concrete"].probability < 0.5:
            result.notices.append(Notice("judge", f"the audit's findings read as general rather than located (p={answers['concrete'].probability:.2f})", kind="warning"))
    result.audit = checks


# --------------------------------------------------------------------------
# Feeding the prompts
# --------------------------------------------------------------------------


def judgments_section(path: Path) -> str:
    """A prompt section from a ``shiplock judge --out`` file: leads to verify first."""
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [
        "",
        "## Judge results to verify first",
        "",
        "A typed-judgment pass ran before you. Its output below is a set of leads,",
        "not conclusions: a claim marked contradicted at 0.7 is a place to look,",
        "not a verdict. For each item, say whether you confirmed or overturned it,",
        "and cite the code either way. Then continue with the audit as usual.",
        "",
    ]
    leads = data.get("leads") or []
    if leads:
        lines.append("Claims the judge doubted (doc:line, pick, P(contradicted)):")
        for r in leads[:40]:
            lines.append(f"- {r['doc']}:{r['line']} [{r['pick']}, {r['p_contradicted']}] {r['text']}")
        lines.append("")
    uncovered = [c for c in (data.get("coverage") or []) if c.get("p_described", 1.0) < COVERAGE_WARN]
    if uncovered:
        lines.append("Code surface no judged doc describes:")
        for c in uncovered[:40]:
            lines.append(f"- {c['kind']} `{c['name']}` ({c['path']}:{c['line']}, p={c['p_described']})")
        lines.append("")
    un = data.get("unanchored") or {}
    if un:
        lines.append("Sentences the judge couldn't anchor to code, per doc: " + ", ".join(f"{d}: {n}" for d, n in un.items()) + ".")
        lines.append("")
    if data.get("refused"):
        lines.append(f"{len(data['refused'])} judge call(s) were refused by the provider; those claims weren't judged.")
        lines.append("")
    return "\n".join(lines)
