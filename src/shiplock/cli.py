"""Command-line entry point: ``shiplock check`` and ``shiplock prompt``.

Exit codes are contractual: 0 clean, 1 a check found a problem, 2 a config or
usage error. Findings print to stdout (the answer to what was asked); notices
and the summary print to stderr so stdout stays clean for a pipe. ``--json``
swaps the human rendering for one JSON object on stdout.

A repo without a ``shiplock.toml`` still gets a useful run: the default checks
sweep whichever recognized docs exist, so ``shiplock check path/to/repo`` works
with no setup. Argparse's raw error strings never reach the user; they're
translated to sentences, with a fuzzy suggestion when one is close enough.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from importlib.resources import files
from pathlib import Path

from shiplock import __version__
from shiplock._checks import run_checks
from shiplock._config import CONFIG_FILENAME, ConfigError, default_config, load_config
from shiplock._report import Report

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

_DESCRIPTION = "Deterministic docs-vs-code release checks."
_COMMANDS = ("check", "prompt")

_RED = "\033[31m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


class _Parser(argparse.ArgumentParser):
    """Argparse with its raw error strings translated to sentences.

    No usage dump, no ``error:`` prefix — one plain sentence to stderr and
    exit 2, with a fuzzy suggestion when a mistyped command is close enough
    to a known one.
    """

    def error(self, message: str) -> "typing.NoReturn":  # type: ignore[name-defined]  # noqa: F821
        print(file=sys.stderr)
        print(f"shiplock: {_humanize(message)}", file=sys.stderr)
        print(file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _humanize(message: str) -> str:
    """Turn an argparse error string into a sentence a person would write."""
    choice = re.search(r"invalid choice: '([^']+)'", message)
    if choice:
        word = choice.group(1)
        near = difflib.get_close_matches(word, _COMMANDS, n=1, cutoff=0.6)
        hint = f" Perhaps you meant 'shiplock {near[0]}'?" if near else ""
        return (
            f"'{word}' isn't a shiplock command; the commands are "
            f"{' and '.join(_COMMANDS)}.{hint}"
        )
    unknown = re.search(r"unrecognized arguments: (.+)", message)
    if unknown:
        return (
            f"unknown option {unknown.group(1).strip()}. Run 'shiplock --help' "
            f"for the available options."
        )
    return f"{message}. Run 'shiplock --help' for the available options."


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns the process exit code."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        _print_welcome()
        return EXIT_OK

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        return _cmd_check(Path(args.path), as_json=args.json)
    return _cmd_prompt()


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="shiplock",
        description=_DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version", action="version", version=f"shiplock {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    check = sub.add_parser(
        "check",
        help="run the deterministic checks over a repo",
        allow_abbrev=False,
    )
    check.add_argument(
        "path",
        nargs="?",
        default=".",
        help="repo to check (default: the current directory)",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="print the report as one JSON object on stdout",
    )

    sub.add_parser(
        "prompt",
        help="print the semantic audit prompt for a fresh agent",
        allow_abbrev=False,
    )
    return parser


def _cmd_check(root: Path, as_json: bool = False) -> int:
    if not root.is_dir():
        print(
            f"shiplock: '{root}' isn't a directory it can check. Point it at a "
            f"repo root, or run it from inside one.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    defaulted = not (root / CONFIG_FILENAME).is_file()
    try:
        config = default_config(root) if defaulted else load_config(root)
    except ConfigError as exc:
        print(f"shiplock: {exc}", file=sys.stderr)
        return EXIT_USAGE

    report = run_checks(config)
    if as_json:
        print(json.dumps(_to_json(report)))
    else:
        if defaulted:
            print(
                f"shiplock: no {CONFIG_FILENAME} here, so this is the default "
                f"run: the docs shiplock recognized, swept for problems. A "
                f"{CONFIG_FILENAME} unlocks the remaining checks — "
                f"https://github.com/shehuphd/shiplock/blob/main/USAGE.md",
                file=sys.stderr,
            )
        _render(report)
    return EXIT_FINDINGS if not report.ok else EXIT_OK


def _to_json(report: Report) -> dict:
    """The report's canonical machine shape."""
    return {
        "ok": report.ok,
        "findings": [
            {"check": f.check, "message": f.message, "path": f.path, "line": f.line}
            for f in report.findings
        ],
        "notices": [{"check": n.check, "message": n.message} for n in report.notices],
    }


def _paint(text: str, code: str, stream) -> str:
    """Wrap ``text`` in a color code when the stream is a terminal.

    Color marks a fixed category (a finding, a clean run), never decoration,
    and is applied after any layout math so escape codes can't skew widths.
    """
    if os.environ.get("NO_COLOR") is not None or not stream.isatty():
        return text
    return f"{code}{text}{_RESET}"


def _render(report: Report) -> None:
    """Findings to stdout; notices and the summary to stderr."""
    for finding in report.findings:
        loc = finding.location()
        prefix = f"{finding.check}  {loc}".rstrip()
        print(f"{_paint(prefix, _RED, sys.stdout)}\n    {finding.message}")

    for notice in report.notices:
        print(f"shiplock: {notice.check} skipped — {notice.message}", file=sys.stderr)

    n = len(report.findings)
    if report.ok:
        print(_paint("shiplock: clean", _GREEN, sys.stderr), file=sys.stderr)
    else:
        word = "finding" if n == 1 else "findings"
        print(_paint(f"shiplock: {n} {word}", _RED, sys.stderr), file=sys.stderr)


def _cmd_prompt() -> int:
    # Anchor on the shiplock package itself, not the prompts subdirectory, which
    # has no __init__ and would otherwise rely on namespace-package resolution.
    text = files("shiplock").joinpath("prompts", "audit.md").read_text(encoding="utf-8")
    # The prompt is content; print it verbatim without a trailing reformat.
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    return EXIT_OK


def _print_welcome() -> None:
    """Greet a bare invocation: a bare command is the command succeeding."""
    print(f"shiplock {__version__} — {_DESCRIPTION}")
    print()
    print("Try:")
    print("  shiplock check path/to/repo   check any repo, no setup needed")
    print("  shiplock check                check the current directory")
    print("  shiplock prompt               print the semantic audit prompt")
    print()
    print("Docs: https://github.com/shehuphd/shiplock")


if __name__ == "__main__":
    raise SystemExit(main())
