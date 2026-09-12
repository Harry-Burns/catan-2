"""Run the rule verifier over a batch of self-play games.

    python tools/verify.py                      # 25 games with the JSettlers bot
    python tools/verify.py --games 200
    python tools/verify.py --players random     # wider action coverage
    python tools/verify.py --players chaos      # uniform over every legal move
    python tools/verify.py --seed 4242 --stop-on-finding
    python tools/verify.py --show 10            # more examples per finding

Slow by design -- every action is re-derived from the rulebook and the whole
game state is diffed. Expect a few games a second.
"""

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification import verify_games                       # noqa: E402
from verification.report import Severity                    # noqa: E402


class ChaosPlayer:
    """Picks uniformly over every legal move.

    Deliberately bad at Catan, which is the point: a rule-following bot never
    visits the odd corners of the engine -- bank shortages, road-builder edge
    cases, long trade chains -- and those are where bugs live.
    """

    def __init__(self, player_id, seed=None):
        self.player_id = player_id
        self.rng = random.Random(seed)

    def decide(self, gs, playable_moves):
        return playable_moves[self.rng.randrange(len(playable_moves))]


def make_factory(kind: str):
    if kind == "chaos":
        return lambda pid, seed: ChaosPlayer(pid, seed)
    if kind == "random":
        from players.player import RandomDistributedNoTrades
        return lambda pid, seed: RandomDistributedNoTrades(player_id=pid, seed=seed)
    if kind == "jsettlers":
        from players.player_jsettlers import JSettlersPlayer
        return lambda pid, seed: JSettlersPlayer(player_id=pid, seed=seed)
    raise SystemExit(f"unknown player kind: {kind}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=25)
    ap.add_argument("--seed", type=int, default=9000, help="base seed")
    ap.add_argument("--players", default="jsettlers",
                    choices=("jsettlers", "random", "chaos"))
    ap.add_argument("--max-steps", type=int, default=10_000)
    ap.add_argument("--stop-on-finding", action="store_true",
                    help="halt at the first RULE or CORRUPTION finding")
    ap.add_argument("--show", type=int, default=3,
                    help="examples to print per finding code")
    ap.add_argument("--quiet", action="store_true", help="no progress line")
    args = ap.parse_args()

    factory = make_factory(args.players)

    def progress(done, total, new_findings):
        if args.quiet:
            return
        mark = f" +{new_findings}" if new_findings else ""
        print(f"\r  game {done}/{total}{mark}          ", end="", flush=True)

    print(f"verifying {args.games} games, players={args.players}, seed={args.seed}")
    report = verify_games(
        factory,
        n_games=args.games,
        base_seed=args.seed,
        max_steps=args.max_steps,
        stop_on_finding=args.stop_on_finding,
        progress=progress,
    )
    if not args.quiet:
        print("\r" + " " * 40 + "\r", end="")

    report.stats["players"] = args.players
    print(report.summary(max_examples=args.show))

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
