"""
JSettlers-inspired rule-based player.

This follows the spirit of Robert S. Thomas' JSettlers robot (SOCRobotBrain /
SOCRobotDM / SOCBuildingSpeedEstimate / OpeningBuildStrategy) adapted to this
engine.

Key structural insight for this codebase
----------------------------------------
`decide(gs, playable_moves)` is handed the *legal* moves for the current prompt,
and the engine re-calls `decide` on the same player until they PASS. So the robot
is a **per-prompt move ranker** plus a lightweight greedy planner, rather than a
move generator. Each handler below scores the provided moves and returns one.

Handlers, one per prompt:
    SETUP_TURN      -> opening placement (OpeningBuildStrategy)
    PLAY_PRETURN    -> pre-roll dev-card play (mostly: just roll)
    PLAY_TURN       -> greedy build plan (SOCRobotDM.planStuff, ETA-ranked)
    MOVE_ROBBER     -> hurt the leader / steal from the richest
    DISCARD         -> shed least-needed resources
    DECIDE_TRADE    -> respond to an opponent's proposed trade
    DECIDE_ACCEPTEES-> pick among acceptors of our proposal

Simplifications vs. full JSettlers (marked SIMPLIFICATION below): no player-trade
*negotiation* (bank/port trades only), no full per-opponent winGameETA search
(leader tracking only), fast bottleneck BSE rather than the probabilistic DP.
"""

import numpy as np

from players.player import Player
from catan.state import GameState

from catan.actions import (
    # prompts
    SETUP_TURN, PLAY_PRETURN, PLAY_TURN, MOVE_ROBBER, DISCARD,
    DECIDE_TRADE, DECIDE_ACCEPTEES,
    # response types
    ROLL, PLAY_KNIGHT, PASS, BUILD_ROAD, BUILD_SETTLEMENT, BUILD_CITY,
    PURCHASE_DEV_CARD, PORT_TRADE, TABLE_TRADE_ACCEPT, TABLE_TRADE_REJECT,
    DISCARD_RESOURCE, SELECT_ROBBER_RESPONSE,
    # helpers
    get_response, unpack_action, unpack_port_trade,
)

from catan.ids import (
    WOOD, BRICK, SHEEP, WHEAT, ORE, N_RES, N_PLAYERS, NONE_PLAYER,
    DESERT, SETTLEMENT, CITY, COSTS,
    OBJ_ROAD, OBJ_SETTLEMENT, OBJ_CITY, OBJ_DEV,
    SETTLEMENTS_ALLOWED, CITIES_ALLOWED, ROADS_ALLOWED,
    KNIGHT, LARGEST_ARMY_MIN,
)

from catan.interface import (
    get_victory_points, get_total_cards, _get_placeable_settlements,
)


# Dice "pips": ways-to-roll for each face value (expected production weight /36).
#            0  1  2  3  4  5  6  7  8  9 10 11 12
PIPS = np.array([0, 0, 1, 2, 3, 4, 5, 0, 5, 4, 3, 2, 1], dtype=np.float64)

# How much we value holding each resource (used for discards / trade surplus).
# Ore/wheat drive cities & dev cards, so they are the most precious.
RES_VALUE = np.array([2.0, 2.0, 1.0, 3.0, 3.0], dtype=np.float64)  # W B S Wh O

# A road only gets built (when nothing better is available) if it opens access to
# a settlement node at least this good.
MIN_EXPANSION_SCORE = 6.0


class JSettlersPlayer(Player):

    # ------------------------------------------------------------------ #
    #  Entry point                                                        #
    # ------------------------------------------------------------------ #
    def decide(self, gs: GameState, playable_moves: np.ndarray):
        pid = int(gs.current_player_idx)
        prompt = int(gs.prompt)

        try:
            if prompt == SETUP_TURN:
                return self._setup(gs, pid, playable_moves)
            if prompt == PLAY_PRETURN:
                return self._preturn(gs, pid, playable_moves)
            if prompt == PLAY_TURN:
                return self._turn(gs, pid, playable_moves)
            if prompt == MOVE_ROBBER:
                return self._robber(gs, pid, playable_moves)
            if prompt == DISCARD:
                return self._discard(gs, pid, playable_moves)
            if prompt == DECIDE_TRADE:
                return self._decide_trade(gs, pid, playable_moves)
            if prompt == DECIDE_ACCEPTEES:
                return int(playable_moves[0])  # we never propose -> shouldn't occur
        except Exception:
            # Never crash the game loop on a planning bug: fall back to a legal move.
            pass
        return int(playable_moves[0])

    # ================================================================== #
    #  Production / ETA primitives (SOCBuildingSpeedEstimate-lite)        #
    # ================================================================== #
    def _node_production(self, gs: GameState, sid: int, respect_robber: bool) -> np.ndarray:
        """Pips per resource for a single settlement node (settlement multiplier)."""
        topo = gs.topology
        prod = np.zeros(N_RES, dtype=np.float64)
        robber = int(gs.board.robber_hex)
        hex_sett = topo.hex_settlement_ix
        for hid in range(hex_sett.shape[0]):
            if respect_robber and hid == robber:
                continue
            if sid not in hex_sett[hid]:
                continue
            res = int(topo.hex_resource[hid])
            if res == DESERT:
                continue
            prod[res] += PIPS[int(topo.hex_number[hid])]
        return prod

    def _player_production(self, gs: GameState, pid: int, respect_robber: bool = True) -> np.ndarray:
        """Total pips per resource across a player's settlements (x1) and cities (x2)."""
        topo = gs.topology
        board = gs.board
        prod = np.zeros(N_RES, dtype=np.float64)
        robber = int(board.robber_hex)
        for hid in range(topo.hex_settlement_ix.shape[0]):
            if respect_robber and hid == robber:
                continue
            res = int(topo.hex_resource[hid])
            num = int(topo.hex_number[hid])
            if res == DESERT or num == 0:
                continue
            p = PIPS[num]
            for sid in topo.hex_settlement_ix[hid]:
                sid = int(sid)
                if sid < 0 or int(board.settlement_owner[sid]) != pid:
                    continue
                prod[res] += p * (2.0 if int(board.settlement_type[sid]) == CITY else 1.0)
        return prod

    def _eta(self, prod: np.ndarray, hand: np.ndarray, cost: np.ndarray) -> float:
        """Rough rolls-to-afford `cost`: the bottleneck resource dominates.

        rate_r = prod_r / 36 (expected units/roll). A resource we need but do not
        produce is (softly) reachable via trading, so we cap its cost instead of
        returning infinity.
        """
        need = np.maximum(0, cost - hand)
        if need.sum() == 0:
            return 0.0
        worst = 0.0
        for r in range(N_RES):
            if need[r] <= 0:
                continue
            rate = prod[r] / 36.0
            rolls = need[r] / rate if rate > 1e-9 else 8.0 * need[r]  # trade fallback
            worst = max(worst, rolls)
        return worst

    # ================================================================== #
    #  Node scoring (OpeningBuildStrategy)                                #
    # ================================================================== #
    def _touches_port(self, gs: GameState, sid: int) -> bool:
        return bool(np.any(gs.topology.port_settlement_ix == sid))

    def _setup_node_score(self, gs: GameState, pid: int, sid: int) -> float:
        prod = self._node_production(gs, sid, respect_robber=False)
        total = float(prod.sum())
        diversity = float((prod > 0).sum())

        score = total + 2.0 * diversity
        if self._touches_port(gs, sid):
            score += 2.5
        # Slight preference for the city engine (wheat + ore).
        score += 0.25 * (prod[WHEAT] + prod[ORE])
        # Complement what we already produce (reward resources we currently lack).
        existing = self._player_production(gs, pid, respect_robber=False)
        for r in range(N_RES):
            if existing[r] == 0:
                score += 0.5 * prod[r]
        return score

    # ================================================================== #
    #  SETUP_TURN                                                         #
    # ================================================================== #
    def _setup(self, gs: GameState, pid: int, moves: np.ndarray) -> int:
        # moves are act_setup(sid, rid); pick best settlement node, then the road
        # pointing toward the strongest neighbouring expansion node.
        best_sid, best_score = None, -1e9
        by_sid: dict[int, list[int]] = {}
        for a in moves:
            _, sid, _rid = unpack_action(int(a))
            by_sid.setdefault(sid, []).append(int(a))
            if sid == best_sid:
                continue
            s = self._setup_node_score(gs, pid, sid)
            if s > best_score:
                best_score, best_sid = s, sid

        candidates = by_sid[best_sid]
        # Choose the road whose *other* endpoint has the best production.
        r_adj_sett = gs.topology.road_adj_settlement
        best_move, best_dir = candidates[0], -1e9
        for a in candidates:
            _, _sid, rid = unpack_action(a)
            sA, sB = int(r_adj_sett[rid, 0]), int(r_adj_sett[rid, 1])
            other = sB if sA == best_sid else sA
            dir_score = float(self._node_production(gs, other, respect_robber=False).sum()) if other >= 0 else 0.0
            if dir_score > best_dir:
                best_dir, best_move = dir_score, a
        return best_move

    # ================================================================== #
    #  PLAY_PRETURN                                                       #
    # ================================================================== #
    def _preturn(self, gs: GameState, pid: int, moves: np.ndarray) -> int:
        roll_move = None
        knight_move = None
        for a in moves:
            r = get_response(int(a))
            if r == ROLL:
                roll_move = int(a)
            elif r == PLAY_KNIGHT:
                knight_move = int(a)

        # Play a knight before rolling if the robber sits on one of our hexes
        # (move it off), or if it wins us Largest Army.
        if knight_move is not None and self._knight_worth_it(gs, pid):
            return knight_move
        return roll_move if roll_move is not None else int(moves[0])

    def _knight_worth_it(self, gs: GameState, pid: int) -> bool:
        p = gs.players[pid]
        # About to grab (or extend) largest army?
        if int(p.used_knights) + 1 >= LARGEST_ARMY_MIN and int(gs.largest_army_owner) != pid:
            return True
        # Robber parked on a hex we produce from?
        robber = int(gs.board.robber_hex)
        for sid in gs.topology.hex_settlement_ix[robber]:
            if sid >= 0 and int(gs.board.settlement_owner[int(sid)]) == pid:
                return True
        return False

    # ================================================================== #
    #  PLAY_TURN  (SOCRobotDM.planStuff, greedy one-step)                 #
    # ================================================================== #
    def _turn(self, gs: GameState, pid: int, moves: np.ndarray) -> int:
        buckets: dict[int, list[int]] = {}
        for a in moves:
            buckets.setdefault(get_response(int(a)), []).append(int(a))

        pass_move = buckets.get(PASS, [None])[0]

        # 1) Largest-army knight, if it flips the badge this instant.
        for a in buckets.get(PLAY_KNIGHT, []):
            if int(gs.players[pid].used_knights) + 1 >= LARGEST_ARMY_MIN and int(gs.largest_army_owner) != pid:
                return a

        # 2) Upgrade to a city -> the node whose doubled production gains the most.
        cities = buckets.get(BUILD_CITY, [])
        if cities:
            return max(cities, key=lambda a: self._node_production(
                gs, unpack_action(a)[1], respect_robber=True).sum())

        # 3) Build a settlement -> best-scoring reachable node (+1 VP, more prod).
        setts = buckets.get(BUILD_SETTLEMENT, [])
        if setts:
            return max(setts, key=lambda a: self._setup_node_score(gs, pid, unpack_action(a)[1]))

        # 4) Build a road only to open a genuinely good expansion node.
        roads = buckets.get(BUILD_ROAD, [])
        if roads:
            road = self._best_expansion_road(gs, pid, roads)
            if road is not None:
                return road

        # 5) Decide a build target and trade toward it (bank/port only).
        cost, target = self._target(gs, pid)
        trade = self._pick_trade(gs, pid, buckets.get(PORT_TRADE, []), cost)
        if trade is not None:
            return trade

        # 6) Buy a dev card when flush / boxed in and can't do better.
        dev = buckets.get(PURCHASE_DEV_CARD, [])
        if dev and self._want_dev(gs, pid, target):
            return dev[0]

        return pass_move if pass_move is not None else int(moves[0])

    def _best_expansion_road(self, gs: GameState, pid: int, roads: list[int]):
        r_adj_sett = gs.topology.road_adj_settlement
        owner = gs.board.settlement_owner
        best, best_score = None, MIN_EXPANSION_SCORE
        for a in roads:
            _, rid, _ = unpack_action(a)
            for sid in (int(r_adj_sett[rid, 0]), int(r_adj_sett[rid, 1])):
                if sid < 0 or int(owner[sid]) != -1:
                    continue
                s = self._setup_node_score(gs, pid, sid)
                if s > best_score:
                    best_score, best = s, a
        return best

    def _target(self, gs: GameState, pid: int) -> tuple[np.ndarray, str]:
        """Which piece are we saving/trading for right now?"""
        p = gs.players[pid]
        board = gs.board
        own_plain = np.any((board.settlement_owner == pid) & (board.settlement_type == SETTLEMENT))
        if own_plain and int(p.cities_built) < CITIES_ALLOWED:
            return COSTS[OBJ_CITY], "city"
        if int(p.settlements_built) < SETTLEMENTS_ALLOWED and len(_get_placeable_settlements(gs)) > 0:
            return COSTS[OBJ_SETTLEMENT], "settlement"
        return COSTS[OBJ_DEV], "dev"

    def _pick_trade(self, gs: GameState, pid: int, port_trades: list[int], cost: np.ndarray):
        """Convert a genuinely-surplus resource into one we need for `cost`.

        Only trades resources we don't need for the target, so `need` strictly
        shrinks each call -> the turn always terminates (build or pass).
        """
        hand = gs.players[pid].hand
        need = np.maximum(0, cost - hand)
        if need.sum() == 0:
            return None
        best, best_key = None, None
        for a in port_trades:
            give, rate, take = unpack_port_trade(a)
            if need[take] <= 0 or need[give] > 0 or hand[give] < rate:
                continue
            # Prefer relieving the biggest need, spending the deepest surplus.
            key = (need[take], hand[give] - rate)
            if best_key is None or key > best_key:
                best_key, best = key, a
        return best

    def _want_dev(self, gs: GameState, pid: int, target: str) -> bool:
        if target == "dev":  # boxed in: dev cards are the only progress
            return True
        # Otherwise only when flush enough that we'd risk a 7-discard anyway.
        return get_total_cards(gs, pid) >= 7

    # ================================================================== #
    #  MOVE_ROBBER                                                        #
    # ================================================================== #
    def _robber(self, gs: GameState, pid: int, moves: np.ndarray) -> int:
        leader = self._vp_leader(gs, pid)
        topo, board = gs.topology, gs.board

        best, best_score = int(moves[0]), -1e9
        for a in moves:
            _, hid, victim = unpack_action(int(a))
            pips = PIPS[int(topo.hex_number[hid])] if int(topo.hex_resource[hid]) != DESERT else 0.0

            score = 0.0
            for sid in topo.hex_settlement_ix[hid]:
                sid = int(sid)
                if sid < 0:
                    continue
                o = int(board.settlement_owner[sid])
                if o in (-1, pid):
                    continue
                mult = 2.0 if int(board.settlement_type[sid]) == CITY else 1.0
                score += pips * mult
                if o == leader:
                    score += pips * mult  # double-count damage to the leader

            if victim != NONE_PLAYER:
                score += 0.5 * get_total_cards(gs, victim)
                if victim == leader:
                    score += 3.0
            if score > best_score:
                best_score, best = score, int(a)
        return best

    def _vp_leader(self, gs: GameState, pid: int) -> int:
        best_pid, best_vp = -1, -1
        for p in range(N_PLAYERS):
            if p == pid:
                continue
            v = get_victory_points(gs, p)
            if v > best_vp:
                best_vp, best_pid = v, p
        return best_pid

    # ================================================================== #
    #  DISCARD                                                            #
    # ================================================================== #
    def _discard(self, gs: GameState, pid: int, moves: np.ndarray) -> int:
        hand = gs.players[pid].hand
        cost, _ = self._target(gs, pid)
        need = np.maximum(0, cost - hand)
        # Keep-priority: needed-for-target dominates, then intrinsic value.
        keep = need * 10.0 + RES_VALUE
        best, best_key = int(moves[0]), 1e9
        for a in moves:
            _, res, _ = unpack_action(int(a))
            if keep[res] < best_key:
                best_key, best = keep[res], int(a)
        return best

    # ================================================================== #
    #  DECIDE_TRADE  (respond to an opponent's proposal)                  #
    # ================================================================== #
    def _decide_trade(self, gs: GameState, pid: int, moves: np.ndarray) -> int:
        # SIMPLIFICATION: don't help opponents -> reject. (Accepting only strictly
        # beneficial offers is a natural upgrade point.)
        for a in moves:
            if get_response(int(a)) == TABLE_TRADE_REJECT:
                return int(a)
        return int(moves[0])
