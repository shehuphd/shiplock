"""Introspect a repo's package in a subprocess, bound to the checked root.

``version`` and ``coverage`` need facts about the code under the root being
checked: its ``__version__``, a module's ``__all__``, an enum's members, a
callable's parameter names. Importing the package in-process would read whatever
copy is on ``sys.path`` — which may be a stale install elsewhere, not the source
at root — and would be unsafe to force-reimport while shiplock itself runs inside
a caller's pytest.

So this runs a fresh subprocess that prepends the root's source directories to
``sys.path``, imports the target there, and confirms the resolved module lives
under root before reading anything. If it resolved to a copy outside root, the
query comes back as ``not_under_root`` and the calling check emits a notice
instead of comparing the wrong code.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


class IntrospectError(Exception):
    """The introspection subprocess couldn't run or returned unparseable output."""


# Runs in a clean subprocess. Reads one JSON request on stdin and writes one
# JSON object of results to stdout. It imports only the target package, never
# shiplock, so there's no interference with the tool's own import state.
_RUNNER = r'''
import sys, json, os, importlib, inspect, enum

request = json.load(sys.stdin)
root = os.path.realpath(request["root"])
for entry in request["src_candidates"]:
    sys.path.insert(0, entry)


def under_root(module):
    location = getattr(module, "__file__", None)
    if location is None:
        paths = list(getattr(module, "__path__", []) or [])
        location = paths[0] if paths else None
    if not location:
        return False
    return os.path.realpath(location).startswith(root + os.sep)


results = {}
for query in request["queries"]:
    qid = query["id"]
    op = query["op"]
    try:
        module_name = query.get("module") or query["target"].split(":", 1)[0]
        module = importlib.import_module(module_name)
        top = importlib.import_module(module_name.split(".")[0])
        if not under_root(top):
            results[qid] = {"status": "not_under_root"}
            continue
        if op == "version":
            results[qid] = {"status": "ok", "version": getattr(module, "__version__", None)}
        elif op == "exports":
            names = getattr(module, "__all__", None)
            if names is None:
                results[qid] = {"status": "no_all"}
            else:
                results[qid] = {"status": "ok", "members": list(names)}
        else:
            obj = module
            for part in filter(None, query["target"].split(":", 1)[1].split(".")):
                obj = getattr(obj, part)
            if op == "enum":
                if not isinstance(obj, enum.EnumMeta):
                    results[qid] = {"status": "not_enum"}
                else:
                    results[qid] = {"status": "ok", "members": [m.name for m in obj]}
            elif op == "params":
                if not callable(obj):
                    results[qid] = {"status": "not_callable"}
                else:
                    skip = {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
                    members = [
                        p.name
                        for p in inspect.signature(obj).parameters.values()
                        if p.name not in ("self", "cls") and p.kind not in skip
                    ]
                    results[qid] = {"status": "ok", "members": members}
            else:
                results[qid] = {"status": "unknown_op"}
    except Exception as exc:
        results[qid] = {"status": "error", "error": type(exc).__name__ + ": " + str(exc)}

json.dump(results, sys.stdout)
'''


def introspect(root: Path, queries: list[dict]) -> dict[str, dict]:
    """Run the queries against the package source under ``root``.

    Each query is ``{"id": str, "op": "version"|"exports"|"enum"|"params",
    "target": "module:Attr.path"}`` (``op="version"`` may pass ``module``
    instead of ``target``). Returns a mapping of query id to a result dict
    carrying a ``status`` and, when ``ok``, the requested data. Raises
    ``IntrospectError`` only when the subprocess itself can't run.
    """
    request = {
        "root": str(root),
        "src_candidates": [str(root / "src"), str(root)],
        "queries": queries,
    }
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER],
        input=json.dumps(request),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "no output"
        raise IntrospectError(f"introspection subprocess failed: {detail}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise IntrospectError(f"introspection returned malformed output: {exc}") from exc
