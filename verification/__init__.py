"""Rule verification for the Catan engine.

A wrapper you can put a game inside. It re-derives everything the engine
computes -- production, placement legality, longest road, victory points --
straight from the board and the rulebook, then checks the engine's answer after
every single action. Nothing here is fast, and none of it is meant to be: it
exists to be run occasionally and tell you whether the engine is still playing
Catan.

    from game.engine import Engine
    from players.player_jsettlers import JSettlersPlayer
    from verification import VerifiedGame

    engine = Engine(seed=1)
    players = [JSettlersPlayer(i) for i in range(4)]
    print(VerifiedGame(engine, players, seed=1).play().summary())

Or from the command line::

    python tools/verify.py --games 25

Findings are graded:

    CORRUPTION  the position is internally impossible -- an engine bug
    RULE        self-consistent but against the rules of Catan
    DEVIATION   a deliberate-looking simplification, reported once with the
                rulebook text so it is a decision rather than a surprise
    NOTE        worth knowing, not wrong
"""

from verification.report import Severity, Violation, VerificationReport
from verification.verifier import GameVerifier, VerifiedGame, verify_games

__all__ = [
    "GameVerifier",
    "VerifiedGame",
    "verify_games",
    "VerificationReport",
    "Violation",
    "Severity",
]
