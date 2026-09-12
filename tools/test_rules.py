"""Targeted tests for specific rules, on positions built by hand.

The verifier in verification/ sweeps whole games and is good at finding things.
These are the opposite: one position each, constructed so that exactly one rule
is under test, so a regression says precisely what broke.

    python tools/test_rules.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                              # noqa: E402

import catan.interface as interface                             # noqa: E402
from catan.engine import apply_action_inplace, playable_moves   # noqa: E402
from catan.actions import (                                     # noqa: E402
    PLAY_PRETURN, PLAY_TURN, MOVE_ROBBER, SETUP_TURN,
    ROLL, PLAY_ROAD_BUILDER, SELECT_ROBBER_RESPONSE,
    act_play_knight, act_select_robber_response,
    get_response, unpack_action,
)
from catan.ids import (                                         # noqa: E402
    DESERT, SETTLEMENT, NONE_PLAYER, KNIGHT, ROAD_BUILDER,
    N_PLAYERS, N_RES,
)
from game.engine import Engine                                  # noqa: E402


class Failure(Exception):
    pass


def check(condition, message):
    if not condition:
        raise Failure(message)


def find_road_chain(topo, length):
    """A run of `length` paths through distinct intersections.

    Returns (paths, end_a, end_b) -- the two ends are where the blocking
    settlements go.
    """
    adjacency = {}
    for rid, (a, b) in enumerate(topo.py_road_adj_sett):
        if a >= 0 and b >= 0:
            adjacency.setdefault(a, []).append((rid, b))
            adjacency.setdefault(b, []).append((rid, a))

    def walk(node, used_paths, seen_nodes):
        if len(used_paths) == length:
            return list(used_paths), node
        for rid, nxt in adjacency.get(node, ()):
            if rid in used_paths or nxt in seen_nodes:
                continue
            used_paths.append(rid)
            seen_nodes.add(nxt)
            found = walk(nxt, used_paths, seen_nodes)
            if found:
                return found
            used_paths.pop()
            seen_nodes.discard(nxt)
        return None

    for start in adjacency:
        result = walk(start, [], {start})
        if result:
            paths, end = result
            return paths, start, end
    raise Failure(f"no run of {length} paths on this board")


# --------------------------------------------------------------------------- #
def test_longest_road_counts_between_opponents():
    """An opponent's building breaks a road; it does not delete the segments.

    A run may start or end at an occupied intersection -- it may only not pass
    through one. The engine used to refuse to start there and so undercounted
    any run bookended by opponents.
    """
    eng = Engine(seed=11)
    gs = eng.gs
    topo = gs.topology

    paths, end_a, end_b = find_road_chain(topo, 5)
    for rid in paths:
        gs.board.road_owner[rid] = 0
    gs.players[0].roads_built = len(paths)

    # bookend the run with opponents
    for node, owner in ((end_a, 1), (end_b, 2)):
        gs.board.settlement_owner[node] = owner
        gs.board.settlement_type[node] = SETTLEMENT

    measured = interface._calculate_longest_road(gs, 0)
    check(measured == 5,
          f"run of 5 between two opponents measured as {measured}")

    # ...but a run may not pass THROUGH one: block the middle and the longest
    # remaining stretch is the longer of the two halves.
    middle = topo.py_road_adj_sett[paths[2]][0]
    if middle in (end_a, end_b):
        middle = topo.py_road_adj_sett[paths[2]][1]
    gs.board.settlement_owner[middle] = 3
    gs.board.settlement_type[middle] = SETTLEMENT

    split = interface._calculate_longest_road(gs, 0)
    check(split < 5,
          f"a run cut in the middle still measured {split}; it must not pass "
          f"through an opponent's building")


def test_road_building_playable_with_one_spot():
    """Road Building stays playable when the board allows only one road.

    Two free roads "according to normal building rules" means one road when one
    placement is legal, not a card that cannot be played at all.
    """
    eng = Engine(seed=12)
    gs = eng.gs
    gs.players[0].roads_built = 3       # plenty of pieces left

    original = interface._get_placeable_roads_from
    try:
        # exactly one legal spot, and taking it opens nothing further
        def only_one(_gs, extra_road=-1):
            if extra_road < 0:
                return np.asarray([7], dtype=np.int32)
            return np.asarray([], dtype=np.int32)

        interface._get_placeable_roads_from = only_one
        pairs = interface._generate_playable_road_builder(gs)
    finally:
        interface._get_placeable_roads_from = original

    check(len(pairs) >= 1,
          "Road Building offered no placements when one road was legal")
    check(tuple(pairs[0]) == (7, 7),
          f"expected the single-road encoding (7, 7), got {tuple(pairs[0])}")

    # and the encoding must actually place one road, not two
    gs.players[0].dev_cards[ROAD_BUILDER] = 1
    before = int(gs.players[0].roads_built)
    interface.play_road_builder(gs, 7, 7)
    placed = int(gs.players[0].roads_built) - before
    check(placed == 1, f"the (7, 7) pair placed {placed} roads, expected 1")


def test_knight_before_rolling_still_requires_the_roll():
    """Rulebook p.4: you must roll for resource production every turn."""
    eng = Engine(seed=13)
    gs = eng.gs

    # skip setup
    while gs.prompt == SETUP_TURN:
        apply_action_inplace(gs, int(playable_moves(gs)[0]), eng.rng)

    actor = int(gs.current_player_idx)
    gs.players[actor].dev_cards[KNIGHT] = 1
    check(gs.prompt == PLAY_PRETURN, "expected to be before the roll")
    check(not gs.has_rolled, "has_rolled set before rolling")

    apply_action_inplace(gs, act_play_knight(), eng.rng)
    check(gs.prompt == MOVE_ROBBER, "playing a knight did not call for the robber")

    robber_move = next(m for m in playable_moves(gs)
                       if get_response(int(m)) == SELECT_ROBBER_RESPONSE)
    apply_action_inplace(gs, int(robber_move), eng.rng)

    check(not gs.has_rolled, "the roll was somehow recorded without rolling")
    check(gs.prompt == PLAY_PRETURN,
          f"after a pre-roll knight the prompt was {gs.prompt}, expected "
          f"PLAY_PRETURN so the player still has to roll")
    check(any(get_response(int(m)) == ROLL for m in playable_moves(gs)),
          "the player cannot roll after playing a knight before the roll")


def test_second_setup_settlement_pays_out():
    """Almanac, Set-up Phase: the second settlement pays one card per adjacent
    producing hex, immediately."""
    eng = Engine(seed=14)
    gs = eng.gs
    topo = gs.topology

    second_settlement = {}
    while gs.prompt == SETUP_TURN:
        actor = int(gs.current_player_idx)
        placing_second = int(gs.players[actor].settlements_built) == 1
        action = int(playable_moves(gs)[0])
        _, sid, _ = unpack_action(action)
        if placing_second:
            second_settlement[actor] = sid
        apply_action_inplace(gs, action, eng.rng)

    check(len(second_settlement) == N_PLAYERS,
          f"only {len(second_settlement)} players placed a second settlement")

    for pid, sid in second_settlement.items():
        expected = [0] * N_RES
        for hid in topo.py_sett_adj_hex[sid]:
            res = topo.py_hex_resource[hid]
            if res != DESERT:
                expected[res] += 1
        actual = gs.players[pid].hand.tolist()
        check(actual == expected,
              f"player {pid}'s second settlement at {sid} paid {actual}, "
              f"expected {expected}")

    # the cards must come out of the bank, not thin air
    for res in range(N_RES):
        handed = sum(gs.players[p].hand[res] for p in range(N_PLAYERS))
        check(int(gs.board.bank_res[res]) + handed == 19,
              f"setup payout broke conservation for resource {res}")


def test_stealing_is_mandatory_when_a_victim_is_adjacent():
    """Rulebook p.5: after moving the robber you steal from an adjacent
    opponent. Declining is only an option when nobody is there to rob."""
    eng = Engine(seed=15)
    gs = eng.gs
    topo = gs.topology

    while gs.prompt == SETUP_TURN:
        apply_action_inplace(gs, int(playable_moves(gs)[0]), eng.rng)

    actor = int(gs.current_player_idx)
    gs.prompt = MOVE_ROBBER
    gs.current_player_idx = actor

    moves = interface.generate_robber_moves(gs)
    by_hex = {}
    for action in moves:
        _, hid, victim = unpack_action(action)
        by_hex.setdefault(hid, set()).add(victim)

    checked_occupied = checked_empty = 0
    for hid, victims in by_hex.items():
        opponents = {
            int(gs.board.settlement_owner[sid])
            for sid in topo.py_hex_settlement[hid]
            if int(gs.board.settlement_owner[sid]) not in (-1, actor)
        }
        if opponents:
            check(NONE_PLAYER not in victims,
                  f"hex {hid} has opponents {sorted(opponents)} but the engine "
                  f"still offered stealing from nobody")
            check(victims == opponents,
                  f"hex {hid}: offered victims {sorted(victims)}, adjacent "
                  f"opponents are {sorted(opponents)}")
            checked_occupied += 1
        else:
            check(victims == {NONE_PLAYER},
                  f"hex {hid} has no opponents on it but offered {sorted(victims)}")
            checked_empty += 1

    check(checked_occupied > 0, "no hex with an adjacent opponent to test")
    check(checked_empty > 0, "no empty hex to test")


TESTS = [
    ("longest road counts between opponents", test_longest_road_counts_between_opponents),
    ("road building playable with one spot", test_road_building_playable_with_one_spot),
    ("knight before rolling still needs the roll", test_knight_before_rolling_still_requires_the_roll),
    ("second setup settlement pays out", test_second_setup_settlement_pays_out),
    ("stealing is mandatory when possible", test_stealing_is_mandatory_when_a_victim_is_adjacent),
]


def main() -> int:
    width = max(len(name) for name, _ in TESTS) + 2
    failures = 0
    for name, test in TESTS:
        print(name.ljust(width), end="", flush=True)
        try:
            test()
        except Failure as exc:
            failures += 1
            print(f"FAIL  {exc}")
        except Exception as exc:                       # noqa: BLE001
            failures += 1
            print(f"ERROR {type(exc).__name__}: {exc}")
        else:
            print("ok")

    print()
    if failures:
        print(f"{failures} of {len(TESTS)} rule tests failed.")
        return 1
    print(f"All {len(TESTS)} rule tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
