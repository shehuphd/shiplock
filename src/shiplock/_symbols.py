"""A symbol index over the repo's source, built once per run.

The docs name things in backticks: a flag, a config key, an environment
variable, a function, a file. Resolving each to where the code declares it is
deterministic work, and it feeds three consumers: the ``doc-anchors`` check (a
named thing must exist), the ``doc-defaults`` check (a stated default must
match the parser's), and the judge's evidence gathering (the code and the
prose around a declaration, handed to the model or the audit).

Python files are read with ``ast``, so a flag's ``default=`` and ``action=``
are literal values rather than regex captures, and every ``os.environ`` read
is found by shape. Other languages get a regex pass for definitions, which is
enough for existence and evidence, not for defaults.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_SOURCE_SUFFIXES = (".py", ".rs", ".js", ".ts", ".tsx", ".go", ".sh", ".toml", ".yml", ".yaml")
_DEF_RE = re.compile(
    r"^\s*(?:pub(?:\(crate\))?\s+)?(?:async\s+)?(?:fn|func|function|def|class|struct|enum|trait|interface|type|const|static|let|var|export\s+(?:const|function|class|default\s+function))\s+([A-Za-z_][\w]*)"
)
_ENV_RE = re.compile(r"(?:process\.env\.|env::var\(\"|getenv\(\"|std::env::var\(\"|\$\{?)([A-Z][A-Z0-9_]{2,})")
_YAML_ENV_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,}):\s", re.M)
_SECRET_RE = re.compile(r"secrets\.([A-Z][A-Z0-9_]{2,})")


@dataclass(frozen=True)
class Location:
    """Where a symbol is declared: a tracked path and a 1-based line span."""

    path: str
    line: int
    end: int
    kind: str  # function | class | assignment | flag | command | env | key | file | definition


@dataclass(frozen=True)
class Flag:
    """One argparse option, with the literal values the parser gives it."""

    name: str
    path: str
    line: int
    default: object = None
    has_default: bool = False
    action: str | None = None
    help: str | None = None


@dataclass
class Index:
    """Symbols by name, plus the raw text of every indexed file."""

    root: Path
    files: dict[str, list[str]] = field(default_factory=dict)
    symbols: dict[str, list[Location]] = field(default_factory=dict)
    flags: dict[str, list[Flag]] = field(default_factory=dict)
    commands: set[str] = field(default_factory=set)
    env_vars: set[str] = field(default_factory=set)
    string_literals: set[str] = field(default_factory=set)

    def lookup(self, token: str) -> list[Location]:
        """Declarations for a backticked token, or an empty list."""
        clean = _clean(token)
        if not clean:
            return []
        if clean in self.files:
            return [Location(clean, 1, min(len(self.files[clean]), 40), "file")]
        if clean.startswith("--"):
            return [Location(f.path, f.line, f.line, "flag") for f in self.flags.get(clean, [])]
        key = re.sub(r"^\[([\w-]+)\]\.([\w-]+)$", r"\2", clean)
        last = key.split(".")[-1].split("(")[0]
        return list(self.symbols.get(last, []))

    def has_literal(self, value: str) -> bool:
        """Whether any source file holds ``value`` as a quoted string."""
        return value in self.string_literals

    def excerpt(self, loc: Location, before: int = 5, after: int = 15) -> str:
        """The lines around a location, with a path header, prose included."""
        lines = self.files.get(loc.path)
        if not lines:
            return ""
        start = max(1, loc.line - before)
        end = min(len(lines), max(loc.end, loc.line) + after)
        return f"# {loc.path}:{start}-{end}\n" + "\n".join(lines[start - 1 : end])


def build_index(root: Path, tracked: list[str], skip: set[str] = frozenset()) -> Index:
    """Index every tracked source file under ``root`` except ``skip`` (the docs)."""
    index = Index(root=root)
    for rel in tracked:
        if rel in skip or not rel.endswith(_SOURCE_SUFFIXES):
            continue
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        index.files[rel] = text.splitlines()
        index.string_literals.update(_literals(text))
        if rel.endswith(".py"):
            _index_python(index, rel, text)
        else:
            _index_regex(index, rel, text)
    return index


def _clean(token: str) -> str:
    token = token.strip().strip("`")
    if token.startswith("shiplock ") or " " in token:
        token = token.split()[0] if token.startswith("--") else token
    return token.strip("()[]{}.,;:")


def _literals(text: str) -> set[str]:
    return {m.group(1) or m.group(2) for m in re.finditer(r"\"([^\"\\\n]{1,80})\"|'([^'\\\n]{1,80})'", text)}


def _add(index: Index, name: str, loc: Location) -> None:
    index.symbols.setdefault(name, []).append(loc)


def _index_python(index: Index, rel: str, text: str) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        _index_regex(index, rel, text)
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _add(index, node.name, Location(rel, node.lineno, node.end_lineno or node.lineno, "function"))
        elif isinstance(node, ast.ClassDef):
            _add(index, node.name, Location(rel, node.lineno, node.end_lineno or node.lineno, "class"))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add(index, target.id, Location(rel, node.lineno, node.end_lineno or node.lineno, "assignment"))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add(index, node.target.id, Location(rel, node.lineno, node.end_lineno or node.lineno, "assignment"))
        elif isinstance(node, ast.Call):
            _index_call(index, rel, node)
        elif isinstance(node, ast.Subscript):
            _index_env_subscript(index, node)


def _index_call(index: Index, rel: str, node: ast.Call) -> None:
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name == "add_argument":
        flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("-")]
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        default = kw.get("default")
        has_default = default is not None
        literal_default = default.value if isinstance(default, ast.Constant) else (ast.unparse(default) if has_default else None)
        action = kw["action"].value if isinstance(kw.get("action"), ast.Constant) else None
        help_text = kw["help"].value if isinstance(kw.get("help"), ast.Constant) else (ast.unparse(kw["help"]) if "help" in kw else None)
        for flag in flags:
            entry = Flag(flag, rel, node.lineno, literal_default, has_default, action, help_text)
            index.flags.setdefault(flag, []).append(entry)
            _add(index, flag, Location(rel, node.lineno, node.end_lineno or node.lineno, "flag"))
    elif name == "add_parser" and node.args and isinstance(node.args[0], ast.Constant):
        cmd = str(node.args[0].value)
        index.commands.add(cmd)
        _add(index, cmd, Location(rel, node.lineno, node.end_lineno or node.lineno, "command"))
    elif name in ("get", "getenv") and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        # os.environ.get("NAME") / os.getenv("NAME") / section.get("key")
        value = node.args[0].value
        target = func.value if isinstance(func, ast.Attribute) else None
        if name == "getenv" or (isinstance(target, ast.Attribute) and target.attr == "environ"):
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value):
                index.env_vars.add(value)
                _add(index, value, Location(rel, node.lineno, node.lineno, "env"))
        else:
            _add(index, value, Location(rel, node.lineno, node.lineno, "key"))


def _index_env_subscript(index: Index, node: ast.Subscript) -> None:
    # os.environ["NAME"]
    value = node.value
    if isinstance(value, ast.Attribute) and value.attr == "environ" and isinstance(node.slice, ast.Constant):
        name = node.slice.value
        if isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
            index.env_vars.add(name)


def _index_regex(index: Index, rel: str, text: str) -> None:
    for i, line in enumerate(text.splitlines(), start=1):
        m = _DEF_RE.match(line)
        if m:
            _add(index, m.group(1), Location(rel, i, i, "definition"))
        for e in _ENV_RE.finditer(line):
            index.env_vars.add(e.group(1))
            # A read through the language's env API is the code's own; a shell
            # ``$VAR`` in a workflow or script only proves the name exists.
            if not e.group(0).startswith("$"):
                _add(index, e.group(1), Location(rel, i, i, "env"))
        for s in _SECRET_RE.finditer(line):
            index.env_vars.add(s.group(1))
    if rel.endswith((".yml", ".yaml")):
        for m in _YAML_ENV_RE.finditer(text):
            index.env_vars.add(m.group(1))
