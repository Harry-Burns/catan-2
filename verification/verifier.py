"""The wrapper: put a game inside it and every step gets audited.

Two ways in.

Wrap a whole game::

    from game.engine import Engine
    from verification import VerifiedGame

    report = VerifiedGame(Engine(seed=1), players).play()
    print(report.summary())

Or drive your own loop and call the verifier around each move::

    v = GameVerifier(seed=1)
    v.begin(gs)
    while not over:
        moves = playable_moves(gs)
        v.before_move(gs, moves)
        action = player.decide(gs, moves)
        apply_action_inplace(gs, action, rng)
        v.after_move(gs, action)
    v.finish(gs)
"""

from catan.actions import PROMPT2STR, RESPONSE2STR, unpack_action
from catan.ids import NONE_PLAYER, N_PLAYERS, VICTORY_POINT

from verification import rules as R
from verification import reference as ref
from verification.board import BoardAuditor
from verification.invariants import InvariantChecker
from verification.legality import LegalityAuditor
from verification.report import Severity, Violation, VerificationReport
from verification.transitions import TransitionChecker


class GameVerifier:
    """Audits one game. Reuse a `VerificationReport` to pool findings."""

    def __init__(self, report: VerificationReport | None = None, seed=None,
                 n_players: int = N_PLAYERS):
        self.report = report if report is not None else VerificationReport()
        self.seed = seed
        self.n_players = n_players

        self._loc = {}
        self._before: ref.Snapshot | None = None
        self._moves: list[int] | None = None
        self._topo = None

        self.board_auditor = BoardAuditor(self._emit)
        self.invariants: InvariantChecker | None = None
        self.legality: LegalityAuditor | None = None
        self.transitions: TransitionChecker | None = None
        self.steps = 0

    # --- plumbing ------------------------------------------------------- #
    def _emit(self, code, severity, message, detail=None):
        violation = Violation(
            code=code, severity=severity, message=message, detail=detail,
            **self._loc,
        )
        # A deviation is a modelling decision, not an incident: the engine
        # either works this way or it does not, so one worked example is the
        # whole story and repeats are only noise.
        if severity == Severity.DEVIATION:
            self.report.add_once(violation)
        else:
            self.report.add(violation)

    def _emit_once(self, code, severity, message, detail=None):
        self.report.add_once(Violation(
            code=code, severity=severity, message=message, detail=detail,
            **self._loc,
        ))

    def _set_location(self, snap: ref.Snapshot | None, action=None):
        loc = {"seed": self.seed, "step": self.steps}
        if snap is not None:
            loc["turn"] = snap.turn_index
            loc["player"] = snap.current_player_idx
            prompt = snap.prompt
            loc["prompt"] = PROMPT2STR[prompt] if 0 <= prompt < len(PROMPT2STR) else str(prompt)
        if action is not None:
            kind, a1, a2 = unpack_action(action)
            name = RESPONSE2STR[kind] if 0 <= kind < len(RESPONSE2STR) else str(kind)
            loc["action"] = f"{name}({a1},{a2})"
        self._loc = loc

    @property
    def checks(self) -> int:
        total = self.board_auditor.checks
        for part in (self.invariants, self.legality, self.transitions):
            if part is not None:
                total += part.checks
        return total

    # --- lifecycle ------------------------------------------------------ #
    def begin(self, gs) -> None:
        self._topo = gs.topology
        self.invariants = InvariantChecker(self._topo, self.n_players, self._emit)
        self.legality = LegalityAuditor(self._topo, self.n_players, self._emit)
        self.transitions = TransitionChecker(self._topo, self.n_players, self._emit)

        self._set_location(None)
        self.board_auditor.run(gs)

        snap = ref.Snapshot(gs)
        self._set_location(snap)
        self.invariants.run(snap, self.transitions.played_progress, gs)

    def before_move(self, gs, moves) -> None:
        snap = ref.Snapshot(gs)
        self._before = snap
        self._moves = list(int(m) for m in moves)
        self._set_location(snap)
        self.legality.run(snap, self._moves)

    def after_move(self, gs, action: int) -> None:
        after = ref.Snapshot(gs)
        before = self._before
        self._set_location(before, action)

        if before is not None:
            self.transitions.run(before, after, int(action), self._moves or [])
            self._check_log(before, after)

        self.steps += 1
        self._set_location(after)
        self.invariants.run(after, self.transitions.played_progress, gs)
        self._before = None
        self._moves = None

    def _check_log(self, before, after):
        self.transitions.checks += 1
        if after.action_log_len != before.action_log_len + 1:
            self._emit("ACTION_LOG", Severity.CORRUPTION,
                       f"action log grew by {after.action_log_len - before.action_log_len}, "
                       f"expected 1")

    def finish(self, gs, hit_step_limit: bool = False) -> VerificationReport:
        snap = ref.Snapshot(gs)
        self._set_location(snap)

        points = [ref.victory_points(self._topo, snap, p, VICTORY_POINT)
                  for p in range(self.n_players)]

        if hit_step_limit:
            self._emit("GAME_DID_NOT_FINISH", Severity.NOTE,
                       f"game hit the step limit with no winner; points were {points}")
        elif snap.winner == NONE_PLAYER:
            self._emit("GAME_ENDED_WITHOUT_WINNER", Severity.CORRUPTION,
                       f"the loop stopped but nobody won; points were {points}")
        else:
            self.transitions.checks += 1
            if points[snap.winner] < R.VICTORY_POINTS_TO_WIN:
                self._emit("FINAL_WINNER_SHORT", Severity.RULE,
                           f"player {snap.winner} won with {points[snap.winner]} points")
            others = [p for p in range(self.n_players) if p != snap.winner]
            for p in others:
                self.transitions.checks += 1
                if points[p] >= R.VICTORY_POINTS_TO_WIN:
                    self._emit("MULTIPLE_WINNERS", Severity.RULE,
                               f"player {snap.winner} was declared the winner but "
                               f"player {p} also has {points[p]} points")

        rolls = self.report.stats.setdefault("identified_rolls", {})
        for face, count in self.transitions.roll_histogram.items():
            rolls[face] = rolls.get(face, 0) + count

        self.report.games += 1
        self.report.steps += self.steps
        self.report.checks += self.checks
        return self.report


class VerifiedGame:
    """Runs a full game with every step audited.

    `engine` is a `game.engine.Engine`; `players` is the usual list of four
    controllers with a `.decide(gs, moves)` method.
    """

    def __init__(self, engine, players, report: VerificationReport | None = None,
                 seed=None, max_steps: int = 10_000, stop_on_finding: bool = False):
        self.engine = engine
        self.players = players
        self.report = report if report is not None else VerificationReport()
        self.seed = seed
        self.max_steps = max_steps
        self.stop_on_finding = stop_on_finding

    def play(self) -> VerificationReport:
        from catan.engine import playable_moves, apply_action_inplace

        gs = self.engine.gs
        verifier = GameVerifier(self.report, seed=self.seed, n_players=len(self.players))
        verifier.begin(gs)

        mark = len(self.report.violations)
        steps = 0
        while int(gs.winner) == NONE_PLAYER and steps < self.max_steps:
            moves = playable_moves(gs)
            verifier.before_move(gs, moves)

            action = int(self.players[int(gs.current_player_idx)].decide(gs, moves))
            apply_action_inplace(gs, action, self.engine.rng)

            verifier.after_move(gs, action)
            steps += 1

            if self.stop_on_finding and self._serious_since(mark):
                break

        verifier.finish(gs, hit_step_limit=steps >= self.max_steps)
        return self.report

    def _serious_since(self, mark: int) -> bool:
        return any(v.severity >= Severity.RULE for v in self.report.violations[mark:])


def verify_games(player_factory, n_games: int = 20, base_seed: int = 9000,
                 max_steps: int = 10_000, stop_on_finding: bool = False,
                 progress=None) -> VerificationReport:
    """Audit `n_games` self-play games.

    `player_factory(pid, seed)` returns one player controller.
    """
    from game.engine import Engine

    report = VerificationReport()

    for index in range(n_games):
        seed = base_seed + index
        engine = Engine(seed=seed)
        players = [player_factory(pid, seed * 4 + pid) for pid in range(N_PLAYERS)]
        game = VerifiedGame(engine, players, report=report, seed=seed,
                            max_steps=max_steps, stop_on_finding=stop_on_finding)
        before = len(report.violations)
        game.play()
        if progress is not None:
            progress(index + 1, n_games, len(report.violations) - before)
        if stop_on_finding and any(
            v.severity >= Severity.RULE for v in report.violations[before:]
        ):
            break

    _dice_sanity(report)
    return report


def _dice_sanity(report: VerificationReport, trials: int = 120_000) -> None:
    """Exercise the engine's own dice directly.

    The rolls seen during play cannot be used for this: a roll nobody has a
    building on pays nothing and so cannot be told apart from any other barren
    roll. Instead call `roll_dice` itself and check the shape of what comes out.
    """
    import numpy as np
    from catan.interface import roll_dice
    from catan.state import GameState

    class _Stub:
        has_rolled = False

    rng = np.random.default_rng(20260912)
    stub = _Stub()
    observed: dict[int, int] = {}
    for _ in range(trials):
        total = roll_dice(stub, rng)
        observed[total] = observed.get(total, 0) + 1

    out_of_range = {k: v for k, v in observed.items()
                    if not (R.DICE_MIN <= k <= R.DICE_MAX)}
    if out_of_range:
        report.add(Violation(
            code="DICE_OUT_OF_RANGE", severity=Severity.CORRUPTION,
            message=f"roll_dice produced totals outside 2-12: {sorted(out_of_range)}",
        ))
        return

    missing = [t for t in R.DICE_WAYS if t not in observed]
    if missing:
        report.add(Violation(
            code="DICE_MISSING_TOTALS", severity=Severity.RULE,
            message=f"roll_dice never produced {missing} in {trials:,} rolls",
        ))

    chi = 0.0
    for total, ways in R.DICE_WAYS.items():
        expected = trials * ways / R.DICE_OUTCOMES
        chi += (observed.get(total, 0) - expected) ** 2 / expected
    report.stats["dice_chi_square"] = f"{chi:.1f} (10 d.o.f., {trials:,} rolls)"

    # 10 degrees of freedom, p = 0.001 -> 29.59
    if chi > 29.59:
        report.add(Violation(
            code="DICE_DISTRIBUTION", severity=Severity.RULE,
            message=f"roll_dice does not match two six-sided dice "
                    f"(chi-square {chi:.1f} on 10 d.o.f. over {trials:,} rolls)",
            detail=(
                f"observed={dict(sorted(observed.items()))}\n"
                f"expected={ {t: round(trials * w / R.DICE_OUTCOMES) for t, w in sorted(R.DICE_WAYS.items())} }"
            ),
        ))
