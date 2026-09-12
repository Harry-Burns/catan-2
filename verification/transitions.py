"""Per-action checks: given the state before and after, was that action applied
exactly as the rules say, and did nothing else move?

The "nothing else moved" half matters as much as the arithmetic. A build that
charges the right resources but also silently clears a trade offer is still a
bug, and only a whole-state diff catches it.
"""

from catan.ids import (
    EMPTY, SETTLEMENT, CITY, NONE_PLAYER,
    KNIGHT, YEAR_OF_PLENTY, MONOPOLY, ROAD_BUILDER, VICTORY_POINT,
)
from catan.actions import (
    SETUP_RESPONSE, ROLL, PLAY_KNIGHT, PLAY_MONOPOLY, PLAY_YEAR_OF_PLENTY,
    PLAY_ROAD_BUILDER, PASS, BUILD_ROAD, BUILD_SETTLEMENT, BUILD_CITY,
    PURCHASE_DEV_CARD, PORT_TRADE, TABLE_TRADE_PROPOSE, TABLE_TRADE_SELECT,
    TABLE_TRADE_ACCEPT, TABLE_TRADE_REJECT, DISCARD_RESOURCE,
    SELECT_ROBBER_RESPONSE, RESPONSE2STR,
    MOVE_ROBBER, DISCARD, PLAY_TURN,
    unpack_action, unpack_port_trade, unpack_table_trade,
)

from verification import rules as R
from verification import reference as ref
from verification.report import Severity


# Fields the engine may legitimately touch on any action: turn bookkeeping and
# the derived award/victory state.
ALWAYS_ALLOWED = frozenset({
    "prompt", "action_log_len", "current_player_idx",
    "longest_road_owner", "largest_army_owner", "longest_road_len", "winner",
})

SCALAR_FIELDS = (
    "robber_hex", "current_player_idx", "current_player_turn_idx", "in_setup",
    "setup_turn_idx", "trade_offer_from", "longest_road_owner",
    "largest_army_owner", "has_rolled", "dev_card_used", "winner",
    "turn_index", "prompt", "action_log_len",
)
LIST_FIELDS = (
    "road_owner", "settlement_owner", "settlement_type", "dev_deck", "bank",
    "hands", "ports_mask", "dev_cards", "new_dev_cards", "used_knights",
    "longest_road_len", "discards_required", "roads_built",
    "settlements_built", "cities_built", "trade_offer_give",
    "trade_offer_take", "trade_accept_mask",
)

COSTS = {
    BUILD_ROAD: R.COST_ROAD,
    BUILD_SETTLEMENT: R.COST_SETTLEMENT,
    BUILD_CITY: R.COST_CITY,
    PURCHASE_DEV_CARD: R.COST_DEV_CARD,
}

PROGRESS_CARDS = {
    PLAY_MONOPOLY: MONOPOLY,
    PLAY_YEAR_OF_PLENTY: YEAR_OF_PLENTY,
    PLAY_ROAD_BUILDER: ROAD_BUILDER,
}


def changed_fields(before: ref.Snapshot, after: ref.Snapshot) -> set[str]:
    out = set()
    for name in SCALAR_FIELDS + LIST_FIELDS:
        if getattr(before, name) != getattr(after, name):
            out.add(name)
    return out


class TransitionChecker:
    def __init__(self, topo, n_players: int, emit):
        self.topo = topo
        self.n_players = n_players
        self.emit = emit
        self.checks = 0
        self.played_progress: dict[int, int] = {}
        # Only rolls whose payout identifies them uniquely; a biased
        # sample, useful as a smell test, not as a distribution.
        self.roll_histogram: dict[int, int] = {}

    def _check(self, ok, code, severity, message, detail=None):
        self.checks += 1
        if not ok:
            self.emit(code, severity, message, detail)

    def _hand_delta(self, before, after, pid):
        return [after.hands[pid][r] - before.hands[pid][r] for r in range(R.N_RESOURCES)]

    def _bank_delta(self, before, after):
        return [after.bank[r] - before.bank[r] for r in range(R.N_RESOURCES)]

    # ------------------------------------------------------------------ #
    def run(self, before: ref.Snapshot, after: ref.Snapshot, action: int,
            legal_moves) -> None:
        kind, arg1, arg2 = unpack_action(action)
        actor = before.current_player_idx

        self._check(
            action in legal_moves,
            "ILLEGAL_ACTION", Severity.RULE,
            f"action {RESPONSE2STR[kind] if kind < len(RESPONSE2STR) else kind} was "
            f"applied but was not in the generated legal move list",
        )

        handler = {
            SETUP_RESPONSE: self._setup,
            ROLL: self._roll,
            PASS: self._pass,
            BUILD_ROAD: self._build_road,
            BUILD_SETTLEMENT: self._build_settlement,
            BUILD_CITY: self._build_city,
            PURCHASE_DEV_CARD: self._buy_dev,
            PLAY_KNIGHT: self._knight,
            PLAY_MONOPOLY: self._monopoly,
            PLAY_YEAR_OF_PLENTY: self._year_of_plenty,
            PLAY_ROAD_BUILDER: self._road_builder,
            SELECT_ROBBER_RESPONSE: self._move_robber,
            PORT_TRADE: self._port_trade,
            TABLE_TRADE_PROPOSE: self._trade_propose,
            TABLE_TRADE_ACCEPT: self._trade_respond,
            TABLE_TRADE_REJECT: self._trade_respond,
            TABLE_TRADE_SELECT: self._trade_select,
            DISCARD_RESOURCE: self._discard,
        }.get(kind)

        if handler is None:
            self._check(False, "UNKNOWN_ACTION", Severity.CORRUPTION,
                        f"no transition rule for action kind {kind}")
            return

        allowed = handler(before, after, actor, arg1, arg2, action)
        if allowed is None:
            return

        unexpected = changed_fields(before, after) - ALWAYS_ALLOWED - set(allowed)
        self._check(
            not unexpected,
            "UNEXPECTED_STATE_CHANGE", Severity.CORRUPTION,
            f"{RESPONSE2STR[kind]} also changed {sorted(unexpected)}",
            self._describe(before, after, unexpected),
        )

    def _describe(self, before, after, fields):
        lines = []
        for name in sorted(fields):
            lines.append(f"{name}: {getattr(before, name)!r} -> {getattr(after, name)!r}")
        return "\n".join(lines)

    def _dev_card_play_allowed(self, before, actor, dev_type, name):
        """Rulebook p.5: one development card per turn, and never one bought
        this turn (victory point cards excepted)."""
        self._check(
            not before.dev_card_used,
            "SECOND_DEV_CARD", Severity.RULE,
            f"player {actor} played {name} after already playing a development "
            f"card this turn",
            "Rulebook p.5: 'You may play only 1 development card during your turn.'",
        )
        self._check(
            before.dev_cards[actor][dev_type] > 0,
            "DEV_CARD_NOT_HELD", Severity.RULE,
            f"player {actor} played {name} holding "
            f"{before.dev_cards[actor][dev_type]} of them "
            f"({before.new_dev_cards[actor][dev_type]} bought this turn)",
            "Rulebook p.5: a development card may not be played on the turn it "
            "was bought.",
        )

    # --- setup ---------------------------------------------------------- #
    def _setup(self, before, after, actor, sid, rid, action):
        self._check(
            before.settlement_owner[sid] == -1,
            "SETUP_OCCUPIED", Severity.RULE,
            f"player {actor} placed on intersection {sid}, already owned by "
            f"{before.settlement_owner[sid]}")
        self._check(
            ref.distance_rule_ok(self.topo, before, sid),
            "SETUP_DISTANCE_RULE", Severity.RULE,
            f"player {actor} placed at {sid}, adjacent to "
            f"{ref.adjacent_occupied(self.topo, before, sid)}")
        self._check(
            rid in self.topo.py_sett_adj_roads[sid],
            "SETUP_ROAD_NOT_ADJACENT", Severity.RULE,
            f"player {actor}'s setup road {rid} does not touch their settlement {sid}")
        self._check(
            before.road_owner[rid] == -1,
            "SETUP_ROAD_OCCUPIED", Severity.RULE,
            f"path {rid} already belongs to {before.road_owner[rid]}")

        self._check(
            after.settlement_owner[sid] == actor
            and after.settlement_type[sid] == SETTLEMENT
            and after.road_owner[rid] == actor,
            "SETUP_NOT_PLACED", Severity.CORRUPTION,
            f"after setup move, intersection {sid} / path {rid} are not owned by {actor}")
        self._check(
            after.settlements_built[actor] == before.settlements_built[actor] + 1
            and after.roads_built[actor] == before.roads_built[actor] + 1,
            "SETUP_COUNTER_WRONG", Severity.CORRUPTION,
            f"player {actor} piece counters did not advance by exactly one each")

        # Second settlement pays out. Rulebook, Almanac "Set-up Phase".
        if before.settlements_built[actor] == 1:
            gained = sum(self._hand_delta(before, after, actor))
            expected = sum(
                1 for hid, nodes in enumerate(self.topo.py_hex_settlement)
                if sid in nodes and self.topo.py_hex_resource[hid] != 5
            )
            self._check(
                gained == expected or not R.SECOND_SETTLEMENT_PRODUCES,
                "SETUP_PRODUCTION", Severity.DEVIATION,
                f"player {actor}'s second settlement paid {gained} resource cards, "
                f"the rules give {expected} (one per adjacent producing hex)",
                R.KNOWN_RULE_TEXT["SETUP_PRODUCTION"][0]
                + f"  [{R.KNOWN_RULE_TEXT['SETUP_PRODUCTION'][1]}]",
            )

        return {"settlement_owner", "settlement_type", "road_owner",
                "settlements_built", "roads_built", "ports_mask", "hands", "bank",
                "setup_turn_idx", "in_setup", "current_player_turn_idx"}

    # --- roll ----------------------------------------------------------- #
    def _roll(self, before, after, actor, _a1, _a2, _action):
        self._check(after.has_rolled, "ROLL_NOT_RECORDED", Severity.CORRUPTION,
                    "has_rolled is still false after a roll")
        self._check(not before.has_rolled, "DOUBLE_ROLL", Severity.RULE,
                    f"player {actor} rolled twice in one turn",
                    "Rulebook p.4: one roll for resource production per turn.")

        seven = after.prompt in (DISCARD, MOVE_ROBBER)
        if seven:
            self.roll_histogram[7] = self.roll_histogram.get(7, 0) + 1
            for p in range(self.n_players):
                held = before.total_cards(p)
                expected = R.discard_count(held)
                actual = after.discards_required[p]
                self._check(
                    actual == expected,
                    "DISCARD_AMOUNT_WRONG", Severity.RULE,
                    f"player {p} holds {held} cards and must discard {expected}, "
                    f"engine asked for {actual}",
                    "Rulebook p.5: every player with more than 7 resource cards "
                    "discards half, rounded down.",
                )
            self._check(
                self._hand_delta(before, after, actor) == [0] * R.N_RESOURCES,
                "SEVEN_PRODUCED_RESOURCES", Severity.RULE,
                "a 7 was rolled but resources were still handed out",
                "Rulebook p.5: 'If you roll a 7, no one receives any resources.'",
            )
            return {"discards_required", "has_rolled"}

        # Not a 7: the hand deltas must match the production for *some* roll.
        observed = [self._hand_delta(before, after, p) for p in range(self.n_players)]
        self._check_production(before, observed)
        return {"hands", "bank", "has_rolled"}

    def _check_production(self, before, observed):
        candidates = [r for r in range(R.DICE_MIN, R.DICE_MAX + 1) if r != 7]

        matches = [r for r in candidates
                   if ref.official_production(self.topo, before, roll=r,
                                              n_players=self.n_players) == observed]
        if matches:
            self.checks += 1
            # A roll nobody has a building on produces nothing, and so looks
            # like every other barren roll. Only record the histogram when the
            # payout identifies the roll uniquely, otherwise the sample skews
            # towards whichever number happens to be checked first.
            if len(matches) == 1:
                self.roll_histogram[matches[0]] = self.roll_histogram.get(matches[0], 0) + 1
            return

        # No roll explains it under the official rule. Is it explained by the
        # engine handing out a short resource anyway?
        for roll in candidates:
            raw = ref.raw_production(self.topo, before, roll, self.n_players)
            if raw == observed:
                continue  # would have matched official too; unreachable
            plausible = all(
                0 <= observed[p][res] <= raw[p][res]
                for p in range(self.n_players) for res in range(R.N_RESOURCES)
            )
            short = any(
                sum(raw[p][res] for p in range(self.n_players)) > before.bank[res]
                for res in range(R.N_RESOURCES)
            )
            if plausible and short:
                self.roll_histogram[roll] = self.roll_histogram.get(roll, 0) + 1
                official = ref.official_production(self.topo, before, roll, self.n_players)
                self._check(
                    False, "BANK_SHORTAGE", Severity.DEVIATION,
                    "the bank ran short and the engine split what was left between "
                    "the players; the rules give nobody any of that resource",
                    f"entitled={raw}\nrules give={official}\nengine gave={observed}\n"
                    f"bank was={before.bank}\n"
                    + R.KNOWN_RULE_TEXT["BANK_SHORTAGE"][0]
                    + f"  [{R.KNOWN_RULE_TEXT['BANK_SHORTAGE'][1]}]",
                )
                return

        self._check(
            False, "PRODUCTION_MISMATCH", Severity.CORRUPTION,
            "resources were handed out that no dice roll can explain",
            f"observed deltas={observed}\nbank before={before.bank}",
        )

    # --- turn ----------------------------------------------------------- #
    def _pass(self, before, after, actor, _a1, _a2, _action):
        self._check(
            before.has_rolled,
            "PASS_WITHOUT_ROLL", Severity.RULE,
            f"player {before.current_player_turn_idx} ended their turn without rolling",
            "Rulebook p.4: rolling for resource production is the first step of "
            "every turn and the result applies to all players.",
        )
        prev = before.current_player_turn_idx
        self._check(
            after.current_player_turn_idx == (prev + 1) % self.n_players,
            "TURN_ORDER", Severity.RULE,
            f"turn passed from {prev} to {after.current_player_turn_idx}",
            "Rulebook p.4: pass the dice to the player on your left.",
        )
        self._check(after.turn_index == before.turn_index + 1, "TURN_INDEX",
                    Severity.CORRUPTION, "turn counter did not advance by one")
        self._check(not after.has_rolled and not after.dev_card_used,
                    "TURN_FLAGS_NOT_RESET", Severity.CORRUPTION,
                    "has_rolled / dev_card_used were not cleared for the new turn")

        expected_dev = [before.dev_cards[prev][t] + before.new_dev_cards[prev][t]
                        for t in range(len(before.dev_cards[prev]))]
        self._check(
            after.dev_cards[prev] == expected_dev and not any(after.new_dev_cards[prev]),
            "DEV_CARDS_NOT_RELEASED", Severity.CORRUPTION,
            f"player {prev}'s cards bought last turn were not made playable",
            f"before held={before.dev_cards[prev]} new={before.new_dev_cards[prev]} "
            f"after held={after.dev_cards[prev]} new={after.new_dev_cards[prev]}",
        )
        return {"current_player_turn_idx", "turn_index", "has_rolled",
                "dev_card_used", "dev_cards", "new_dev_cards"}

    # --- building ------------------------------------------------------- #
    def _charge(self, before, after, actor, kind, label):
        cost = COSTS[kind]
        hand_delta = self._hand_delta(before, after, actor)
        bank_delta = self._bank_delta(before, after)
        self._check(
            hand_delta == [-c for c in cost],
            "BUILD_COST_WRONG", Severity.RULE,
            f"{label} charged {[-d for d in hand_delta]}, the cost is {list(cost)}",
        )
        self._check(
            bank_delta == list(cost),
            "BUILD_PAYMENT_LOST", Severity.CORRUPTION,
            f"{label}: player paid {[-d for d in hand_delta]} but the bank received "
            f"{bank_delta}",
        )
        self._check(
            all(before.hands[actor][r] >= cost[r] for r in range(R.N_RESOURCES)),
            "BUILD_UNAFFORDABLE", Severity.RULE,
            f"player {actor} built a {label} holding {before.hands[actor]}, "
            f"cost {list(cost)}",
        )

    def _build_road(self, before, after, actor, rid, _a2, _action):
        self._charge(before, after, actor, BUILD_ROAD, "road")
        self._check(
            rid in ref.legal_roads(self.topo, before, actor),
            "ROAD_PLACEMENT_ILLEGAL", Severity.RULE,
            f"player {actor} built road {rid}, which does not connect to their network",
            "Rulebook p.4: a new road must connect to one of your existing roads, "
            "settlements or cities.",
        )
        self._check(before.road_owner[rid] == -1, "ROAD_OCCUPIED", Severity.RULE,
                    f"path {rid} already belongs to {before.road_owner[rid]}")
        self._check(after.road_owner[rid] == actor, "ROAD_NOT_PLACED",
                    Severity.CORRUPTION, f"path {rid} not owned by {actor} afterwards")
        self._check(
            after.roads_built[actor] == before.roads_built[actor] + 1,
            "ROAD_COUNTER", Severity.CORRUPTION, "road counter did not advance by one")
        self._check(
            before.roads_built[actor] < R.MAX_ROADS,
            "ROAD_SUPPLY_EXHAUSTED", Severity.RULE,
            f"player {actor} built a {before.roads_built[actor] + 1}th road, "
            f"only {R.MAX_ROADS} pieces exist")
        return {"hands", "bank", "road_owner", "roads_built"}

    def _build_settlement(self, before, after, actor, sid, _a2, _action):
        self._charge(before, after, actor, BUILD_SETTLEMENT, "settlement")
        self._check(
            sid in ref.legal_settlements(self.topo, before, actor),
            "SETTLEMENT_PLACEMENT_ILLEGAL", Severity.RULE,
            f"player {actor} built a settlement at {sid}, which is occupied, breaks "
            f"the distance rule, or touches none of their roads",
            f"occupied_by={before.settlement_owner[sid]} "
            f"adjacent_occupied={ref.adjacent_occupied(self.topo, before, sid)}",
        )
        self._check(
            after.settlement_owner[sid] == actor and after.settlement_type[sid] == SETTLEMENT,
            "SETTLEMENT_NOT_PLACED", Severity.CORRUPTION,
            f"intersection {sid} not a settlement of {actor} afterwards")
        self._check(
            after.settlements_built[actor] == before.settlements_built[actor] + 1,
            "SETTLEMENT_COUNTER", Severity.CORRUPTION,
            "settlement counter did not advance by one")
        self._check(
            before.settlements_built[actor] < R.MAX_SETTLEMENTS,
            "SETTLEMENT_SUPPLY_EXHAUSTED", Severity.RULE,
            f"player {actor} has {before.settlements_built[actor]} settlements "
            f"standing, only {R.MAX_SETTLEMENTS} pieces exist")
        return {"hands", "bank", "settlement_owner", "settlement_type",
                "settlements_built", "ports_mask"}

    def _build_city(self, before, after, actor, sid, _a2, _action):
        self._charge(before, after, actor, BUILD_CITY, "city")
        self._check(
            sid in ref.legal_cities(before, actor),
            "CITY_PLACEMENT_ILLEGAL", Severity.RULE,
            f"player {actor} upgraded intersection {sid}, which holds "
            f"owner={before.settlement_owner[sid]} type={before.settlement_type[sid]}",
            "Rulebook p.5: you may only establish a city by upgrading one of your "
            "own settlements.",
        )
        self._check(
            after.settlement_type[sid] == CITY and after.settlement_owner[sid] == actor,
            "CITY_NOT_PLACED", Severity.CORRUPTION,
            f"intersection {sid} is not {actor}'s city afterwards")
        self._check(
            after.cities_built[actor] == before.cities_built[actor] + 1
            and after.settlements_built[actor] == before.settlements_built[actor] - 1,
            "CITY_COUNTERS", Severity.CORRUPTION,
            "upgrading did not move one piece from settlements to cities",
            f"settlements {before.settlements_built[actor]}->"
            f"{after.settlements_built[actor]} cities "
            f"{before.cities_built[actor]}->{after.cities_built[actor]}")
        self._check(
            before.cities_built[actor] < R.MAX_CITIES,
            "CITY_SUPPLY_EXHAUSTED", Severity.RULE,
            f"player {actor} built a {before.cities_built[actor] + 1}th city, "
            f"only {R.MAX_CITIES} pieces exist")
        return {"hands", "bank", "settlement_type", "settlements_built", "cities_built"}

    def _buy_dev(self, before, after, actor, _a1, _a2, _action):
        self._charge(before, after, actor, PURCHASE_DEV_CARD, "development card")
        self._check(
            len(before.dev_deck) > 0,
            "DEV_DECK_EMPTY", Severity.RULE,
            "a development card was bought from an empty deck",
            "Rulebook p.5: you cannot buy development cards if the supply is empty.",
        )
        self._check(
            len(after.dev_deck) == len(before.dev_deck) - 1,
            "DEV_DECK_SIZE", Severity.CORRUPTION,
            f"deck went from {len(before.dev_deck)} to {len(after.dev_deck)} cards")
        if before.dev_deck:
            drawn = before.dev_deck[-1]
            self._check(
                after.dev_deck == before.dev_deck[:-1],
                "DEV_DRAW_NOT_FROM_TOP", Severity.CORRUPTION,
                "the card drawn was not the top of the deck")
            self._check(
                after.new_dev_cards[actor][drawn] == before.new_dev_cards[actor][drawn] + 1,
                "DEV_CARD_NOT_DEALT", Severity.CORRUPTION,
                f"player {actor} did not receive the drawn card (type {drawn})")
            self._check(
                after.dev_cards[actor] == before.dev_cards[actor],
                "DEV_CARD_PLAYABLE_TOO_SOON", Severity.RULE,
                f"the card player {actor} just bought went straight into their "
                f"playable hand",
                "Rulebook p.5: a development card may not be played on the turn it "
                "was bought.",
            )
        return {"hands", "bank", "dev_deck", "new_dev_cards"}

    # --- development cards ---------------------------------------------- #
    def _knight(self, before, after, actor, _a1, _a2, _action):
        self._dev_card_play_allowed(before, actor, KNIGHT, "a knight")
        self._check(
            after.dev_cards[actor][KNIGHT] == before.dev_cards[actor][KNIGHT] - 1,
            "KNIGHT_NOT_SPENT", Severity.CORRUPTION, "knight card was not discarded")
        self._check(
            after.used_knights[actor] == before.used_knights[actor] + 1,
            "KNIGHT_NOT_COUNTED", Severity.CORRUPTION,
            "played knight was not added to the player's army")
        self._check(after.dev_card_used, "DEV_FLAG_NOT_SET", Severity.CORRUPTION,
                    "dev_card_used was not set after playing a knight")
        self._check(
            after.prompt == MOVE_ROBBER,
            "KNIGHT_NO_ROBBER", Severity.RULE,
            f"playing a knight left the prompt at {after.prompt}",
            "Rulebook p.5: if you play a knight card, you must immediately move "
            "the robber.",
        )
        return {"dev_cards", "used_knights", "dev_card_used"}

    def _monopoly(self, before, after, actor, res, _a2, _action):
        self._dev_card_play_allowed(before, actor, MONOPOLY, "monopoly")
        self.played_progress[MONOPOLY] = self.played_progress.get(MONOPOLY, 0) + 1

        taken = sum(before.hands[p][res] for p in range(self.n_players) if p != actor)
        self._check(
            after.hands[actor][res] == before.hands[actor][res] + taken,
            "MONOPOLY_SHORT", Severity.RULE,
            f"monopoly on {R.RESOURCE_NAMES[res]}: player {actor} received "
            f"{after.hands[actor][res] - before.hands[actor][res]}, opponents held {taken}",
            "Rulebook p.5: all other players must give you all of their resources "
            "of that type.",
        )
        for p in range(self.n_players):
            if p == actor:
                continue
            self._check(
                after.hands[p][res] == 0,
                "MONOPOLY_LEFTOVER", Severity.RULE,
                f"player {p} kept {after.hands[p][res]} {R.RESOURCE_NAMES[res]} "
                f"after a monopoly")
            others = [r for r in range(R.N_RESOURCES) if r != res]
            self._check(
                all(after.hands[p][r] == before.hands[p][r] for r in others),
                "MONOPOLY_TOOK_TOO_MUCH", Severity.RULE,
                f"monopoly on {R.RESOURCE_NAMES[res]} also moved other resources "
                f"from player {p}")
        self._check(
            self._bank_delta(before, after) == [0] * R.N_RESOURCES,
            "MONOPOLY_TOUCHED_BANK", Severity.CORRUPTION,
            "monopoly changed the bank; it only moves cards between players")
        return {"dev_cards", "dev_card_used", "hands"}

    def _year_of_plenty(self, before, after, actor, res1, res2, _action):
        self._dev_card_play_allowed(before, actor, YEAR_OF_PLENTY, "year of plenty")
        self.played_progress[YEAR_OF_PLENTY] = self.played_progress.get(YEAR_OF_PLENTY, 0) + 1

        want = [0] * R.N_RESOURCES
        want[res1] += 1
        want[res2] += 1
        granted = [min(want[r], before.bank[r]) for r in range(R.N_RESOURCES)]

        self._check(
            self._hand_delta(before, after, actor) == granted,
            "YEAR_OF_PLENTY_WRONG", Severity.RULE,
            f"year of plenty gave {self._hand_delta(before, after, actor)}, "
            f"expected {granted}",
            "Rulebook p.5: take any 2 resource cards from the supply stacks.",
        )
        self._check(
            self._bank_delta(before, after) == [-g for g in granted],
            "YEAR_OF_PLENTY_BANK", Severity.CORRUPTION,
            "cards taken by year of plenty did not come out of the bank")
        if granted != want:
            self._check(
                False, "YEAR_OF_PLENTY_BANK_EMPTY", Severity.NOTE,
                f"year of plenty asked for {want} but the bank only had {before.bank}; "
                f"the shortfall was silently dropped",
                "The rulebook does not say what happens when the supply cannot pay "
                "a Year of Plenty. Worth deciding deliberately.",
            )
        return {"dev_cards", "dev_card_used", "hands", "bank"}

    def _road_builder(self, before, after, actor, rid1, rid2, _action):
        self._dev_card_play_allowed(before, actor, ROAD_BUILDER, "road building")
        self.played_progress[ROAD_BUILDER] = self.played_progress.get(ROAD_BUILDER, 0) + 1

        self._check(
            self._hand_delta(before, after, actor) == [0] * R.N_RESOURCES,
            "ROAD_BUILDER_CHARGED", Severity.RULE,
            "road building roads were not free",
            "Rulebook p.5: you may immediately place 2 free roads.",
        )

        first_legal = ref.legal_roads(self.topo, before, actor)
        placed = []
        if rid1 >= 0:
            placed.append(rid1)
            self._check(rid1 in first_legal, "ROAD_BUILDER_ILLEGAL", Severity.RULE,
                        f"road building placed {rid1}, not connected to player "
                        f"{actor}'s network")
        if rid2 >= 0 and rid2 != rid1:
            placed.append(rid2)
            second_legal = ref.legal_roads(self.topo, before, actor, extra_road=rid1)
            self._check(rid2 in second_legal, "ROAD_BUILDER_ILLEGAL", Severity.RULE,
                        f"road building placed {rid2}, not connected to player "
                        f"{actor}'s network (even counting {rid1})")

        expected_roads = before.roads_built[actor] + len(placed)
        self._check(
            after.roads_built[actor] == expected_roads,
            "ROAD_BUILDER_COUNT", Severity.CORRUPTION,
            f"road building placed {len(placed)} roads but the counter went "
            f"{before.roads_built[actor]} -> {after.roads_built[actor]}")
        self._check(
            len(placed) == 2 or before.roads_built[actor] >= R.MAX_ROADS - 1,
            "ROAD_BUILDER_ONE_ROAD", Severity.NOTE,
            f"road building placed {len(placed)} road(s) while player {actor} still "
            f"had {R.MAX_ROADS - before.roads_built[actor]} pieces left",
            "Rulebook p.5: the card places 2 free roads.",
        )
        self._check(
            expected_roads <= R.MAX_ROADS,
            "ROAD_BUILDER_OVER_SUPPLY", Severity.RULE,
            f"road building took player {actor} to {expected_roads} roads, "
            f"only {R.MAX_ROADS} exist")
        return {"dev_cards", "dev_card_used", "road_owner", "roads_built"}

    # --- robber --------------------------------------------------------- #
    def _move_robber(self, before, after, actor, hid, victim, _action):
        self._check(
            hid != before.robber_hex,
            "ROBBER_DID_NOT_MOVE", Severity.RULE,
            f"the robber was 'moved' to hex {hid}, where it already was",
            "Rulebook p.5: you must move the robber to any OTHER terrain hex.",
        )
        self._check(after.robber_hex == hid, "ROBBER_NOT_MOVED", Severity.CORRUPTION,
                    f"robber is on {after.robber_hex}, expected {hid}")

        eligible = ref.robbable_players(self.topo, before, hid, actor)
        if victim != NONE_PLAYER:
            self._check(
                victim in eligible,
                "ROBBER_BAD_VICTIM", Severity.RULE,
                f"player {actor} stole from {victim}, who has no building on hex {hid}",
                f"eligible={sorted(eligible)}",
            )
            self._check(victim != actor, "ROBBER_SELF_STEAL", Severity.RULE,
                        f"player {actor} stole from themselves")

            stolen = self._hand_delta(before, after, victim)
            gained = self._hand_delta(before, after, actor)
            had = before.total_cards(victim)
            expected_moves = 1 if had > 0 else 0
            self._check(
                sum(-d for d in stolen) == expected_moves and sum(gained) == expected_moves,
                "ROBBER_STEAL_AMOUNT", Severity.RULE,
                f"stealing moved {sum(gained)} cards from player {victim} "
                f"(who held {had}); exactly {expected_moves} should have moved",
                "Rulebook p.5: you take 1 card at random. If that player has no "
                "cards, you get nothing.",
            )
            self._check(
                all(g == -s for g, s in zip(gained, stolen)),
                "ROBBER_CARD_CHANGED", Severity.CORRUPTION,
                f"the card taken from {victim} is not the card {actor} received",
                f"victim delta={stolen} thief delta={gained}",
            )
        else:
            with_cards = {p for p in eligible if before.total_cards(p) > 0}
            self._check(
                not with_cards,
                "ROBBER_STEAL_DECLINED", Severity.RULE,
                f"player {actor} moved the robber to hex {hid} and stole from nobody, "
                f"but {sorted(with_cards)} have buildings there and cards to take",
                "Rulebook p.5: after moving the robber you steal 1 resource card "
                "from an adjacent opponent. It is not optional.",
            )
            self._check(
                self._hand_delta(before, after, actor) == [0] * R.N_RESOURCES,
                "ROBBER_PHANTOM_STEAL", Severity.CORRUPTION,
                f"player {actor} gained cards without naming a victim")

        self._check(
            self._bank_delta(before, after) == [0] * R.N_RESOURCES,
            "ROBBER_TOUCHED_BANK", Severity.CORRUPTION,
            "moving the robber changed the bank")
        return {"robber_hex", "hands"}

    # --- trading -------------------------------------------------------- #
    def _port_trade(self, before, after, actor, _a1, _a2, action):
        give, rate, take = unpack_port_trade(action)
        expected_rate = ref.maritime_rate(before.ports_mask[actor], give)
        self._check(
            rate == expected_rate,
            "MARITIME_RATE_WRONG", Severity.RULE,
            f"player {actor} traded {R.RESOURCE_NAMES[give]} at {rate}:1, their "
            f"harbours allow {expected_rate}:1",
            f"ports_mask={before.ports_mask[actor]:#07b}",
        )
        self._check(
            before.hands[actor][give] >= rate,
            "MARITIME_UNAFFORDABLE", Severity.RULE,
            f"player {actor} traded away {rate} {R.RESOURCE_NAMES[give]} holding "
            f"{before.hands[actor][give]}")
        self._check(
            before.bank[take] >= 1,
            "MARITIME_BANK_EMPTY", Severity.RULE,
            f"player {actor} took {R.RESOURCE_NAMES[take]} from an empty supply")
        self._check(give != take, "MARITIME_SELF_TRADE", Severity.RULE,
                    f"player {actor} traded {R.RESOURCE_NAMES[give]} for itself")

        expected_hand = [0] * R.N_RESOURCES
        expected_hand[give] -= rate
        expected_hand[take] += 1
        self._check(
            self._hand_delta(before, after, actor) == expected_hand,
            "MARITIME_HAND_WRONG", Severity.RULE,
            f"maritime trade moved {self._hand_delta(before, after, actor)}, "
            f"expected {expected_hand}")
        self._check(
            self._bank_delta(before, after) == [-d for d in expected_hand],
            "MARITIME_BANK_WRONG", Severity.CORRUPTION,
            "maritime trade did not balance against the bank")
        return {"hands", "bank"}

    def _trade_propose(self, before, after, actor, _a1, _a2, action):
        give, take = unpack_table_trade(action)
        self._check(
            actor == before.current_player_turn_idx,
            "TRADE_PROPOSER_NOT_TURN_HOLDER", Severity.RULE,
            f"player {actor} proposed a trade on player "
            f"{before.current_player_turn_idx}'s turn",
            "Rulebook p.4: players may only trade with the player whose turn it is.",
        )
        self._check(
            all(before.hands[actor][r] >= give[r] for r in range(R.N_RESOURCES)),
            "TRADE_OFFER_UNBACKED", Severity.RULE,
            f"player {actor} offered {give} holding {before.hands[actor]}")
        self._check(
            all(h == b for h, b in zip(after.hands[actor], before.hands[actor])),
            "TRADE_PROPOSAL_MOVED_CARDS", Severity.CORRUPTION,
            "proposing a trade moved resources")
        return {"trade_offer_from", "trade_offer_give", "trade_offer_take",
                "trade_accept_mask"}

    def _trade_respond(self, before, after, actor, _a1, _a2, _action):
        self._check(
            all(a == b for a, b in zip(after.hands[actor], before.hands[actor])),
            "TRADE_RESPONSE_MOVED_CARDS", Severity.CORRUPTION,
            "accepting or rejecting an offer moved resources on its own")
        return {"trade_accept_mask", "trade_offer_from", "trade_offer_give",
                "trade_offer_take"}

    def _trade_select(self, before, after, actor, partner, _a2, _action):
        if partner == 0xFFF or partner < 0 or partner >= self.n_players:
            return {"trade_offer_from", "trade_offer_give", "trade_offer_take",
                    "trade_accept_mask"}

        give = before.trade_offer_give or [0] * R.N_RESOURCES
        take = before.trade_offer_take or [0] * R.N_RESOURCES
        self._check(
            before.trade_accept_mask is not None and before.trade_accept_mask[partner],
            "TRADE_PARTNER_DID_NOT_ACCEPT", Severity.RULE,
            f"player {actor} completed a trade with {partner}, who did not accept",
        )
        self._check(
            all(before.hands[partner][r] >= take[r] for r in range(R.N_RESOURCES)),
            "TRADE_PARTNER_SHORT", Severity.RULE,
            f"player {partner} owed {take} but held {before.hands[partner]}")

        want_actor = [take[r] - give[r] for r in range(R.N_RESOURCES)]
        want_partner = [give[r] - take[r] for r in range(R.N_RESOURCES)]
        self._check(
            self._hand_delta(before, after, actor) == want_actor,
            "TRADE_EXCHANGE_WRONG", Severity.RULE,
            f"proposer received {self._hand_delta(before, after, actor)}, "
            f"agreed {want_actor}")
        self._check(
            self._hand_delta(before, after, partner) == want_partner,
            "TRADE_EXCHANGE_WRONG", Severity.RULE,
            f"partner received {self._hand_delta(before, after, partner)}, "
            f"agreed {want_partner}")
        self._check(
            self._bank_delta(before, after) == [0] * R.N_RESOURCES,
            "TRADE_TOUCHED_BANK", Severity.CORRUPTION,
            "a player-to-player trade changed the bank")
        for p in range(self.n_players):
            if p in (actor, partner):
                continue
            self._check(
                after.hands[p] == before.hands[p],
                "TRADE_HIT_BYSTANDER", Severity.CORRUPTION,
                f"a trade between {actor} and {partner} changed player {p}'s hand")
        return {"hands", "trade_offer_from", "trade_offer_give", "trade_offer_take",
                "trade_accept_mask"}

    # --- discarding ----------------------------------------------------- #
    def _discard(self, before, after, actor, res, _a2, _action):
        self._check(
            before.discards_required[actor] > 0,
            "DISCARD_NOT_OWED", Severity.RULE,
            f"player {actor} discarded without owing any cards")
        self._check(
            before.hands[actor][res] > 0,
            "DISCARD_CARD_NOT_HELD", Severity.RULE,
            f"player {actor} discarded {R.RESOURCE_NAMES[res]} holding none")

        expected = [0] * R.N_RESOURCES
        expected[res] = -1
        self._check(
            self._hand_delta(before, after, actor) == expected,
            "DISCARD_AMOUNT", Severity.RULE,
            f"discard moved {self._hand_delta(before, after, actor)}, expected "
            f"one {R.RESOURCE_NAMES[res]}")
        self._check(
            self._bank_delta(before, after) == [-d for d in expected],
            "DISCARD_NOT_RETURNED", Severity.CORRUPTION,
            "the discarded card did not go back to the bank",
            "Rulebook p.5: discarded cards are returned to the bank.",
        )
        self._check(
            after.discards_required[actor] == before.discards_required[actor] - 1,
            "DISCARD_COUNTER", Severity.CORRUPTION,
            "discard counter did not decrease by one")
        return {"hands", "bank", "discards_required", "current_player_idx"}
