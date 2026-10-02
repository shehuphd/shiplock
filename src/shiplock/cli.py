"""Command-line entry point: ``check``, ``scan``, ``init``, ``rules``, ``prompt``, and ``needs-import``.

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
import dataclasses
import difflib
import json
import os
import re
import sys
from importlib.resources import files
from pathlib import Path

from shiplock import __version__
from shiplock._checks import deprecation_notices, needs_import, run_checks
from shiplock._config import CONFIG_FILENAME, ConfigError, default_config, load_config
from shiplock._report import Report

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

_DESCRIPTION = "Docs-vs-code release checks: deterministic, plus semantic and ablation audit prompts."
_COMMANDS = ("check", "scan", "init", "rules", "prompt", "needs-import")
_PROMPT_KINDS = ("audit", "ablation")
_RULE_KINDS = ("code-name", "email", "username", "ref", "folder", "file")

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
        if "argument kind" in message:
            near = difflib.get_close_matches(word, _PROMPT_KINDS, n=1, cutoff=0.6)
            hint = f" Perhaps you meant 'shiplock prompt {near[0]}'?" if near else ""
            return (
                f"'{word}' isn't a shiplock prompt; the prompts are "
                f"{' and '.join(_PROMPT_KINDS)}.{hint}"
            )
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
        return _cmd_check(Path(args.path), as_json=args.json, rules=args.rules)
    if args.command == "scan":
        return _cmd_scan(
            Path(args.path),
            as_json=args.json,
            rules=args.rules,
            rev_range=args.range,
            staged=args.staged,
        )
    if args.command == "needs-import":
        return _cmd_needs_import(Path(args.path))
    if args.command == "init":
        return _cmd_init(Path(args.path), hooks=not args.no_hooks)
    if args.command == "rules":
        return _cmd_rules(args)
    return _cmd_prompt(args.kind)


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
    check.add_argument(
        "--rules",
        action="append",
        default=[],
        metavar="PATH",
        help="an extra rules file to read (repeatable), e.g. one a CI gate "
        "writes from a secret",
    )

    scan = sub.add_parser(
        "scan",
        help="scan the git-tracked files for internal refs and identity leaks",
        allow_abbrev=False,
    )
    scan.add_argument(
        "path",
        nargs="?",
        default=".",
        help="repo to scan (default: the current directory)",
    )
    scan.add_argument(
        "--json",
        action="store_true",
        help="print the report as one JSON object on stdout",
    )
    scan.add_argument(
        "--rules",
        action="append",
        default=[],
        metavar="PATH",
        help="an extra rules file to read (repeatable), e.g. one a CI gate "
        "writes from a secret",
    )
    mode = scan.add_mutually_exclusive_group()
    mode.add_argument(
        "--range",
        metavar="REVS",
        help="scan the added lines and commit messages of a commit range "
        "(a git rev-list expression, e.g. origin/main..HEAD) instead of the "
        "tracked files; what a pre-push hook checks",
    )
    mode.add_argument(
        "--staged",
        action="store_true",
        help="scan the added lines of the staged diff instead of the tracked "
        "files; what a pre-commit hook checks",
    )

    init = sub.add_parser(
        "init",
        help="set a repo up: starter config, rules file, .gitignore line, git hooks",
        allow_abbrev=False,
    )
    init.add_argument("path", nargs="?", default=".", help="repo to set up (default: the current directory)")
    init.add_argument("--no-hooks", action="store_true", help="skip installing the pre-commit and pre-push hooks")

    rules = sub.add_parser(
        "rules",
        help="edit the rules files: add, allow, remove, list, suggest, push-secret",
        allow_abbrev=False,
    )
    rules.add_argument("--path", default=".", metavar="REPO", help="the repo (default: the current directory)")
    rsub = rules.add_subparsers(dest="rules_command", required=True, parser_class=_Parser)

    add = rsub.add_parser("add", help="add a rule", allow_abbrev=False)
    add.add_argument("kind", choices=_RULE_KINDS, help="what the value is")
    add.add_argument("values", nargs="+", metavar="VALUE", help="one or more values")
    add.add_argument("--pattern", help="the regex, for kind 'ref' only")
    _target_flags(add)

    allow = rsub.add_parser("allow", help="allow a value this repo contains on purpose", allow_abbrev=False)
    allow.add_argument("value", help="a code-name, username, email, or pattern label")
    allow.add_argument("--in", dest="paths", action="append", default=[], metavar="PATH",
                       help="a repo-relative path or glob it's allowed in (repeatable); omit for anywhere")
    _target_flags(allow)

    remove = rsub.add_parser("remove", help="remove a rule from whichever file holds it", allow_abbrev=False)
    remove.add_argument("kind", choices=_RULE_KINDS + ("allow",), help="what the value is")
    remove.add_argument("value")
    remove.add_argument("--rewrite", action="store_true", help="rewrite a file even if it holds hand-written comments")

    listing = rsub.add_parser("list", help="show the rules in force, masked", allow_abbrev=False)
    listing.add_argument("--unmask", action="store_true", help="print values in full")

    rsub.add_parser("suggest", help="list ignored folders and files no rule covers", allow_abbrev=False)

    push = rsub.add_parser("push-secret", help="store the merged rules as the repo's SHIPLOCK_RULES secret through gh", allow_abbrev=False)
    push.add_argument("--repo-only", action="store_true", help="send only this repo's rules file, not the user file")
    push.add_argument("--rules", action="append", default=[], metavar="PATH", help="an extra rules file to include (repeatable)")

    prompt = sub.add_parser(
        "prompt",
        help="print a prompt for a fresh agent (audit or ablation)",
        allow_abbrev=False,
    )
    prompt.add_argument(
        "kind",
        nargs="?",
        default="audit",
        choices=_PROMPT_KINDS,
        help="which prompt: the gating docs-vs-code audit (default) or the "
        "advisory ablation audit",
    )

    needs = sub.add_parser(
        "needs-import",
        help="print whether this repo's config needs the package importable",
        allow_abbrev=False,
    )
    needs.add_argument(
        "path",
        nargs="?",
        default=".",
        help="repo to inspect (default: the current directory)",
    )
    return parser


def _cmd_check(root: Path, as_json: bool = False, rules: list[str] | None = None) -> int:
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

    config = dataclasses.replace(config, rules_paths=tuple(rules or ()))
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


def _cmd_scan(
    root: Path,
    as_json: bool = False,
    rules: list[str] | None = None,
    rev_range: str | None = None,
    staged: bool = False,
) -> int:
    """Run the leak & internal-reference scan alone over ``root``.

    Explicit invocation always scans, with whatever rules are declared: the
    user's rules file, the default rules file, and any ``--rules`` files even
    when the repo declares no ``[scan]``, plus ``[scan]``'s committed refs,
    rules files, and secrets switch when it does. With no rules at all it
    skips with a notice. ``rev_range`` scans a commit range and ``staged`` the
    staged diff instead of the tracked files. Exit codes match the rest of the
    CLI: 0 clean, 1 a finding, 2 a usage or config error.
    """
    from shiplock._config import ScanConfig
    from shiplock._scan import (
        default_scan_config,
        run_scan,
        run_scan_range,
        run_scan_staged,
    )

    if not root.is_dir():
        print(
            f"shiplock: '{root}' isn't a directory it can scan. Point it at a "
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

    config = dataclasses.replace(config, rules_paths=tuple(rules or ()))
    scan_cfg: ScanConfig = config.scan or default_scan_config()
    if rev_range is not None:
        findings, notices = run_scan_range(config, scan_cfg, rev_range)
    elif staged:
        findings, notices = run_scan_staged(config, scan_cfg)
    else:
        findings, notices = run_scan(config, scan_cfg)
    report = Report(findings=findings, notices=deprecation_notices(config) + notices)
    if as_json:
        print(json.dumps(_to_json(report)))
    else:
        _render(report)
    return EXIT_FINDINGS if not report.ok else EXIT_OK


def _target_flags(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--user", dest="to_user", action="store_true", default=None,
                       help="write to your user rules file")
    where.add_argument("--repo", dest="to_user", action="store_false",
                       help="write to this repo's rules file")
    parser.add_argument("--rewrite", action="store_true",
                        help="rewrite a file even if it holds hand-written comments")


def _cmd_init(root: Path, hooks: bool = True) -> int:
    """Set a repo up in one command; never overwrites, never prompts."""
    from shiplock._init import run_init
    from shiplock._suggest import render, suggest

    if not root.is_dir():
        print(f"shiplock: '{root}' isn't a directory. Point init at a repo root.", file=sys.stderr)
        return EXIT_USAGE
    result = run_init(root, hooks=hooks)
    for line in result.lines:
        print(f"shiplock init: {line}")
    config = _config_for(root)
    if config is not None:
        print()
        sys.stdout.write(render(suggest(config)))
    print()
    print("Next: add the names to keep out with 'shiplock rules add code-name NAME',")
    print("'shiplock rules add email ADDRESS', or 'shiplock rules add folder DIR/'.")
    return EXIT_OK


def _config_for(root: Path):
    root = root.resolve()
    try:
        return load_config(root) if (root / CONFIG_FILENAME).is_file() else default_config(root)
    except ConfigError as exc:
        print(f"shiplock: {exc}", file=sys.stderr)
        return None


def _cmd_rules(args: argparse.Namespace) -> int:
    """Edit the rules files without hand-written TOML."""
    from shiplock import _rules
    from shiplock._rules import RulesFileError

    root = Path(args.path).resolve()
    config = _config_for(root)
    if config is None and (root / CONFIG_FILENAME).is_file():
        return EXIT_USAGE
    try:
        if args.rules_command == "add":
            return _rules_add(args, root, config)
        if args.rules_command == "allow":
            path = _rules.target_path("allow", root, config, args.to_user)
            rules = _rules.load_rules_file(path)
            line = _rules.allow_entry(rules, args.value, args.paths)
            _rules.save(rules, rewrite=args.rewrite)
            print(f"shiplock rules: {line} ({path})")
            return EXIT_OK
        if args.rules_command == "remove":
            return _rules_remove(args, root, config)
        if args.rules_command == "list":
            return _rules_list(args, root, config)
        if args.rules_command == "suggest":
            from shiplock._suggest import render, suggest

            if config is None:
                return EXIT_USAGE
            sys.stdout.write(render(suggest(config)))
            return EXIT_OK
        return _rules_push_secret(args, root, config)
    except RulesFileError as exc:
        print(f"shiplock: {exc}", file=sys.stderr)
        return EXIT_USAGE


def _rules_add(args: argparse.Namespace, root: Path, config) -> int:
    from shiplock import _rules

    if args.kind == "ref" and len(args.values) > 1:
        print("shiplock: a ref takes one label and one --pattern; add them one at a time.", file=sys.stderr)
        return EXIT_USAGE
    path = _rules.target_path(args.kind, root, config, args.to_user)
    rules = _rules.load_rules_file(path)
    lines = [_rules.add_rule(rules, args.kind, value, args.pattern) for value in args.values]
    _rules.save(rules, rewrite=args.rewrite)
    for line in lines:
        print(f"shiplock rules: added {line} ({path})")
    return EXIT_OK


def _rules_remove(args: argparse.Namespace, root: Path, config) -> int:
    from shiplock import _rules

    removed = False
    for path in _rules_paths(root, config):
        if not path.is_file():
            continue
        rules = _rules.load_rules_file(path)
        if _rules.remove_rule(rules, args.kind, args.value):
            _rules.save(rules, rewrite=args.rewrite)
            print(f"shiplock rules: removed {args.kind} {_rules.mask(args.value)} ({path})")
            removed = True
    if not removed:
        print(f"shiplock rules: no {args.kind} {_rules.mask(args.value)} in any rules file")
    return EXIT_OK


def _rules_paths(root: Path, config) -> list[Path]:
    from shiplock import _rules
    from shiplock._scan import user_rules_path

    out: list[Path] = []
    user, _ = user_rules_path()
    if user is not None:
        out.append(user)
    out.append(_rules.repo_rules_path(root, config))
    return out


def _rules_list(args: argparse.Namespace, root: Path, config) -> int:
    from shiplock import _rules

    show = (lambda v: v) if args.unmask else _rules.mask
    for path in _rules_paths(root, config):
        print(f"{path}{'' if path.is_file() else ' (absent)'}")
        if not path.is_file():
            continue
        rules = _rules.load_rules_file(path)
        for label, _ in rules.refs:
            print(f"  ref        {show(label)}")
        for key, kind in (("code_names", "code-name"), ("home_usernames", "username"), ("emails", "email")):
            for value in getattr(rules, key):
                print(f"  {kind:<10} {show(value)}")
        for value, scope in rules.allow:
            where = f" in {', '.join(scope)}" if scope else ""
            print(f"  allow      {show(value)}{where}")
        if rules.is_empty():
            print("  (no rules)")
    return EXIT_OK


def _rules_push_secret(args: argparse.Namespace, root: Path, config) -> int:
    from shiplock import _rules

    paths = [] if args.repo_only else _rules_paths(root, config)[:-1]
    paths.append(_rules.repo_rules_path(root, config))
    paths.extend(Path(p).expanduser() for p in args.rules)
    files = [_rules.load_rules_file(p) for p in paths if p.is_file()]
    merged = _rules.merge(files)
    if merged.is_empty():
        print("shiplock: no rules to push; the rules files are empty or absent.", file=sys.stderr)
        return EXIT_USAGE
    failure = _rules.push_secret(root, _rules.render(merged))
    if failure:
        print(f"shiplock: {failure}", file=sys.stderr)
        return EXIT_USAGE
    counts = ", ".join(f"{n} {key.replace('_', ' ')}" for key, n in merged.counts().items() if n)
    print(f"shiplock rules: {_rules.SECRET_NAME} set with {counts}.")
    print("The secret is a copy: rerun this after changing any rules file.")
    if not args.repo_only:
        print(_rules.shared_repo_caution())
    return EXIT_OK


def _cmd_needs_import(root: Path) -> int:
    """Print ``true`` or ``false``: does this repo's config need the package?

    A CI gate reads this to decide whether to install the checked repo before
    running ``shiplock check``. Only the version and coverage checks import it,
    so an app repo that configures neither needn't be installable. On a config
    error, the message goes to stderr and the value prints ``false`` (the gate
    skips the install, and the following ``shiplock check`` surfaces the same
    error properly); exit stays 0 so the gate reads a clean value.
    """
    if not root.is_dir():
        print(
            f"shiplock: '{root}' isn't a directory it can inspect. Point it at "
            f"a repo root, or run it from inside one.",
            file=sys.stderr,
        )
        print("false")
        return EXIT_OK

    defaulted = not (root / CONFIG_FILENAME).is_file()
    try:
        config = default_config(root) if defaulted else load_config(root)
    except ConfigError as exc:
        print(f"shiplock: {exc}", file=sys.stderr)
        print("false")
        return EXIT_OK

    print("true" if needs_import(config) else "false")
    return EXIT_OK


def _to_json(report: Report) -> dict:
    """The report's canonical machine shape."""
    return {
        "ok": report.ok,
        "findings": [
            {"check": f.check, "message": f.message, "path": f.path, "line": f.line}
            for f in report.findings
        ],
        "notices": [
            {"check": n.check, "kind": n.kind, "message": n.message} for n in report.notices
        ],
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
        label = "skipped" if notice.kind == "skip" else notice.kind
        print(f"shiplock: {notice.check} {label} — {notice.message}", file=sys.stderr)

    n = len(report.findings)
    if report.ok:
        print(_paint("shiplock: clean", _GREEN, sys.stderr), file=sys.stderr)
    else:
        word = "finding" if n == 1 else "findings"
        print(_paint(f"shiplock: {n} {word}", _RED, sys.stderr), file=sys.stderr)


def _cmd_prompt(kind: str = "audit") -> int:
    # Anchor on the shiplock package itself, not the prompts subdirectory, which
    # has no __init__ and would otherwise rely on namespace-package resolution.
    text = files("shiplock").joinpath("prompts", f"{kind}.md").read_text(encoding="utf-8")
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
    print("  shiplock scan                 scan tracked files for leaks and internal refs")
    print("  shiplock init                 set a repo up: config, rules file, git hooks")
    print("  shiplock rules add ...        add a code-name, email, folder, or file to keep out")
    print("  shiplock prompt               print the semantic audit prompt")
    print("  shiplock prompt ablation      print the advisory ablation prompt")
    print("  shiplock needs-import         does this repo's config need it installed?")
    print()
    print("Docs: https://github.com/shehuphd/shiplock")


if __name__ == "__main__":
    raise SystemExit(main())
