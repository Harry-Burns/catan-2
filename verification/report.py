"""Violations, and the report that collects them."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional


class Severity(IntEnum):
    """How bad a finding is.

    CORRUPTION  The state is internally impossible -- cards conjured or lost, a
                counter disagreeing with the board. Always an engine bug.
    RULE        The state is self-consistent but breaks a rule of Catan, or the
                engine offered / accepted a move the rules forbid.
    DEVIATION   The engine deliberately models a rule differently. Reported once
                per run with the rulebook text, so it is a decision on record
                rather than a surprise.
    NOTE        Informational; worth seeing but not wrong.
    """

    NOTE = 0
    DEVIATION = 1
    RULE = 2
    CORRUPTION = 3


SEVERITY_LABEL = {
    Severity.NOTE: "NOTE",
    Severity.DEVIATION: "DEVIATION",
    Severity.RULE: "RULE",
    Severity.CORRUPTION: "CORRUPTION",
}


@dataclass
class Violation:
    code: str
    severity: Severity
    message: str
    detail: Optional[str] = None
    # Where in the run it happened.
    seed: Optional[int] = None
    step: Optional[int] = None
    turn: Optional[int] = None
    prompt: Optional[str] = None
    action: Optional[str] = None
    player: Optional[int] = None

    def location(self) -> str:
        bits = []
        if self.seed is not None:
            bits.append(f"seed={self.seed}")
        if self.step is not None:
            bits.append(f"step={self.step}")
        if self.turn is not None:
            bits.append(f"turn={self.turn}")
        if self.player is not None:
            bits.append(f"player={self.player}")
        if self.prompt:
            bits.append(f"prompt={self.prompt}")
        if self.action:
            bits.append(f"action={self.action}")
        return " ".join(bits)

    def render(self, indent: str = "") -> str:
        head = f"{indent}[{SEVERITY_LABEL[self.severity]}] {self.code}: {self.message}"
        lines = [head]
        loc = self.location()
        if loc:
            lines.append(f"{indent}    at {loc}")
        if self.detail:
            for line in self.detail.rstrip().splitlines():
                lines.append(f"{indent}    {line}")
        return "\n".join(lines)


@dataclass
class VerificationReport:
    violations: list[Violation] = field(default_factory=list)
    games: int = 0
    steps: int = 0
    checks: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    # A single broken invariant can fire on every step of every game. Keep a
    # handful of examples per code and count the rest, so one noisy finding
    # cannot bury the others.
    max_per_code: int = 5
    _counts: dict = field(default_factory=dict)
    _seen_once: set = field(default_factory=set)

    def add(self, violation: Violation) -> None:
        code = violation.code
        seen = self._counts.get(code, 0)
        self._counts[code] = seen + 1
        if seen < self.max_per_code:
            self.violations.append(violation)

    def add_once(self, violation: Violation) -> None:
        """Record a finding only the first time its code appears.

        Used for systematic differences -- the engine either models a rule this
        way or it does not, and one worked example says everything.
        """
        code = violation.code
        self._counts[code] = self._counts.get(code, 0) + 1
        if code in self._seen_once:
            return
        self._seen_once.add(code)
        self.violations.append(violation)

    # --- querying -----------------------------------------------------------
    def by_severity(self, severity: Severity) -> list[Violation]:
        return [v for v in self.violations if v.severity == severity]

    @property
    def ok(self) -> bool:
        """True when nothing worse than a deviation was found."""
        return not any(v.severity >= Severity.RULE for v in self.violations)

    def counts(self) -> dict[str, int]:
        """True totals, including findings that were counted but not stored."""
        return dict(self._counts)

    def merge(self, other: "VerificationReport") -> None:
        self.violations.extend(other.violations)
        self.games += other.games
        self.steps += other.steps
        self.checks += other.checks
        self._seen_once |= other._seen_once
        for code, n in other._counts.items():
            self._counts[code] = self._counts.get(code, 0) + n

    # --- rendering ----------------------------------------------------------
    def summary(self, max_examples: int = 3) -> str:
        lines = []
        lines.append("=" * 72)
        lines.append("CATAN RULE VERIFICATION")
        lines.append("=" * 72)
        lines.append(
            f"games: {self.games}   steps: {self.steps}   assertions: {self.checks:,}"
        )
        for key, value in self.stats.items():
            lines.append(f"{key}: {value}")
        lines.append("")

        if not self.violations:
            lines.append("No findings. Every check passed.")
            lines.append("=" * 72)
            return "\n".join(lines)

        counts = self.counts()
        # group by code, most severe first then most frequent
        grouped: dict[str, list[Violation]] = {}
        for v in self.violations:
            grouped.setdefault(v.code, []).append(v)
        order = sorted(
            grouped.items(),
            key=lambda kv: (-max(v.severity for v in kv[1]), -len(kv[1])),
        )

        for severity in (Severity.CORRUPTION, Severity.RULE, Severity.DEVIATION, Severity.NOTE):
            codes = [(c, vs) for c, vs in order if max(v.severity for v in vs) == severity]
            if not codes:
                continue
            label = SEVERITY_LABEL[severity]
            total = sum(counts.get(c, len(vs)) for c, vs in codes)
            lines.append(f"--- {label} ({total} finding{'s' if total != 1 else ''}) " + "-" * max(0, 44 - len(label)))
            for code, vs in codes:
                total_for_code = counts.get(code, len(vs))
                lines.append(f"\n{code}  x{total_for_code}")
                for v in vs[:max_examples]:
                    lines.append(v.render(indent="  "))
                shown = min(len(vs), max_examples)
                if total_for_code > shown:
                    lines.append(f"  ... and {total_for_code - shown} more")
            lines.append("")

        lines.append("=" * 72)
        verdict = "PASS" if self.ok else "FAIL"
        lines.append(f"verdict: {verdict}")
        lines.append("=" * 72)
        return "\n".join(lines)
