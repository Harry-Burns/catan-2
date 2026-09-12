"""Static audit of the board itself, run once per game before any move.

If the island is built wrong, every later check is measuring the wrong thing --
so this runs first: component counts, number tokens, harbours, and the internal
consistency of the adjacency tables the whole engine reads from.
"""

from collections import Counter

from catan.ids import (
    DESERT, EMPTY, NONE_PLAYER, COSTS as ENGINE_COSTS,
    ROADS_ALLOWED, SETTLEMENTS_ALLOWED, CITIES_ALLOWED,
    VICTORY_POINTS_REQUIRED, BANK_STOCK, N_RES, N_PLAYERS,
    KNIGHT, YEAR_OF_PLENTY, MONOPOLY, ROAD_BUILDER, VICTORY_POINT,
)

from verification import rules as R
from verification.report import Severity


ENGINE_COST_ROWS = {
    "road": (0, R.COST_ROAD),
    "settlement": (1, R.COST_SETTLEMENT),
    "city": (2, R.COST_CITY),
    "development card": (3, R.COST_DEV_CARD),
}

DEV_COMPOSITION = {
    KNIGHT: R.DEV_KNIGHT,
    YEAR_OF_PLENTY: R.DEV_YEAR_OF_PLENTY,
    MONOPOLY: R.DEV_MONOPOLY,
    ROAD_BUILDER: R.DEV_ROAD_BUILDING,
    VICTORY_POINT: R.DEV_VICTORY_POINT,
}


class BoardAuditor:
    def __init__(self, emit):
        self.emit = emit
        self.checks = 0

    def _check(self, ok, code, severity, message, detail=None):
        self.checks += 1
        if not ok:
            self.emit(code, severity, message, detail)

    def run(self, gs) -> None:
        topo = gs.topology
        self.engine_constants()
        self.terrain(topo)
        self.number_tokens(topo)
        self.harbours(topo)
        self.geometry(topo)
        self.opening_state(gs)

    # --- the engine's own constants vs the rulebook --------------------- #
    def engine_constants(self):
        self._check(N_RES == R.N_RESOURCES, "CONST_RESOURCES", Severity.RULE,
                    f"engine has {N_RES} resources, Catan has {R.N_RESOURCES}")
        self._check(N_PLAYERS == R.N_PLAYERS, "CONST_PLAYERS", Severity.NOTE,
                    f"engine is configured for {N_PLAYERS} players")
        self._check(ROADS_ALLOWED == R.MAX_ROADS, "CONST_ROADS", Severity.RULE,
                    f"engine allows {ROADS_ALLOWED} roads, the rules give {R.MAX_ROADS}")
        self._check(SETTLEMENTS_ALLOWED == R.MAX_SETTLEMENTS, "CONST_SETTLEMENTS",
                    Severity.RULE,
                    f"engine allows {SETTLEMENTS_ALLOWED} settlements, the rules "
                    f"give {R.MAX_SETTLEMENTS}")
        self._check(CITIES_ALLOWED == R.MAX_CITIES, "CONST_CITIES", Severity.RULE,
                    f"engine allows {CITIES_ALLOWED} cities, the rules give {R.MAX_CITIES}")
        self._check(VICTORY_POINTS_REQUIRED == R.VICTORY_POINTS_TO_WIN, "CONST_VP",
                    Severity.RULE,
                    f"engine wins at {VICTORY_POINTS_REQUIRED} points, the rules say "
                    f"{R.VICTORY_POINTS_TO_WIN}")
        self._check(BANK_STOCK == R.RESOURCE_CARDS_PER_TYPE, "CONST_BANK", Severity.RULE,
                    f"engine stocks {BANK_STOCK} of each resource, the rules give "
                    f"{R.RESOURCE_CARDS_PER_TYPE}")

        for label, (row, expected) in ENGINE_COST_ROWS.items():
            actual = tuple(int(x) for x in ENGINE_COSTS[row])
            self._check(actual == expected, "CONST_COST", Severity.RULE,
                        f"{label} costs {actual} in the engine, the rules say {expected}",
                        "Order is (wood, brick, sheep, wheat, ore).")

    # --- terrain -------------------------------------------------------- #
    def terrain(self, topo):
        resources = list(topo.py_hex_resource)
        self._check(len(resources) == R.N_HEXES, "HEX_COUNT", Severity.RULE,
                    f"board has {len(resources)} hexes, Catan has {R.N_HEXES}")

        counts = Counter(resources)
        for res, expected in R.TERRAIN_COUNTS.items():
            self._check(counts.get(res, 0) == expected, "TERRAIN_MIX", Severity.RULE,
                        f"{expected} {R.RESOURCE_NAMES[res]} hexes expected, "
                        f"board has {counts.get(res, 0)}")
        self._check(counts.get(DESERT, 0) == R.N_DESERT_HEXES, "TERRAIN_MIX",
                    Severity.RULE,
                    f"{R.N_DESERT_HEXES} desert expected, board has {counts.get(DESERT, 0)}")

    def number_tokens(self, topo):
        numbers = list(topo.py_hex_number)
        resources = list(topo.py_hex_resource)

        tokens = [n for n in numbers if n != 0]
        self._check(len(tokens) == R.N_NUMBER_TOKENS, "TOKEN_COUNT", Severity.RULE,
                    f"{len(tokens)} number tokens on the board, Catan has "
                    f"{R.N_NUMBER_TOKENS}")

        counts = Counter(tokens)
        self._check(counts == Counter(R.NUMBER_TOKEN_COUNTS), "TOKEN_MIX", Severity.RULE,
                    "number tokens do not match the standard set",
                    f"board={dict(sorted(counts.items()))}\n"
                    f"rules={dict(sorted(R.NUMBER_TOKEN_COUNTS.items()))}")
        self._check(R.NO_TOKEN not in counts, "TOKEN_SEVEN", Severity.RULE,
                    "a hex is marked 7; there is no 7 token")

        for hid, (res, num) in enumerate(zip(resources, numbers)):
            if res == DESERT:
                self._check(num == 0, "DESERT_HAS_TOKEN", Severity.RULE,
                            f"desert hex {hid} carries the number {num}")
            else:
                self._check(num != 0, "HEX_MISSING_TOKEN", Severity.RULE,
                            f"hex {hid} ({R.RESOURCE_NAMES[res]}) has no number token")
                self._check(R.DICE_MIN <= num <= R.DICE_MAX, "TOKEN_OUT_OF_RANGE",
                            Severity.RULE, f"hex {hid} carries the number {num}")

    def harbours(self, topo):
        port_res = [int(x) for x in topo.port_res_id]
        self._check(len(port_res) == R.N_HARBOURS, "HARBOUR_COUNT", Severity.RULE,
                    f"board has {len(port_res)} harbours, Catan has {R.N_HARBOURS}")

        generic = sum(1 for r in port_res if r == -1)
        specific = [r for r in port_res if r != -1]
        self._check(generic == R.N_GENERIC_HARBOURS, "HARBOUR_MIX", Severity.RULE,
                    f"{generic} generic 3:1 harbours, Catan has {R.N_GENERIC_HARBOURS}")
        self._check(sorted(specific) == list(range(R.N_RESOURCES)), "HARBOUR_MIX",
                    Severity.RULE,
                    f"specific harbours are {sorted(specific)}, Catan has exactly one "
                    f"per resource",
                    "Almanac, 'Maritime Trade': there is only 1 special harbor for "
                    "each type of resource.")

        for pid, nodes in enumerate(topo.port_settlement_ix):
            real = [int(n) for n in nodes if int(n) >= 0]
            self._check(len(real) == 2, "HARBOUR_NODES", Severity.RULE,
                        f"harbour {pid} touches {len(real)} intersections, expected 2")

    # --- adjacency tables ----------------------------------------------- #
    def geometry(self, topo):
        n_sett = len(topo.py_sett_adj_roads)
        n_road = len(topo.py_road_adj_sett)
        self._check(n_sett == R.N_INTERSECTIONS, "INTERSECTION_COUNT", Severity.RULE,
                    f"board has {n_sett} intersections, the standard board has "
                    f"{R.N_INTERSECTIONS}")
        self._check(n_road == R.N_PATHS, "PATH_COUNT", Severity.RULE,
                    f"board has {n_road} paths, the standard board has {R.N_PATHS}")

        # every path joins exactly two intersections
        for rid, ends in enumerate(topo.py_road_adj_sett):
            real = [s for s in ends if s >= 0]
            self._check(len(real) == 2 and real[0] != real[1], "PATH_ENDPOINTS",
                        Severity.CORRUPTION,
                        f"path {rid} joins intersections {list(ends)}")

        # settlement -> road and road -> settlement must agree
        for sid, roads in enumerate(topo.py_sett_adj_roads):
            self._check(2 <= len(roads) <= 3, "INTERSECTION_DEGREE", Severity.CORRUPTION,
                        f"intersection {sid} touches {len(roads)} paths, expected 2 or 3")
            for rid in roads:
                self._check(sid in topo.py_road_adj_sett[rid], "ADJACENCY_ASYMMETRIC",
                            Severity.CORRUPTION,
                            f"intersection {sid} lists path {rid}, but that path joins "
                            f"{list(topo.py_road_adj_sett[rid])}")
        for rid, ends in enumerate(topo.py_road_adj_sett):
            for sid in ends:
                if sid < 0:
                    continue
                self._check(rid in topo.py_sett_adj_roads[sid], "ADJACENCY_ASYMMETRIC",
                            Severity.CORRUPTION,
                            f"path {rid} joins intersection {sid}, which does not list it")

        # intersection neighbours must be symmetric, and match the path degree
        for sid, neighbours in enumerate(topo.py_sett_adj_sett):
            self._check(len(neighbours) == len(set(neighbours)), "NEIGHBOUR_DUPLICATE",
                        Severity.CORRUPTION,
                        f"intersection {sid} lists a neighbour twice: {list(neighbours)}")
            self._check(sid not in neighbours, "NEIGHBOUR_SELF", Severity.CORRUPTION,
                        f"intersection {sid} is its own neighbour")
            self._check(
                len(neighbours) == len(topo.py_sett_adj_roads[sid]),
                "DEGREE_MISMATCH", Severity.CORRUPTION,
                f"intersection {sid} has {len(neighbours)} neighbours but "
                f"{len(topo.py_sett_adj_roads[sid])} paths",
            )
            for other in neighbours:
                self._check(sid in topo.py_sett_adj_sett[other], "ADJACENCY_ASYMMETRIC",
                            Severity.CORRUPTION,
                            f"intersection {sid} lists {other} as a neighbour, but not "
                            f"the other way round")

        # every hex is a hexagon
        for hid, nodes in enumerate(topo.py_hex_settlement):
            self._check(len(nodes) == 6, "HEX_CORNERS", Severity.CORRUPTION,
                        f"hex {hid} has {len(nodes)} corners")
            self._check(len(set(nodes)) == len(nodes), "HEX_CORNER_DUPLICATE",
                        Severity.CORRUPTION, f"hex {hid} lists a corner twice")

        # each intersection belongs to between one and three hexes
        touching = Counter()
        for nodes in topo.py_hex_settlement:
            for sid in nodes:
                touching[sid] += 1
        for sid in range(n_sett):
            count = touching.get(sid, 0)
            self._check(1 <= count <= 3, "INTERSECTION_HEXES", Severity.CORRUPTION,
                        f"intersection {sid} borders {count} hexes, expected 1 to 3")

    # --- opening position ----------------------------------------------- #
    def opening_state(self, gs):
        board = gs.board
        for res in range(R.N_RESOURCES):
            self._check(int(board.bank_res[res]) == R.RESOURCE_CARDS_PER_TYPE,
                        "OPENING_BANK", Severity.RULE,
                        f"bank opens with {int(board.bank_res[res])} "
                        f"{R.RESOURCE_NAMES[res]}, expected {R.RESOURCE_CARDS_PER_TYPE}")

        deck = board.dev_deck.tolist()
        self._check(len(deck) == R.TOTAL_DEV_CARDS, "OPENING_DEV_DECK", Severity.RULE,
                    f"development deck has {len(deck)} cards, Catan has "
                    f"{R.TOTAL_DEV_CARDS}")
        counts = Counter(deck)
        for dev_type, expected in DEV_COMPOSITION.items():
            self._check(counts.get(dev_type, 0) == expected, "OPENING_DEV_MIX",
                        Severity.RULE,
                        f"development deck holds {counts.get(dev_type, 0)} of type "
                        f"{dev_type}, expected {expected}")

        desert = [hid for hid, res in enumerate(gs.topology.py_hex_resource)
                  if res == DESERT]
        self._check(int(board.robber_hex) in desert, "ROBBER_START", Severity.RULE,
                    f"robber starts on hex {int(board.robber_hex)}, the desert is "
                    f"{desert}",
                    "Almanac, 'Set-up Phase': place the robber in the desert.")

        self._check(all(o == -1 for o in board.road_owner.tolist())
                    and all(o == -1 for o in board.settlement_owner.tolist())
                    and all(t == EMPTY for t in board.settlement_type.tolist()),
                    "OPENING_BOARD_NOT_EMPTY", Severity.CORRUPTION,
                    "the board has pieces on it before setup begins")

        for pid, player in enumerate(gs.players):
            self._check(sum(player.hand.tolist()) == 0, "OPENING_HAND", Severity.RULE,
                        f"player {pid} starts holding {player.hand.tolist()}")
            self._check(not any(player.dev_cards.tolist())
                        and not any(player.new_dev_cards.tolist()),
                        "OPENING_DEV_CARDS", Severity.RULE,
                        f"player {pid} starts with development cards")
            self._check(int(player.ports_mask) == 0, "OPENING_PORTS", Severity.CORRUPTION,
                        f"player {pid} starts owning harbours")

        self._check(int(gs.longest_road_owner) == NONE_PLAYER
                    and int(gs.largest_army_owner) == NONE_PLAYER,
                    "OPENING_SPECIAL_CARDS", Severity.RULE,
                    "a special card is already awarded before the game starts")
        self._check(int(gs.winner) == NONE_PLAYER, "OPENING_WINNER", Severity.CORRUPTION,
                    "the game opens with a winner")
