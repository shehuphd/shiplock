"""Result types produced by a check run.

A check emits ``Finding`` objects (a doc surface disagrees with the code) and
``Notice`` objects (a check was skipped because its surface isn't declared, or
couldn't run for a stated reason). The distinction is load-bearing: a skip is
never a pass. A ``Report`` collects both and answers one question through
``ok`` — did anything fail?
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    """A concrete disagreement between a doc surface and the code.

    ``path`` and ``line`` locate it where known; ``line`` is None when the
    finding is about a file as a whole (a missing doc, a stale count) rather
    than one line.
    """

    check: str
    message: str
    path: str | None = None
    line: int | None = None

    def location(self) -> str:
        """Render ``path:line`` for display, degrading gracefully."""
        if self.path is None:
            return ""
        if self.line is None:
            return self.path
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class Notice:
    """A check that didn't run, with the reason stated.

    Emitted when a check's surface isn't declared in config, or when a
    prerequisite is absent (no git tag to diff against, an import that failed).
    A notice keeps the run honest: the reader sees the check was skipped, not
    that it passed.
    """

    check: str
    message: str


@dataclass
class Report:
    """Everything one ``run_checks`` produced."""

    findings: list[Finding] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no check produced a finding. Notices don't fail a run."""
        return not self.findings

    def extend(self, findings: list[Finding], notices: list[Notice]) -> None:
        """Fold one check's output into the running report."""
        self.findings.extend(findings)
        self.notices.extend(notices)
