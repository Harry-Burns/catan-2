"""Properties that must hold of *any* legal Catan position.

Checked after every single action. Nothing here looks at how the state was
reached -- if one of these fails, the position on the table is impossible.
"""

from catan.ids import (
    DESERT, EMPTY, SETTLEMENT, CITY, NONE_PLAYER,
    KNIGHT, YEAR_OF_PLENTY, MONOPOLY, ROAD_BUILDER, VICTORY_POINT,
)
from catan.actions import (
    SETUP_TURN, PLAY_PRETURN, PLAY_TURN, MOVE_ROBBER, DISCARD,
    DECIDE_TRADE, DECIDE_ACCEPTEES, PROMPT2STR,
)

from verification import rules as R
from verification import reference as ref
from verification.report import Severity, Violation


DEV_DECK_COMPOSITION = {
    KNIGHT: R.DEV_KNIGHT,
    YEAR_OF_PLENTY: R.DEV_YEAR_OF_PLENTY,
    MONOPOLY: R.DEV_MONOPOLY,
    ROAD_BUILDER: R.DEV_ROAD_BUILDING,
    VICTORY_POINT: R.DEV_VICTORY_POINT,
}

TRADE_PROMPTS = (DECIDE_TRADE, DECIDE_ACCEPTEES)
ALL_PROMPTS = (SETUP_TURN, PLAY_PRETURN, PLAY_TURN, MOVE_ROBBER, DISCARD,
               DECIDE_TRADE, DECIDE_ACCEPTEES)


class InvariantChecker:
    """Runs every state invariant. `emit` takes (code, severity, message, detail)."""

    def __init__(self, topo, n_players: int, emit):
        self.topo = topo
        self.n_players = n_players
        self.emit = emit
        self.checks = 0

    def _check(self, ok: bool, code: str, severity: Severity, message: str, detail=None):
        self.checks += 1
        if not ok:
            self.emit(code, severity, message, detail)

    # ------------------------------------------------------------------ #
    def run(self, snap: ref.Snapshot, played_progress: dict[int, int], gs=None) -> None:
        self._conservation(snap, played_progress)
        self._card_bounds(snap)
        self._board_vs_counters(snap)
        self._piece_limits(snap)
        self._placement_rules(snap)
        self._network(snap)
        self._special_cards(snap, gs)
        self._harbours(snap)
        self._victory(snap)
        self._phase(snap)

    # --- conservation --------------------------------------------------- #
    def _conservation(self, snap, played_progress):
        for res in range(R.N_RESOURCES):
            total = snap.bank[res] + sum(snap.hands[p][res] for p in range(self.n_players))
            self._check(
                total == R.RESOURCE_CARDS_PER_TYPE,
                "RESOURCE_CONSERVATION", Severity.CORRUPTION,
                f"{R.RESOURCE_NAMES[res]} cards in play = {total}, must always be "
                f"{R.RESOURCE_CARDS_PER_TYPE}",
                f"bank={snap.bank[res]} hands={[snap.hands[p][res] for p in range(self.n_players)]}",
            )

        for dev_type, expected in DEV_DECK_COMPOSITION.items():
            in_deck = snap.dev_deck.count(dev_type)
            held = sum(snap.dev_cards[p][dev_type] + snap.new_dev_cards[p][dev_type]
                       for p in range(self.n_players))
            if dev_type == KNIGHT:
                spent = sum(snap.used_knights)
            else:
                spent = played_progress.get(dev_type, 0)
            total = in_deck + held + spent
            self._check(
                total == expected,
                "DEV_CARD_CONSERVATION", Severity.CORRUPTION,
                f"dev card type {dev_type} accounted {total} times, deck holds {expected}",
                f"in_deck={in_deck} held={held} played={spent}",
            )

        self._check(
            len(snap.dev_deck) <= R.TOTAL_DEV_CARDS,
            "DEV_DECK_TOO_BIG", Severity.CORRUPTION,
            f"dev deck has {len(snap.dev_deck)} cards, max {R.TOTAL_DEV_CARDS}",
        )

    def _card_bounds(self, snap):
        for res in range(R.N_RESOURCES):
            self._check(
                snap.bank[res] >= 0,
                "BANK_NEGATIVE", Severity.CORRUPTION,
                f"bank holds {snap.bank[res]} {R.RESOURCE_NAMES[res]}",
            )
            self._check(
                snap.bank[res] <= R.RESOURCE_CARDS_PER_TYPE,
                "BANK_OVERFULL", Severity.CORRUPTION,
                f"bank holds {snap.bank[res]} {R.RESOURCE_NAMES[res]}, "
                f"more than the {R.RESOURCE_CARDS_PER_TYPE} that exist",
            )
        for p in range(self.n_players):
            for res in range(R.N_RESOURCES):
                self._check(
                    snap.hands[p][res] >= 0,
                    "HAND_NEGATIVE", Severity.CORRUPTION,
                    f"player {p} holds {snap.hands[p][res]} {R.RESOURCE_NAMES[res]}",
                )
            for dev_type in DEV_DECK_COMPOSITION:
                self._check(
                    snap.dev_cards[p][dev_type] >= 0 and snap.new_dev_cards[p][dev_type] >= 0,
                    "DEV_CARDS_NEGATIVE", Severity.CORRUPTION,
                    f"player {p} has a negative count of dev type {dev_type}",
                )

    # --- board vs the engine's own counters ----------------------------- #
    def _board_vs_counters(self, snap):
        for p in range(self.n_players):
            roads = sum(1 for o in snap.road_owner if o == p)
            setts = sum(1 for sid, o in enumerate(snap.settlement_owner)
                        if o == p and snap.settlement_type[sid] == SETTLEMENT)
            cities = sum(1 for sid, o in enumerate(snap.settlement_owner)
                         if o == p and snap.settlement_type[sid] == CITY)
            self._check(roads == snap.roads_built[p], "ROAD_COUNT_MISMATCH", Severity.CORRUPTION,
                        f"player {p}: {roads} roads on the board, counter says {snap.roads_built[p]}")
            self._check(setts == snap.settlements_built[p], "SETTLEMENT_COUNT_MISMATCH",
                        Severity.CORRUPTION,
                        f"player {p}: {setts} settlements on the board, counter says "
                        f"{snap.settlements_built[p]}")
            self._check(cities == snap.cities_built[p], "CITY_COUNT_MISMATCH", Severity.CORRUPTION,
                        f"player {p}: {cities} cities on the board, counter says "
                        f"{snap.cities_built[p]}")

        for sid, owner in enumerate(snap.settlement_owner):
            stype = snap.settlement_type[sid]
            self._check(
                (owner == -1) == (stype == EMPTY),
                "BUILDING_INCOHERENT", Severity.CORRUPTION,
                f"intersection {sid}: owner={owner} but type={stype}",
            )
            self._check(
                owner == -1 or 0 <= owner < self.n_players,
                "BUILDING_BAD_OWNER", Severity.CORRUPTION,
                f"intersection {sid} owned by {owner}",
            )
            self._check(
                stype in (EMPTY, SETTLEMENT, CITY),
                "BUILDING_BAD_TYPE", Severity.CORRUPTION,
                f"intersection {sid} has type {stype}",
            )
        for rid, owner in enumerate(snap.road_owner):
            self._check(
                owner == -1 or 0 <= owner < self.n_players,
                "ROAD_BAD_OWNER", Severity.CORRUPTION,
                f"path {rid} owned by {owner}",
            )

    def _piece_limits(self, snap):
        for p in range(self.n_players):
            self._check(snap.roads_built[p] <= R.MAX_ROADS, "TOO_MANY_ROADS", Severity.RULE,
                        f"player {p} has {snap.roads_built[p]} roads, only "
                        f"{R.MAX_ROADS} pieces exist")
            self._check(snap.settlements_built[p] <= R.MAX_SETTLEMENTS, "TOO_MANY_SETTLEMENTS",
                        Severity.RULE,
                        f"player {p} has {snap.settlements_built[p]} settlements standing, "
                        f"only {R.MAX_SETTLEMENTS} pieces exist")
            self._check(snap.cities_built[p] <= R.MAX_CITIES, "TOO_MANY_CITIES", Severity.RULE,
                        f"player {p} has {snap.cities_built[p]} cities, only "
                        f"{R.MAX_CITIES} pieces exist")

    # --- placement ------------------------------------------------------ #
    def _placement_rules(self, snap):
        for sid, owner in enumerate(snap.settlement_owner):
            if owner == -1:
                continue
            neighbours = ref.adjacent_occupied(self.topo, snap, sid)
            self._check(
                not neighbours,
                "DISTANCE_RULE", Severity.RULE,
                f"intersection {sid} (player {owner}) is adjacent to occupied "
                f"intersection(s) {neighbours}",
                "Rulebook p.5: a settlement may only be built where all 3 adjacent "
                "intersections are vacant.",
            )

        if not snap.in_setup:
            for sid, owner in enumerate(snap.settlement_owner):
                if owner == -1:
                    continue
                touching = [r for r in self.topo.py_sett_adj_roads[sid]
                            if snap.road_owner[r] == owner]
                self._check(
                    bool(touching),
                    "SETTLEMENT_UNCONNECTED", Severity.RULE,
                    f"player {owner}'s building at {sid} touches none of their roads",
                    "Rulebook p.5: each of your settlements must connect to at least "
                    "1 of your own roads.",
                )

        self._check(
            0 <= snap.robber_hex < R.N_HEXES,
            "ROBBER_OFF_BOARD", Severity.CORRUPTION,
            f"robber is on hex {snap.robber_hex}",
        )

    def _network(self, snap):
        for p in range(self.n_players):
            # Physical adjacency on purpose. An opponent building on a junction
            # cuts your network for road-length and for extending it, but the
            # segments still touch, so the count cannot grow that way.
            components = ref.network_components(self.topo, snap, p)
            self._check(
                len(components) <= R.SETUP_ROUNDS,
                "ROAD_NETWORK_FRAGMENTED", Severity.RULE,
                f"player {p}'s roads form {len(components)} groups that do not touch; "
                f"setup lays down at most {R.SETUP_ROUNDS} and every later road must "
                f"attach to one",
                f"groups={[sorted(c) for c in components]}",
            )

    # --- special cards -------------------------------------------------- #
    def _special_cards(self, snap, gs=None):
        lengths = ref.all_longest_roads(self.topo, snap, self.n_players)
        allowed = ref.acceptable_longest_road_owners(lengths)
        self._check(
            snap.longest_road_owner in allowed,
            "LONGEST_ROAD_OWNER", Severity.RULE,
            f"Longest Road held by {snap.longest_road_owner}, but road lengths are "
            f"{lengths} so it must be one of {sorted(allowed)}",
            "Rulebook p.4 / Almanac 'Longest Road': the card goes to the single "
            "longest road of 5+ segments; on a tie the incumbent keeps it, "
            "otherwise it is set aside.",
        )

        # Two separate things can go wrong here, and they want different fixes:
        # the engine's road-length routine can be wrong, or it can be right but
        # not have been re-run for this player. Tell them apart.
        engine_lengths = None
        if gs is not None:
            from catan.interface import _calculate_longest_road
            engine_lengths = [int(_calculate_longest_road(gs, p))
                              for p in range(self.n_players)]
            for p in range(self.n_players):
                self._check(
                    engine_lengths[p] == lengths[p],
                    "LONGEST_ROAD_ALGORITHM", Severity.RULE,
                    f"the engine measures player {p}'s longest road as "
                    f"{engine_lengths[p]}, the board gives {lengths[p]}",
                    self._road_detail(snap, p),
                )

        for p in range(self.n_players):
            reference_point = (engine_lengths[p] if engine_lengths is not None
                               else lengths[p])
            self._check(
                snap.longest_road_len[p] == reference_point,
                "LONGEST_ROAD_LENGTH_STALE", Severity.NOTE,
                f"player {p}'s stored longest road is {snap.longest_road_len[p]}, "
                f"recomputing gives {reference_point}",
                "The engine only re-measures the player who just moved, so this "
                "value can lag. It feeds the award comparison, so when it lags it "
                "shows up as LONGEST_ROAD_OWNER.",
            )

    def _road_detail(self, snap, pid: int) -> str:
        roads = [r for r, o in enumerate(snap.road_owner) if o == pid]
        lines = [f"player {pid} roads: {roads}"]
        for r in roads:
            a, b = self.topo.py_road_adj_sett[r]
            lines.append(f"  path {r}: {a} (owner {snap.settlement_owner[a]}) -- "
                         f"{b} (owner {snap.settlement_owner[b]})")
        lines.append("An opponent's building breaks a road, but the segments either "
                     "side of it still count; a run may start or end at one.")
        return "\n".join(lines)

        knights = list(snap.used_knights)
        allowed_army = ref.acceptable_largest_army_owners(knights)
        self._check(
            snap.largest_army_owner in allowed_army,
            "LARGEST_ARMY_OWNER", Severity.RULE,
            f"Largest Army held by {snap.largest_army_owner}, but knight counts are "
            f"{knights} so it must be one of {sorted(allowed_army)}",
            "Rulebook p.5: 3+ knights, and another player must play strictly more "
            "to take the card.",
        )
        self._check(
            sum(knights) <= R.DEV_KNIGHT,
            "TOO_MANY_KNIGHTS_PLAYED", Severity.CORRUPTION,
            f"{sum(knights)} knights played, only {R.DEV_KNIGHT} exist",
        )

    def _harbours(self, snap):
        for p in range(self.n_players):
            expected = ref.port_mask_from_board(self.topo, snap, p)
            self._check(
                snap.ports_mask[p] == expected,
                "HARBOUR_MASK_WRONG", Severity.RULE,
                f"player {p} harbour mask {snap.ports_mask[p]:#07b}, board gives "
                f"{expected:#07b}",
                "Rulebook p.8: you control a harbour only by having a building on "
                "one of its two intersections.",
            )

    # --- victory -------------------------------------------------------- #
    def _victory(self, snap):
        points = [ref.victory_points(self.topo, snap, p, VICTORY_POINT)
                  for p in range(self.n_players)]

        if snap.winner != NONE_PLAYER:
            self._check(
                0 <= snap.winner < self.n_players,
                "WINNER_INVALID", Severity.CORRUPTION,
                f"winner recorded as {snap.winner}",
            )
            if 0 <= snap.winner < self.n_players:
                self._check(
                    points[snap.winner] >= R.VICTORY_POINTS_TO_WIN,
                    "WINNER_WITHOUT_POINTS", Severity.RULE,
                    f"player {snap.winner} declared winner with {points[snap.winner]} "
                    f"victory points, {R.VICTORY_POINTS_TO_WIN} are needed",
                )
        else:
            # "If you have 10 or more victory points during your turn, the game
            #  ends and you are the winner."
            active = snap.current_player_turn_idx
            if 0 <= active < self.n_players and not snap.in_setup:
                self._check(
                    points[active] < R.VICTORY_POINTS_TO_WIN,
                    "MISSED_WIN", Severity.RULE,
                    f"player {active} has {points[active]} victory points on their own "
                    f"turn but the game has not ended",
                    "Rulebook p.5: if you have 10 or more victory points during your "
                    "turn, the game ends and you are the winner. Points gained on "
                    "another player's turn (a Longest Road or Largest Army swing) "
                    "must still be noticed when your turn comes round.",
                )

        for p in range(self.n_players):
            self._check(
                points[p] >= 0,
                "NEGATIVE_VICTORY_POINTS", Severity.CORRUPTION,
                f"player {p} has {points[p]} victory points",
            )

    # --- phase / prompt coherence --------------------------------------- #
    def _phase(self, snap):
        self._check(
            snap.prompt in ALL_PROMPTS,
            "PROMPT_INVALID", Severity.CORRUPTION,
            f"prompt = {snap.prompt}",
        )
        self._check(
            0 <= snap.current_player_idx < self.n_players,
            "CURRENT_PLAYER_INVALID", Severity.CORRUPTION,
            f"current_player_idx = {snap.current_player_idx}",
        )
        self._check(
            0 <= snap.current_player_turn_idx < self.n_players,
            "TURN_PLAYER_INVALID", Severity.CORRUPTION,
            f"current_player_turn_idx = {snap.current_player_turn_idx}",
        )

        pending = [p for p in range(self.n_players) if snap.discards_required[p] > 0]
        self._check(
            (snap.prompt == DISCARD) == bool(pending),
            "DISCARD_STATE_INCOHERENT", Severity.CORRUPTION,
            f"prompt={PROMPT2STR[snap.prompt] if snap.prompt in ALL_PROMPTS else snap.prompt} "
            f"but players owing discards = {pending}",
        )
        for p in pending:
            self._check(
                snap.discards_required[p] <= snap.total_cards(p),
                "DISCARD_EXCEEDS_HAND", Severity.RULE,
                f"player {p} owes {snap.discards_required[p]} discards but holds "
                f"{snap.total_cards(p)} cards",
            )

        if snap.prompt == PLAY_TURN:
            self._check(
                snap.has_rolled,
                "TURN_WITHOUT_ROLL", Severity.RULE,
                f"player {snap.current_player_turn_idx} is in the build/trade phase "
                f"without having rolled this turn",
                "Rulebook p.4: 'You must roll for resource production' -- it is the "
                "first step of every turn, and the roll applies to all players.",
            )

        if snap.prompt in (PLAY_PRETURN, PLAY_TURN, MOVE_ROBBER):
            self._check(
                snap.current_player_idx == snap.current_player_turn_idx,
                "ACTING_PLAYER_WRONG", Severity.CORRUPTION,
                f"prompt {PROMPT2STR[snap.prompt]} but the acting player "
                f"({snap.current_player_idx}) is not the turn holder "
                f"({snap.current_player_turn_idx})",
            )

        in_trade = snap.prompt in TRADE_PROMPTS
        self._check(
            in_trade == (snap.trade_offer_from != -1),
            "TRADE_STATE_INCOHERENT", Severity.CORRUPTION,
            f"prompt={PROMPT2STR[snap.prompt] if snap.prompt in ALL_PROMPTS else snap.prompt} "
            f"but trade_offer_from={snap.trade_offer_from}",
        )
        if in_trade:
            self._check(
                snap.trade_offer_from == snap.current_player_turn_idx,
                "TRADE_NOT_FROM_TURN_HOLDER", Severity.RULE,
                f"trade offered by player {snap.trade_offer_from} on player "
                f"{snap.current_player_turn_idx}'s turn",
                "Rulebook p.4: players may only trade with the player whose turn it is.",
            )

        for p in range(self.n_players):
            if p == snap.current_player_turn_idx:
                continue
            self._check(
                not any(snap.new_dev_cards[p]),
                "UNRELEASED_DEV_CARDS", Severity.CORRUPTION,
                f"player {p} still holds unreleased dev cards "
                f"{snap.new_dev_cards[p]} on player {snap.current_player_turn_idx}'s turn",
            )

        self._check(
            snap.in_setup == (snap.setup_turn_idx < R.SETUP_PLACEMENTS - 1
                              or snap.prompt == SETUP_TURN),
            "SETUP_STATE_INCOHERENT", Severity.CORRUPTION,
            f"in_setup={snap.in_setup} setup_turn_idx={snap.setup_turn_idx} "
            f"prompt={PROMPT2STR[snap.prompt] if snap.prompt in ALL_PROMPTS else snap.prompt}",
        )
        if snap.in_setup:
            total_placed = sum(snap.settlements_built) + sum(snap.cities_built)
            self._check(
                total_placed <= R.SETUP_PLACEMENTS,
                "SETUP_OVERBUILD", Severity.RULE,
                f"{total_placed} settlements placed during setup, the phase allows "
                f"{R.SETUP_PLACEMENTS}",
            )
