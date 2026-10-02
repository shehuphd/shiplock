"""``shiplock init``: set a repo up in one command, without prompts.

Writes a starter ``shiplock.toml`` from the docs it detects, a repo rules
file, the ``.gitignore`` line for it, and two git hooks (``pre-commit`` over
the staged diff, ``pre-push`` over the pushed range). It never overwrites a
file that exists: an existing hook gets the one line to add printed instead.
Each hook runs shiplock through the interpreter ``init`` ran under, falls
back to PATH, and blocks the commit or push with a clear message when neither
resolves, since a hook that passed on its own would remove the protection the
user believes is in place.
"""

from __future__ import annotations

import fnmatch
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from shiplock._config import CONFIG_FILENAME, _DEFAULT_DOC_NAMES
from shiplock._rules import HEADER
from shiplock._scan import DEFAULT_RULES_FILE, _git

_ZERO_SHA = "0" * 40

_HOOK_PREAMBLE = """#!/bin/sh
# Installed by `shiplock init`. {purpose}
# Bypass once with --no-verify. Reinstall with `pip install shiplock`, then
# `shiplock init`, if the interpreter below has moved.
SHIPLOCK_PY={interpreter}
run_shiplock() {{
  if [ -x "$SHIPLOCK_PY" ]; then
    "$SHIPLOCK_PY" -m shiplock "$@"
  elif command -v shiplock >/dev/null 2>&1; then
    shiplock "$@"
  else
    echo "shiplock: not found at $SHIPLOCK_PY or on PATH, so this hook can't run. Reinstall shiplock (pip install shiplock) and run 'shiplock init' again, or bypass once with --no-verify." >&2
    return 1
  fi
}}
"""

_PRE_COMMIT_BODY = """run_shiplock scan --staged
"""

_PRE_PUSH_BODY = """status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "{zero}" ] && continue
  if [ "$remote_sha" = "{zero}" ]; then
    range="$local_sha --not --remotes"
  else
    range="$remote_sha..$local_sha"
  fi
  run_shiplock scan --range "$range" || status=1
done
exit $status
"""

HOOK_LINES = {
    "pre-commit": "shiplock scan --staged",
    "pre-push": "shiplock scan --range \"$remote_sha..$local_sha\" (per pushed ref; see USAGE)",
}


@dataclass
class InitResult:
    """What ``init`` did, one line per step, plus what it left for the user."""

    lines: list[str] = field(default_factory=list)
    hooks_to_add: dict[str, str] = field(default_factory=dict)


def run_init(root: Path, hooks: bool = True, interpreter: str | None = None) -> InitResult:
    result = InitResult()
    root = root.resolve()
    result.lines.append(_write_config(root))
    result.lines.append(_write_rules_file(root))
    result.lines.append(_gitignore_line(root))
    if hooks:
        hooks_dir = _hooks_dir(root)
        if hooks_dir is None:
            result.lines.append("hooks: not a git repo, so none installed")
        elif isinstance(hooks_dir, str):
            result.lines.append(f"hooks: {hooks_dir}")
            for name in ("pre-commit", "pre-push"):
                result.hooks_to_add[name] = HOOK_LINES[name]
        else:
            py = interpreter or sys.executable
            for name, body in (("pre-commit", _PRE_COMMIT_BODY), ("pre-push", _PRE_PUSH_BODY)):
                line, pending = _install_hook(hooks_dir, name, body, py)
                result.lines.append(line)
                if pending:
                    result.hooks_to_add[name] = pending
    return result


def _write_config(root: Path) -> str:
    path = root / CONFIG_FILENAME
    if path.is_file():
        return f"{CONFIG_FILENAME}: exists, left as is"
    present = [name for name in _DEFAULT_DOC_NAMES if (root / name).is_file()]
    lines = [
        "# Shiplock config: docs-vs-code release checks, deterministic and",
        "# agent-audited (semantic and ablation prompts included).",
        "# Install: pip install shiplock. Docs: https://github.com/shehuphd/shiplock",
        "",
        "[docs]",
        "# The docs shiplock treats as public. Name the files you ship.",
        "public = [" + ", ".join(f'"{n}"' for n in present) + "]",
    ]
    if "CHANGELOG.md" in present:
        lines.append('changelog = "CHANGELOG.md"')
    if "README.md" in present:
        lines.append('readme = "README.md"')
    lines += [
        "",
        "# [style]",
        "# banned = [\"leverage\", \"synergy\"]   # words to keep out of the docs",
        "",
        "# [scan]",
        "# secrets = false                     # opt in to generic secret patterns",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    found = ", ".join(present) if present else "no recognised docs"
    return f"{CONFIG_FILENAME}: written ({found})"


def _write_rules_file(root: Path) -> str:
    path = root / DEFAULT_RULES_FILE
    if path.is_file():
        return f"{DEFAULT_RULES_FILE}: exists, left as is"
    # Only the writer's own header, so ``shiplock rules`` can rewrite it later.
    path.write_text(HEADER, encoding="utf-8")
    return f"{DEFAULT_RULES_FILE}: written (empty)"


def _gitignore_line(root: Path) -> str:
    path = root / ".gitignore"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if fnmatch.fnmatch(DEFAULT_RULES_FILE, line.lstrip("/")):
                return f".gitignore: already covers {DEFAULT_RULES_FILE}"
        text = path.read_text(encoding="utf-8", errors="replace")
        prefix = "" if text.endswith("\n") or not text else "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{DEFAULT_RULES_FILE}\n")
        return f".gitignore: added {DEFAULT_RULES_FILE}"
    path.write_text(f"{DEFAULT_RULES_FILE}\n", encoding="utf-8")
    return f".gitignore: written with {DEFAULT_RULES_FILE}"


def _hooks_dir(root: Path) -> Path | None | str:
    """The hooks directory git uses here, or why ``init`` won't write to it.

    A ``core.hooksPath`` set in the repo's own config is the repo's choice and
    is used. One set globally or system-wide is shared by every repo on the
    machine, so writing shiplock's hooks there would change repos ``init``
    was never pointed at; that case returns a message instead of a path.
    Returns None when ``root`` isn't a git repo.
    """
    if _git(root, "rev-parse", "--git-dir").returncode != 0:
        return None
    origin = _git(root, "config", "--show-origin", "--get", "core.hooksPath")
    if origin.returncode == 0 and origin.stdout.strip():
        source, _, value = origin.stdout.strip().partition("\t")
        local = _git(root, "config", "--local", "--get", "core.hooksPath")
        if local.returncode != 0:
            return (
                f"core.hooksPath is set outside this repo ({source}), to {value}; "
                f"shared hooks aren't touched; the lines to add there are listed below."
            )
    proc = _git(root, "rev-parse", "--git-path", "hooks")
    if proc.returncode != 0:
        return None
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else root / path


def _install_hook(hooks_dir: Path, name: str, body: str, interpreter: str) -> tuple[str, str]:
    """Write one hook; returns ``(status line, line to add by hand or '')``."""
    path = hooks_dir / name
    if path.exists():
        return (
            f"{name} hook: exists, left as is; the line to add is listed below",
            HOOK_LINES[name],
        )
    purpose = {
        "pre-commit": "Scans the staged diff before each commit.",
        "pre-push": "Scans each pushed commit range, and its commit messages.",
    }[name]
    text = _HOOK_PREAMBLE.format(purpose=purpose, interpreter=_sh_quote(interpreter)) + body.format(
        zero=_ZERO_SHA
    )
    hooks_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return f"{name} hook: installed at {path}", ""


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def hook_text_for(name: str, interpreter: str | None = None) -> str:
    """The hook script ``init`` would write, for tests and for printing."""
    body = _PRE_COMMIT_BODY if name == "pre-commit" else _PRE_PUSH_BODY
    purpose = "Scans the staged diff before each commit." if name == "pre-commit" else "Scans each pushed commit range, and its commit messages."
    return _HOOK_PREAMBLE.format(purpose=purpose, interpreter=_sh_quote(interpreter or sys.executable)) + body.format(zero=_ZERO_SHA)

