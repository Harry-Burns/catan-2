"""Does the verifier actually catch anything?

A checker that never fires is indistinguishable from a checker that works, so
this breaks the engine on purpose -- one fault at a time -- and asserts the
right finding comes out. Run it after changing anything under verification/.

    python tools/verify_selftest.py
    python tools/verify_selftest.py --games 4     # more games per mutation

Each mutation monkey-patches one function in `catan.engine` (the names the
dispatcher actually calls), runs a few games through the verifier, and checks
the expected code appears. It also runs the engine untouched and checks that
none of these codes fire on a clean build.
"""

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catan.engine as engine                                   # noqa: E402
from catan.ids import KNIGHT, CITY, N_RES, NONE_PLAYER          # noqa: E402
from verification import verify_games                           # noqa: E402


class RandomBot:
    def __init__(self, player_id, seed=None):
        self.player_id = player_id
        self.rng = random.Random(seed)

    def decide(self, gs, playable_moves):
        return playable_moves[self.rng.randrange(len(playable_moves))]


# --- the faults ------------------------------------------------------------

def free_roads(original):
    """Build roads without paying for them."""
    def patched(gs, rid):
        from catan.interface import _place_road
        _place_road(gs, int(gs.current_player_idx), rid)
    return patched


def greedy_robber(original):
    """Steal two cards instead of one."""
    def patched(gs, rng, hex_id, victim_pid):
        original(gs, rng, hex_id, victim_pid)
        if victim_pid != NONE_PLAYER:
            victim = gs.players[victim_pid]
            for res in range(N_RES):
                if victim.hand[res] > 0:
                    victim.hand[res] -= 1
                    gs.players[int(gs.current_player_idx)].hand[res] += 1
                    break
    return patched


def counterfeit_cards(original):
    """Hand out a wood card that never leaves the bank."""
    def patched(gs, total):
        original(gs, total)
        gs.players[int(gs.current_player_turn_idx)].hand[0] += 1
    return patched


def soft_discard(original):
    """Only ever ask for one discard, whatever the hand size."""
    def patched(gs):
        original(gs)
        for player in gs.players:
            if player.discards_required > 1:
                player.discards_required = 1
    return patched


def eager_longest_road(original):
    """Award Longest Road to whoever moved, at any length."""
    def patched(gs, pid, a):
        original(gs, pid, a)
        return int(pid)
    return patched


def phantom_knight(original):
    """Play knights without ever discarding the card."""
    def patched(gs):
        original(gs)
        gs.players[int(gs.current_player_idx)].dev_cards[KNIGHT] += 1
    return patched


def paper_city(original):
    """Charge for a city and leave the settlement standing."""
    def patched(gs, cid):
        before_type = int(gs.board.settlement_type[cid])
        original(gs, cid)
        gs.board.settlement_type[cid] = before_type
    return patched


def sticky_robber(original):
    """Take the payment for moving the robber but leave it where it was."""
    def patched(gs, rng, hex_id, victim_pid):
        where = int(gs.board.robber_hex)
        original(gs, rng, hex_id, victim_pid)
        gs.board.robber_hex = where
    return patched


MUTATIONS = [
    # (name, function in catan.engine, wrapper, codes that must appear)
    ("free roads", "purchase_road", free_roads,
     ("BUILD_COST_WRONG",)),
    # Note: stealing an extra card moves it between players, so the resource
    # totals still balance. Only the per-action check can see this one.
    ("robber steals two", "move_robber", greedy_robber,
     ("ROBBER_STEAL_AMOUNT",)),
    ("cards from nowhere", "distribute_resources", counterfeit_cards,
     ("RESOURCE_CONSERVATION",)),
    ("half the discards", "handle_7", soft_discard,
     ("DISCARD_AMOUNT_WRONG",)),
    ("longest road for free", "get_longest_road", eager_longest_road,
     ("LONGEST_ROAD_OWNER",)),
    ("knights never spent", "play_knight", phantom_knight,
     ("DEV_CARD_CONSERVATION",)),
    ("city that is not built", "purchase_city", paper_city,
     ("CITY_NOT_PLACED",)),
    ("robber does not move", "move_robber", sticky_robber,
     ("ROBBER_NOT_MOVED",)),
]

# Codes a clean engine must not produce. (Findings the verifier legitimately
# reports on the current engine are listed in the run output, not here.)
MUST_BE_SILENT = sorted({code for _, _, _, codes in MUTATIONS for code in codes})


def run(games: int, seed: int, max_steps: int):
    return verify_games(
        lambda pid, s: RandomBot(pid, s),
        n_games=games, base_seed=seed, max_steps=max_steps,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=3)
    ap.add_argument("--seed", type=int, default=4100)
    ap.add_argument("--max-steps", type=int, default=4000)
    args = ap.parse_args()

    width = max(len(name) for name, *_ in MUTATIONS) + 2
    failures = 0

    print("clean engine".ljust(width), end="", flush=True)
    baseline = run(args.games, args.seed, args.max_steps)
    counts = baseline.counts()
    noisy = [code for code in MUST_BE_SILENT if counts.get(code)]
    if noisy:
        failures += 1
        print(f"FAIL  unexpectedly reported {noisy}")
    else:
        print(f"ok    ({baseline.steps} steps, {baseline.checks:,} assertions, "
              f"none of the fault codes fired)")

    for name, target, wrapper, expected in MUTATIONS:
        print(name.ljust(width), end="", flush=True)
        original = getattr(engine, target)
        setattr(engine, target, wrapper(original))
        try:
            report = run(args.games, args.seed, args.max_steps)
            counts = report.counts()
        except Exception as exc:                      # a broken engine may throw
            counts = {}
            print(f"(engine raised {type(exc).__name__}) ", end="")
        finally:
            setattr(engine, target, original)

        caught = [code for code in expected if counts.get(code)]
        missed = [code for code in expected if not counts.get(code)]
        if missed:
            failures += 1
            print(f"FAIL  missed {missed}")
        else:
            detail = ", ".join(f"{c} x{counts[c]}" for c in caught)
            print(f"ok    caught {detail}")

    print()
    if failures:
        print(f"{failures} mutation(s) went undetected -- the verifier has a hole.")
        return 1
    print(f"All {len(MUTATIONS)} injected faults were caught, and none of them "
          f"fired on the clean engine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
