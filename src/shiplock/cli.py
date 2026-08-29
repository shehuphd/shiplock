"""Command-line entry point: ``shiplock check`` and ``shiplock prompt``.

Exit codes are contractual: 0 clean, 1 a check found a problem, 2 a config or
usage error. Findings print to stdout (the answer to what was asked); notices
and the summary print to stderr so stdout stays clean for a pipe.
"""

from __future__ import annotations

import argparse
import sys
from importlib.resources import files
from pathlib import Path

from shiplock import __version__
from shiplock._checks import run_checks
from shiplock._config import ConfigError, load_config
from shiplock._report import Report

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

_DESCRIPTION = "Deterministic docs-vs-code release checks."


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns the process exit code."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        _print_welcome()
        return EXIT_OK

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        return _cmd_check(Path(args.root))
    if args.command == "prompt":
        return _cmd_prompt()
    # argparse rejects any other command before reaching here.
    parser.error(f"unknown command: {args.command}")
    return EXIT_USAGE  # unreachable; parser.error exits.


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shiplock",
        description=_DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version", action="version", version=f"shiplock {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="run the deterministic checks over a repo",
        allow_abbrev=False,
    )
    check.add_argument(
        "--root",
        default=".",
        help="repo root holding shiplock.toml (default: current directory)",
    )

    sub.add_parser(
        "prompt",
        help="print the semantic audit prompt for a fresh agent",
        allow_abbrev=False,
    )
    return parser


def _cmd_check(root: Path) -> int:
    try:
        config = load_config(root)
    except ConfigError as exc:
        print(f"shiplock: {exc}", file=sys.stderr)
        return EXIT_USAGE

    report = run_checks(config)
    _render(report)
    return EXIT_FINDINGS if not report.ok else EXIT_OK


def _cmd_prompt() -> int:
    # Anchor on the shiplock package itself, not the prompts subdirectory, which
    # has no __init__ and would otherwise rely on namespace-package resolution.
    text = files("shiplock").joinpath("prompts", "audit.md").read_text(encoding="utf-8")
    # The prompt is content; print it verbatim without a trailing reformat.
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    return EXIT_OK


def _render(report: Report) -> None:
    """Findings to stdout; notices and the summary to stderr."""
    for finding in report.findings:
        loc = finding.location()
        prefix = f"{finding.check}  {loc}".rstrip()
        print(f"{prefix}\n    {finding.message}")

    for notice in report.notices:
        print(f"shiplock: {notice.check} skipped — {notice.message}", file=sys.stderr)

    n = len(report.findings)
    if report.ok:
        print("shiplock: clean", file=sys.stderr)
    else:
        word = "finding" if n == 1 else "findings"
        print(f"shiplock: {n} {word}", file=sys.stderr)


def _print_welcome() -> None:
    """Greet a bare invocation: a bare command is the command succeeding."""
    print(f"shiplock {__version__} — {_DESCRIPTION}")
    print()
    print("Try:")
    print("  shiplock check                    check the current directory")
    print("  shiplock check --root path/to/repo  check a repo elsewhere")
    print("  shiplock prompt                   print the semantic audit prompt")
    print()
    print("Docs: https://github.com/shehuphd/shiplock")


if __name__ == "__main__":
    raise SystemExit(main())
