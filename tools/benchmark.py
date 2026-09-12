"""Throughput benchmark for the engine.

Deterministic: the same seeds and the same seeded move picker every run, so two
trees are directly comparable.

    python tools/benchmark.py                 # both workloads, this tree
    python tools/benchmark.py --games 100
    python tools/benchmark.py --root ../catan-baseline

Workloads:
    picker     uniform-random over every legal move. Engine-bound: heavy on move
               generation, trades, robber and discard paths.
    jsettlers  the rule-based bot. Representative of real self-play, so this is
               the number that matters for batch runs.
"""
import argparse
import os
import random
import sys
import time


def _bench(fn, n_games, repeats=3):
    fn(2)  # warm imports and the topology cache
    best = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        steps = fn(n_games)
        elapsed = time.perf_counter() - t0
        if best is None or elapsed < best[0]:
            best = (elapsed, steps)
    return best


def build(root):
    sys.path.insert(0, root)
    from game.engine import Engine
    from catan.engine import playable_moves, apply_action_inplace
    from catan.ids import NONE_PLAYER

    def picker(n_games, seed0=5000):
        total = 0
        for game in range(n_games):
            seed = seed0 + game
            rand = random.Random(seed)
            eng = Engine(seed=seed)
            gs = eng.gs
            steps = 0
            while int(gs.winner) == NONE_PLAYER and steps < 10_000:
                moves = playable_moves(gs)
                apply_action_inplace(gs, int(moves[rand.randrange(len(moves))]), eng.rng)
                steps += 1
            total += steps
        return total

    def jsettlers(n_games, seed0=1000):
        from players.player_jsettlers import JSettlersPlayer
        total = 0
        for game in range(n_games):
            eng = Engine(seed=seed0 + game)
            gs = eng.gs
            players = [JSettlersPlayer(player_id=i) for i in range(4)]
            steps = 0
            while int(gs.winner) == NONE_PLAYER and steps < 10_000:
                moves = playable_moves(gs)
                action = players[int(gs.current_player_idx)].decide(gs, moves)
                apply_action_inplace(gs, action, eng.rng)
                steps += 1
            total += steps
        return total

    return {"picker": picker, "jsettlers": jsettlers}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--only", choices=("picker", "jsettlers"))
    args = ap.parse_args()

    suites = build(args.root)
    label = os.path.basename(os.path.abspath(args.root)) or args.root
    for name, fn in suites.items():
        if args.only and name != args.only:
            continue
        elapsed, steps = _bench(fn, args.games)
        print(f"{name:10s} {label:16s} {args.games:4d} games  {steps:7d} steps  "
              f"{elapsed:6.3f}s  {steps/elapsed:9,.0f} steps/s  {args.games/elapsed:7.1f} games/s")


if __name__ == "__main__":
    main()
