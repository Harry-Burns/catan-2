"""Tests for the web display's view of the board.

The renderer places every piece from hex geometry alone: it is handed each
hex's six corner and edge ids (already rotated into display order by
`api_adapter`) and works out screen positions from the hex centre. If that
rotation is ever wrong, roads stop meeting settlements and ports drift off
their edges -- visible on screen, but only if you happen to look.

So this file recomputes the renderer's geometry in Python and checks it lines
up with what `catan/topology.py` says is adjacent to what.

    python tools/test_display.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from display.web.api_adapter import gamestate2api, _to_display, _to_engine  # noqa: E402
from display.web.movelog import MoveRecorder, snapshot                      # noqa: E402
from game.engine import Engine                                              # noqa: E402
from game.runner import GameRunner                                          # noqa: E402
from players.player import RandomPlayer                                      # noqa: E402

# Mirrors the constants at the top of display/web/static/board.js.
SIZE = 50.0
R = SIZE / math.cos(math.pi / 6)
HEX_W = 2 * SIZE
ROW_STEP = R * 1.5
ROWS, COLS = 5, 9
EXCLUDED = {(0, 0), (0, 8), (4, 0), (4, 8)}

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{name:<46}{'ok' if ok else 'FAILED'}")
    if not ok:
        _failures.append(f"{name}: {detail}" if detail else name)


def _is_hex(x: int, y: int) -> bool:
    return 0 <= x < ROWS and 0 <= y < COLS and (x + y) % 2 == 0 and (x, y) not in EXCLUDED


def _hex_centres() -> list[tuple[float, float]]:
    """Same enumeration order as generate_topology(), so index == hex id."""
    return [((y / 2) * HEX_W, x * ROW_STEP)
            for x in range(ROWS) for y in range(COLS) if _is_hex(x, y)]


def _vertex(centre: tuple[float, float], d: int) -> tuple[float, float]:
    a = math.pi / 3 * d + math.pi / 6
    return (round(centre[0] + R * math.cos(a), 4), round(centre[1] + R * math.sin(a), 4))


def _played_game(seed: int = 7, steps: int = 400):
    engine = Engine(seed=seed)
    runner = GameRunner(engine, [RandomPlayer(i, seed=seed * 4 + i) for i in range(4)])
    for _ in range(steps):
        if runner.finished:
            break
        runner.play_action()
        runner.steps += 1
    return engine, runner


def test_rotation_is_self_inverse():
    ok = all(_to_engine(_to_display(i)) == i for i in range(6))
    check("display/engine rotation round-trips", ok)


def test_geometry():
    engine, runner = _played_game()
    board = gamestate2api(engine.gs)
    centres = _hex_centres()

    check("hex count", len(board.hexes) == 19, str(len(board.hexes)))
    check("node count", len(board.nodes) == 54, str(len(board.nodes)))
    check("edge count", len(board.edges) == 72, str(len(board.edges)))
    check("port count", len(board.ports) == 9, str(len(board.ports)))

    # Every hex naming a node must put it in exactly the same place.
    node_pt: dict[int, tuple] = {}
    edge_seg: dict[int, frozenset] = {}
    conflicts = []
    for hx in board.hexes:
        centre = centres[hx.id]
        for d in range(6):
            nid, eid = hx.nodes[d], hx.edges[d]
            pt = _vertex(centre, d)
            if node_pt.setdefault(nid, pt) != pt:
                conflicts.append(f"node {nid} at hex {hx.id} v{d}")
            seg = frozenset([_vertex(centre, d), _vertex(centre, (d + 1) % 6)])
            if edge_seg.setdefault(eid, seg) != seg:
                conflicts.append(f"edge {eid} at hex {hx.id} e{d}")
    check("shared corners agree across hexes", not conflicts, "; ".join(conflicts[:3]))
    check("every node placed", len(node_pt) == 54, str(len(node_pt)))
    check("every edge placed", len(edge_seg) == 72, str(len(edge_seg)))

    # A road's drawn segment must join the two nodes the topology calls its ends.
    bad = [e.id for e in board.edges
           if edge_seg[e.id] != frozenset([node_pt[e.nodes[0]], node_pt[e.nodes[1]]])]
    check("roads join their endpoint nodes", not bad, f"edges {bad[:5]}")

    # Adjacent intersections are exactly one hex radius apart.
    far = [(n.id, m) for n in board.nodes for m in n.adj_nodes
           if abs(math.dist(node_pt[n.id], node_pt[m]) - R) > 0.01]
    check("adjacent nodes are one radius apart", not far, str(far[:3]))

    # Ports sit on the edge between the two nodes that grant them.
    off = [p.id for p in board.ports
           if frozenset([_vertex(centres[p.location.hexId], p.location.edge),
                         _vertex(centres[p.location.hexId], (p.location.edge + 1) % 6)])
           != frozenset([node_pt[p.nodes[0]], node_pt[p.nodes[1]]])]
    check("ports sit on their own edge", not off, f"ports {off}")

    return engine, runner, board


def test_state_matches_engine(engine, board):
    gs = engine.gs
    owned_nodes = {n.id for n in board.nodes if n.owner}
    engine_nodes = {i for i, o in enumerate(gs.board.settlement_owner.tolist()) if o >= 0}
    check("owned nodes match the engine", owned_nodes == engine_nodes,
          str(owned_nodes ^ engine_nodes))

    owned_edges = {e.id for e in board.edges if e.owner}
    engine_edges = {i for i, o in enumerate(gs.board.road_owner.tolist()) if o >= 0}
    check("owned edges match the engine", owned_edges == engine_edges,
          str(owned_edges ^ engine_edges))

    robber = [h.id for h in board.hexes if h.hasRobber]
    check("robber is on exactly one hex",
          robber == [int(gs.board.robber_hex)], str(robber))

    # VP breakdown must add up to the total the engine reports.
    bad = [p.player_id for p in board.players_data
           if (p.vp_breakdown.settlements + p.vp_breakdown.cities
               + p.vp_breakdown.longest_road + p.vp_breakdown.largest_army
               + p.vp_breakdown.dev_cards) != p.victory_points]
    check("VP breakdowns sum to the total", not bad, str(bad))

    # Pips are the standard 6 - |7 - n|, and the desert has none.
    bad_pips = [h.id for h in board.hexes
                if h.pips != (0 if not h.number else 6 - abs(7 - h.number))]
    check("number tokens carry the right pips", not bad_pips, str(bad_pips))


def test_legal_targets_are_real(board):
    node_ids = {n.id for n in board.nodes}
    edge_ids = {e.id for e in board.edges}
    hex_ids = {h.id for h in board.hexes}
    legal = board.legal

    ok = (set(legal.settlement_nodes) <= node_ids
          and set(legal.city_nodes) <= node_ids
          and set(legal.setup_nodes) <= node_ids
          and set(legal.road_edges) <= edge_ids
          and set(legal.setup_edges) <= edge_ids
          and set(legal.robber_hexes) <= hex_ids)
    check("legal targets reference real slots", ok)
    check("legal move total is consistent",
          legal.total >= sum(legal.action_counts.values()) or legal.error is not None,
          f"total={legal.total} counts={legal.action_counts}")

    # Cities can only go where the player already has a settlement.
    by_id = {n.id: n for n in board.nodes}
    bad = [n for n in legal.city_nodes if by_id[n].type != 'settlement']
    check("city upgrades target own settlements", not bad, str(bad))


def test_move_log(runner):
    entries = runner.recorder.as_dicts(limit=1000)
    check("move log recorded something", len(entries) > 0, str(len(entries)))
    check("move log numbering is contiguous",
          [e["n"] for e in entries] == list(range(entries[0]["n"], entries[0]["n"] + len(entries))))

    known = {"setup", "roll", "build", "dev", "trade", "robber", "turn"}
    check("every entry has a known category",
          all(e["category"] in known for e in entries),
          str({e["category"] for e in entries} - known))
    check("every entry names a player",
          all(e["player"] in ("red", "blue", "white", "orange") for e in entries))
    check("every entry has readable text",
          all(e["text"] and not e["text"].isupper() for e in entries),
          str([e["text"] for e in entries if e["text"].isupper()][:3]))

    rolls = [e for e in entries if e["category"] == "roll"]
    check("rolls carry their dice",
          all(e["dice"] and 2 <= sum(e["dice"]) <= 12 for e in rolls),
          str([e for e in rolls if not e["dice"]][:2]))
    check("roll histogram matches the logged rolls",
          sum(runner.recorder.roll_counts) == len(rolls),
          f"{sum(runner.recorder.roll_counts)} vs {len(rolls)}")


def test_payload_is_serialisable(board):
    import json
    try:
        raw = json.dumps(board.model_dump())
        check("payload serialises to JSON", True)
        print(f"{'  payload size':<46}{len(raw) / 1024:.0f} KB")
    except Exception as exc:
        check("payload serialises to JSON", False, str(exc))


def main() -> int:
    print("display geometry and payload\n" + "-" * 58)
    test_rotation_is_self_inverse()
    engine, runner, board = test_geometry()
    test_state_matches_engine(engine, board)
    test_legal_targets_are_real(board)
    test_move_log(runner)
    test_payload_is_serialisable(board)

    print("-" * 58)
    if _failures:
        print(f"{len(_failures)} check(s) FAILED:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("All display checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
