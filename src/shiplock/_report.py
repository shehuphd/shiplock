"""Result types produced by a check run.

A check emits ``Finding`` objects (a doc surface disagrees with the code) and
``Notice`` objects: a skip (a check's surface isn't declared, or it couldn't run
for a stated reason) or a warning (something to see that doesn't fail the run,
such as an allowed match or a deprecated config key). The distinction is
load-bearing: a skip is never a pass. A ``Report`` collects both and answers one question through
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
    """Something the reader should see that doesn't fail the run.

    ``kind`` is ``"skip"`` (the default) when a check didn't run: its surface
    isn't declared, or a prerequisite is absent (no git tag to diff against, an
    import that failed). A skip keeps the run honest: the reader sees the check
    didn't run, not that it passed. ``kind`` is ``"warning"`` for something
    from a check that did run and that the reader should look at, such as
    matches against allowed entries or a deprecated config key, and ``"info"``
    for plain context about the run, such as where its rules came from.
    """

    check: str
    message: str
    kind: str = "skip"


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
