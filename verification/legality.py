"""Audit of the legal-move list itself.

Two failure modes matter and they are different:

  offered but illegal  -- the engine will let a player do something the rules
                          forbid, so a bot can win by cheating
  legal but missing    -- the engine silently removes an option, so every bot
                          plays a subtly different game from Catan

Both are checked by re-deriving the move set from the board and comparing.
"""

from catan.actions import (
    SETUP_TURN, PLAY_PRETURN, PLAY_TURN, MOVE_ROBBER, DISCARD,
    DECIDE_TRADE, DECIDE_ACCEPTEES,
    SETUP_RESPONSE, ROLL, PASS, BUILD_ROAD, BUILD_SETTLEMENT, BUILD_CITY,
    PURCHASE_DEV_CARD, PLAY_KNIGHT, PLAY_MONOPOLY, PLAY_YEAR_OF_PLENTY,
    PLAY_ROAD_BUILDER, PORT_TRADE, TABLE_TRADE_PROPOSE, TABLE_TRADE_SELECT,
    TABLE_TRADE_ACCEPT, TABLE_TRADE_REJECT, DISCARD_RESOURCE,
    SELECT_ROBBER_RESPONSE, RESPONSE2STR, PROMPT2STR,
    unpack_action, unpack_port_trade,
)
from catan.ids import (
    NONE_PLAYER, KNIGHT, YEAR_OF_PLENTY, MONOPOLY, ROAD_BUILDER,
    ROADS_ALLOWED, SETTLEMENTS_ALLOWED, CITIES_ALLOWED,
)

from verification import rules as R
from verification import reference as ref
from verification.report import Severity


def _affordable(hand, cost) -> bool:
    return all(hand[r] >= cost[r] for r in range(R.N_RESOURCES))


class LegalityAuditor:
    def __init__(self, topo, n_players: int, emit):
        self.topo = topo
        self.n_players = n_players
        self.emit = emit
        self.checks = 0

    def _check(self, ok, code, severity, message, detail=None):
        self.checks += 1
        if not ok:
            self.emit(code, severity, message, detail)

    def _compare(self, label, offered: set, legal: set, describe, severity=Severity.RULE):
        extra = offered - legal
        missing = legal - offered
        self._check(
            not extra, "MOVE_OFFERED_ILLEGAL", severity,
            f"{label}: the engine offered {len(extra)} move(s) the rules forbid",
            "\n".join(describe(x) for x in sorted(extra)[:8]),
        )
        self._check(
            not missing, "MOVE_MISSING", severity,
            f"{label}: {len(missing)} legal move(s) were not offered",
            "\n".join(describe(x) for x in sorted(missing)[:8]),
        )

    # ------------------------------------------------------------------ #
    def run(self, snap: ref.Snapshot, moves: list[int]) -> None:
        prompt = snap.prompt
        label = PROMPT2STR[prompt] if 0 <= prompt < len(PROMPT2STR) else str(prompt)

        self._check(bool(moves), "NO_LEGAL_MOVES", Severity.CORRUPTION,
                    f"no legal moves offered at prompt {label}")
        if not moves:
            return

        self._check(
            len(moves) == len(set(moves)), "DUPLICATE_MOVES", Severity.NOTE,
            f"{label}: move list has {len(moves) - len(set(moves))} duplicate entries",
            "Duplicates skew any bot that samples uniformly over the list.",
        )

        by_kind: dict[int, set] = {}
        for action in moves:
            kind, a1, a2 = unpack_action(action)
            by_kind.setdefault(kind, set()).add((a1, a2))

        if prompt == SETUP_TURN:
            self._setup(snap, by_kind, label)
        elif prompt == PLAY_PRETURN:
            self._preturn(snap, by_kind, label)
        elif prompt == PLAY_TURN:
            self._turn(snap, by_kind, moves, label)
        elif prompt == MOVE_ROBBER:
            self._robber(snap, by_kind, label)
        elif prompt == DISCARD:
            self._discard(snap, by_kind, label)
        elif prompt == DECIDE_TRADE:
            self._decide_trade(snap, by_kind, label)
        elif prompt == DECIDE_ACCEPTEES:
            self._acceptees(snap, by_kind, label)

    # --- setup ---------------------------------------------------------- #
    def _setup(self, snap, by_kind, label):
        offered = by_kind.get(SETUP_RESPONSE, set())
        self._check(set(by_kind) == {SETUP_RESPONSE}, "SETUP_FOREIGN_MOVE", Severity.RULE,
                    f"{label}: offered non-placement actions "
                    f"{[RESPONSE2STR[k] for k in by_kind if k != SETUP_RESPONSE]}")

        legal = set()
        for sid in ref.legal_setup_settlements(self.topo, snap):
            for rid in self.topo.py_sett_adj_roads[sid]:
                if snap.road_owner[rid] == -1:
                    legal.add((sid, rid))

        self._compare(
            label, offered, legal,
            lambda x: f"settlement {x[0]} + road {x[1]} "
                      f"(intersection owner={snap.settlement_owner[x[0]]}, "
                      f"path owner={snap.road_owner[x[1]]})",
        )

    # --- pre-roll ------------------------------------------------------- #
    def _preturn(self, snap, by_kind, label):
        self._check(
            ROLL in by_kind, "ROLL_NOT_OFFERED", Severity.RULE,
            f"{label}: the player cannot roll",
            "Rulebook p.4: you must roll for resource production.",
        )
        self._check(
            PASS not in by_kind, "PASS_BEFORE_ROLL", Severity.RULE,
            f"{label}: the player may end their turn without rolling",
        )
        self._dev_cards(snap, by_kind, label)

    # --- main phase ----------------------------------------------------- #
    def _turn(self, snap, by_kind, moves, label):
        actor = snap.current_player_idx
        hand = snap.hands[actor]

        self._check(PASS in by_kind, "PASS_NOT_OFFERED", Severity.CORRUPTION,
                    f"{label}: the player cannot end their turn")
        self._check(ROLL not in by_kind, "SECOND_ROLL_OFFERED", Severity.RULE,
                    f"{label}: the player may roll again after already rolling")

        self._dev_cards(snap, by_kind, label)

        # roads
        offered_roads = {a for a, _ in by_kind.get(BUILD_ROAD, set())}
        legal_roads = set()
        if _affordable(hand, R.COST_ROAD) and snap.roads_built[actor] < ROADS_ALLOWED:
            legal_roads = ref.legal_roads(self.topo, snap, actor)
        self._compare(f"{label}/road", offered_roads, legal_roads,
                      lambda r: f"path {r} (owner={snap.road_owner[r]})")

        # settlements
        offered_setts = {a for a, _ in by_kind.get(BUILD_SETTLEMENT, set())}
        legal_setts = set()
        if (_affordable(hand, R.COST_SETTLEMENT)
                and snap.settlements_built[actor] < SETTLEMENTS_ALLOWED):
            legal_setts = ref.legal_settlements(self.topo, snap, actor)
        self._compare(f"{label}/settlement", offered_setts, legal_setts,
                      lambda s: f"intersection {s} (owner={snap.settlement_owner[s]}, "
                                f"adjacent occupied="
                                f"{ref.adjacent_occupied(self.topo, snap, s)})")

        # cities
        offered_cities = {a for a, _ in by_kind.get(BUILD_CITY, set())}
        legal_cities = set()
        if _affordable(hand, R.COST_CITY) and snap.cities_built[actor] < CITIES_ALLOWED:
            legal_cities = ref.legal_cities(snap, actor)
        self._compare(f"{label}/city", offered_cities, legal_cities,
                      lambda s: f"intersection {s} (owner={snap.settlement_owner[s]}, "
                                f"type={snap.settlement_type[s]})")

        # development card
        can_buy = _affordable(hand, R.COST_DEV_CARD) and len(snap.dev_deck) > 0
        self._check(
            (PURCHASE_DEV_CARD in by_kind) == can_buy,
            "DEV_PURCHASE_OFFER", Severity.RULE,
            f"{label}: buying a development card "
            f"{'offered' if PURCHASE_DEV_CARD in by_kind else 'not offered'} with hand "
            f"{hand} and {len(snap.dev_deck)} cards left",
        )

        # maritime trade rates
        for action in moves:
            kind, _, _ = unpack_action(action)
            if kind != PORT_TRADE:
                continue
            give, rate, take = unpack_port_trade(action)
            expected = ref.maritime_rate(snap.ports_mask[actor], give)
            self._check(
                rate == expected, "MARITIME_RATE_OFFERED", Severity.RULE,
                f"{label}: offered {R.RESOURCE_NAMES[give]} at {rate}:1, harbours "
                f"allow {expected}:1")
            self._check(
                hand[give] >= rate, "MARITIME_OFFER_UNAFFORDABLE", Severity.RULE,
                f"{label}: offered a {rate}:1 trade of {R.RESOURCE_NAMES[give]} "
                f"holding {hand[give]}")
            self._check(
                snap.bank[take] >= 1, "MARITIME_OFFER_BANK_EMPTY", Severity.RULE,
                f"{label}: offered to take {R.RESOURCE_NAMES[take]} from an empty bank")

        if TABLE_TRADE_PROPOSE in by_kind:
            self._check(
                False, "DOMESTIC_TRADE_RESTRICTED", Severity.DEVIATION,
                "domestic trade is modelled as a fixed menu of 1-for-1 and 2-for-1 "
                "single-resource offers",
                "Rulebook p.4 lets players negotiate any bundle of cards freely. "
                "The restriction is reasonable for a bot API, but it does mean the "
                "engine is not playing full Catan.",
            )

    def _dev_cards(self, snap, by_kind, label):
        actor = snap.current_player_idx
        held = snap.dev_cards[actor]
        spent = snap.dev_card_used

        for kind, dev_type, name in (
            (PLAY_KNIGHT, KNIGHT, "knight"),
            (PLAY_MONOPOLY, MONOPOLY, "monopoly"),
            (PLAY_YEAR_OF_PLENTY, YEAR_OF_PLENTY, "year of plenty"),
            (PLAY_ROAD_BUILDER, ROAD_BUILDER, "road building"),
        ):
            offered = kind in by_kind
            playable = held[dev_type] > 0 and not spent
            if kind == PLAY_ROAD_BUILDER and playable:
                # only offered when there is somewhere to put a road
                playable = (snap.roads_built[actor] < ROADS_ALLOWED
                            and bool(ref.legal_roads(self.topo, snap, actor)))
            self._check(
                offered <= playable, "DEV_CARD_OFFERED_ILLEGALLY", Severity.RULE,
                f"{label}: {name} offered with {held[dev_type]} in hand and "
                f"dev_card_used={spent}",
                "Rulebook p.5: one development card per turn, and never one bought "
                "this turn.",
            )
            self._check(
                playable <= offered, "DEV_CARD_MISSING", Severity.RULE,
                f"{label}: player {actor} holds a playable {name} but it was not offered",
            )

    # --- robber --------------------------------------------------------- #
    def _robber(self, snap, by_kind, label):
        offered = by_kind.get(SELECT_ROBBER_RESPONSE, set())
        self._check(set(by_kind) == {SELECT_ROBBER_RESPONSE}, "ROBBER_FOREIGN_MOVE",
                    Severity.RULE,
                    f"{label}: offered non-robber actions "
                    f"{[RESPONSE2STR[k] for k in by_kind if k != SELECT_ROBBER_RESPONSE]}")

        actor = snap.current_player_idx
        legal = set()
        steal_available = False
        for hid in ref.legal_robber_hexes(self.topo, snap):
            victims = ref.robbable_players(self.topo, snap, hid, actor)
            for v in victims:
                legal.add((hid, v))
            legal.add((hid, NONE_PLAYER))
            if any(snap.total_cards(v) > 0 for v in victims):
                steal_available = True

        self._compare(
            label, offered, legal,
            lambda x: f"hex {x[0]} victim "
                      f"{'none' if x[1] == NONE_PLAYER else x[1]}",
        )
        self._check(
            not (steal_available and any(v == NONE_PLAYER for _, v in offered)),
            "ROBBER_DECLINE_OFFERED", Severity.DEVIATION,
            "the engine lets a player move the robber and decline to steal, even "
            "when an adjacent opponent holds cards",
            "Rulebook p.5: 'Then you steal 1 (random) resource card from an opponent "
            "who has a settlement or city adjacent to the target terrain hex.' "
            "Stealing is not optional when a victim is available.",
        )
        self._check(
            all(hid != snap.robber_hex for hid, _ in offered),
            "ROBBER_STAY_OFFERED", Severity.RULE,
            f"{label}: the engine offered leaving the robber where it is",
            "Rulebook p.5: you must move the robber to any OTHER terrain hex.",
        )

    # --- discard -------------------------------------------------------- #
    def _discard(self, snap, by_kind, label):
        actor = snap.current_player_idx
        offered = {a for a, _ in by_kind.get(DISCARD_RESOURCE, set())}
        legal = {res for res in range(R.N_RESOURCES) if snap.hands[actor][res] > 0}
        self._compare(label, offered, legal,
                      lambda r: f"{R.RESOURCE_NAMES[r]} (holding {snap.hands[actor][r]})")
        self._check(
            snap.discards_required[actor] > 0, "DISCARD_NOT_OWED_OFFER",
            Severity.CORRUPTION,
            f"{label}: player {actor} is being asked to discard but owes nothing")

    # --- trade responses ------------------------------------------------ #
    def _decide_trade(self, snap, by_kind, label):
        actor = snap.current_player_idx
        take = snap.trade_offer_take or [0] * R.N_RESOURCES
        can_accept = all(snap.hands[actor][r] >= take[r] for r in range(R.N_RESOURCES))
        self._check(
            TABLE_TRADE_REJECT in by_kind, "REJECT_NOT_OFFERED", Severity.RULE,
            f"{label}: player {actor} cannot decline the offer")
        self._check(
            (TABLE_TRADE_ACCEPT in by_kind) == can_accept, "ACCEPT_OFFER", Severity.RULE,
            f"{label}: accept "
            f"{'offered' if TABLE_TRADE_ACCEPT in by_kind else 'not offered'} while "
            f"holding {snap.hands[actor]} against a request for {take}")
        self._check(
            actor != snap.trade_offer_from, "PROPOSER_ASKED_TO_DECIDE", Severity.CORRUPTION,
            f"{label}: player {actor} is being asked to respond to their own offer")

    def _acceptees(self, snap, by_kind, label):
        offered = {a for a, _ in by_kind.get(TABLE_TRADE_SELECT, set())}
        mask = snap.trade_accept_mask or []
        legal = {p for p, accepted in enumerate(mask)
                 if accepted and p != snap.current_player_idx}
        self._compare(label, offered, legal, lambda p: f"player {p}")
