"""Golden-master regression check for the engine.

Unlike tools/difftest.py this needs no second tree: the expected digests are
baked in below. Any change that alters what the engine actually does will change
a digest and fail here.

    python tools/regression.py            # check against the recorded digests
    python tools/regression.py --record   # print fresh digests to paste in

Re-record ONLY when you have deliberately changed game behaviour, and say so in
the commit message.
"""
import hashlib
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.engine import Engine                                    # noqa: E402
from catan.engine import playable_moves, apply_action_inplace     # noqa: E402
from catan.ids import NONE_PLAYER                                 # noqa: E402

MAX_STEPS = 10_000

EXPECTED = {
    "picker": "7cb5095ba1f42fe5",
    "jsettlers": "d7c706b27ce2f339",
    "seeded_random": "2cef688b7de11da0",
}


def _digest_state(gs, steps, out):
    b = gs.board
    out.update(repr((
        steps, int(gs.winner), int(gs.turn_index), int(gs.prompt),
        b.road_owner.tolist(), b.settlement_owner.tolist(), b.settlement_type.tolist(),
        int(b.robber_hex), b.bank_res.tolist(),
        [(p.hand.tolist(), int(p.ports_mask), p.dev_cards.tolist(),
          int(p.used_knights), int(p.longest_road_len),
          int(p.roads_built), int(p.settlements_built), int(p.cities_built))
         for p in gs.players],
        int(gs.longest_road_owner), int(gs.largest_army_owner),
        list(gs.action_log),
    )).encode())


def run_picker(n_games=40, seed0=5000):
    """Uniform-random over every legal move: exercises trades, robber, discards."""
    out = hashlib.md5()
    for game in range(n_games):
        seed = seed0 + game
        picker = random.Random(seed)
        eng = Engine(seed=seed)
        gs = eng.gs
        steps = 0
        while int(gs.winner) == NONE_PLAYER and steps < MAX_STEPS:
            moves = playable_moves(gs)
            apply_action_inplace(gs, int(moves[picker.randrange(len(moves))]), eng.rng)
            steps += 1
        _digest_state(gs, steps, out)
    return out.hexdigest()[:16]


def run_jsettlers(n_games=40, seed0=1000):
    """The rule-based bot: exercises the planning paths end to end."""
    from players.player_jsettlers import JSettlersPlayer

    out = hashlib.md5()
    for game in range(n_games):
        eng = Engine(seed=seed0 + game)
        gs = eng.gs
        players = [JSettlersPlayer(player_id=i) for i in range(4)]
        steps = 0
        while int(gs.winner) == NONE_PLAYER and steps < MAX_STEPS:
            moves = playable_moves(gs)
            action = players[int(gs.current_player_idx)].decide(gs, moves)
            apply_action_inplace(gs, action, eng.rng)
            steps += 1
        _digest_state(gs, steps, out)
    return out.hexdigest()[:16]


def run_seeded_random(n_games=40, seed0=2000):
    """Seeded random players.

    This suite only has a stable digest if Player.rng is actually per-instance
    and seeded -- if anything reverts to the global `random` / `np.random`, the
    digest goes non-deterministic and this fails.
    """
    from players.player import RandomDistributedNoTrades

    out = hashlib.md5()
    for game in range(n_games):
        seed = seed0 + game
        eng = Engine(seed=seed)
        gs = eng.gs
        players = [RandomDistributedNoTrades(player_id=i, seed=seed * 4 + i) for i in range(4)]
        steps = 0
        while int(gs.winner) == NONE_PLAYER and steps < MAX_STEPS:
            moves = playable_moves(gs)
            action = players[int(gs.current_player_idx)].decide(gs, moves)
            apply_action_inplace(gs, action, eng.rng)
            steps += 1
        _digest_state(gs, steps, out)
    return out.hexdigest()[:16]


SUITES = {
    "picker": run_picker,
    "jsettlers": run_jsettlers,
    "seeded_random": run_seeded_random,
}


def main(record=False):
    results = {name: fn() for name, fn in SUITES.items()}
    if record:
        print("EXPECTED = {")
        for name, digest in results.items():
            print(f'    "{name}": "{digest}",')
        print("}")
        return 0

    failed = False
    for name, digest in results.items():
        want = EXPECTED[name]
        ok = digest == want
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:10s} {digest}"
              + ("" if ok else f"  (expected {want})"))
    print("\nregression:", "OK" if not failed else "BEHAVIOUR CHANGED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(record="--record" in sys.argv))
