"""What the judge asks about: claims from the docs, and coverage items from the code.

Both are extracted deterministically. A claim is a prose sentence in a
public doc that names something the symbol index resolves; it carries its
anchors and the code around each anchor's declaration, prose included. A
coverage item is one piece of code surface, a CLI flag, a command, or an
environment variable, that the docs ought to describe. Nothing here calls a
model; ``_judge`` does that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from shiplock._config import Config
from shiplock._symbols import Index

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'(])")
_TOKEN = re.compile(r"`([^`\n]{1,80})`")
_EVIDENCE_CAP = 1500 * 4  # characters, about 1,500 tokens


@dataclass(frozen=True)
class Claim:
    """One doc sentence with resolving anchors and the code behind them."""

    doc: str
    line: int
    text: str
    anchors: tuple[str, ...]
    evidence: str


@dataclass(frozen=True)
class CoverageItem:
    """One piece of code surface the docs ought to describe."""

    kind: str  # flag | command | env var
    name: str
    path: str
    line: int


@dataclass
class DocSurface:
    """Everything extracted from the docs and the code for one judge run."""

    claims: list[Claim] = field(default_factory=list)
    unanchored: dict[str, int] = field(default_factory=dict)
    coverage: list[CoverageItem] = field(default_factory=list)
    doc_text: dict[str, str] = field(default_factory=dict)


def sentences(text: str) -> list[tuple[int, str]]:
    """(line, sentence) for prose outside fenced code, tables, and headings."""
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


def extract(config: Config, index: Index, docs: list[str]) -> DocSurface:
    """Claims from ``docs`` and coverage items from ``index``."""
    surface = DocSurface()
    for doc in docs:
        try:
            text = (config.root / doc).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        surface.doc_text[doc] = text
        unanchored = 0
        for line, sent in sentences(text):
            tokens = _TOKEN.findall(sent)
            if not tokens:
                continue
            anchors: list[str] = []
            parts: list[str] = []
            seen: set[tuple[str, int]] = set()
            for tok in tokens:
                locs = index.lookup(tok)
                if not locs:
                    continue
                anchors.append(tok.strip())
                for loc in locs[:2]:
                    key = (loc.path, loc.line)
                    if key in seen:
                        continue
                    seen.add(key)
                    parts.append(index.excerpt(loc))
            if not anchors:
                unanchored += 1
                continue
            evidence = "\n\n".join(parts)
            if len(evidence) > _EVIDENCE_CAP:
                evidence = evidence[:_EVIDENCE_CAP] + "\n# [cut]"
            surface.claims.append(Claim(doc, line, sent, tuple(anchors), evidence))
        surface.unanchored[doc] = unanchored

    undocumented = set(config.judge.undocumented) if config.judge else set()
    for flag, entries in sorted(index.flags.items()):
        shipped = [e for e in entries if _shipped(e.path)]
        if flag in undocumented or not shipped:
            continue
        surface.coverage.append(CoverageItem("flag", flag, shipped[0].path, shipped[0].line))
    for cmd in sorted(index.commands):
        locs = [l for l in index.symbols.get(cmd, []) if l.kind == "command" and _shipped(l.path)]
        if cmd in undocumented or not locs:
            continue
        surface.coverage.append(CoverageItem("command", cmd, locs[0].path, locs[0].line))
    for env in sorted(index.env_vars):
        locs = [l for l in index.symbols.get(env, []) if l.kind == "env" and _shipped(l.path)]
        if env in undocumented or env in _FOREIGN_ENV or not locs:
            continue
        surface.coverage.append(CoverageItem("env var", env, locs[0].path, locs[0].line))
    return surface


_UNSHIPPED_PREFIXES = ("tests/", "test/", "scripts/", "examples/", "eval/", "bench/", "docs/")


def _shipped(path: str) -> bool:
    """Whether a path is part of what the repo ships, not its tests or tooling.

    Coverage asks whether the docs describe the product's surface; a test
    fixture's parser or an eval script's flags aren't that surface.
    """
    return not path.startswith(_UNSHIPPED_PREFIXES)


_FOREIGN_ENV = {"PATH", "HOME", "APPDATA", "XDG_CONFIG_HOME", "NO_COLOR", "TERM", "CI", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY",
                "GITHUB_SHA", "GITHUB_REF", "GITHUB_TOKEN", "GH_TOKEN", "RUNNER_TEMP", "GITHUB_ENV", "GITHUB_REPOSITORY"}
