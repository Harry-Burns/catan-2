"""Independent re-derivations of everything the engine computes.

These are deliberately *not* written by calling into `catan.interface`. A check
that asks the engine whether the engine is right proves nothing. Each function
here re-derives a quantity from the raw board arrays using the rulebook
definition, so a disagreement is a real signal.

Where a quantity is genuinely history-dependent (Longest Road, Largest Army),
the function returns the *set* of owners that could legitimately hold the card
given the current board, rather than a single answer. Anything outside that set
is a violation no matter what the history was.
"""

from collections import defaultdict
from typing import Iterable, Optional

from catan.ids import DESERT, SETTLEMENT, CITY, NONE_PLAYER

from verification import rules as R


# --- plain-Python view of the state ----------------------------------------

class Snapshot:
    """Everything observable about a GameState, as plain Python.

    Used both for invariant checks and for diffing one step against the next.
    """

    __slots__ = (
        "road_owner", "settlement_owner", "settlement_type", "robber_hex",
        "dev_deck", "bank", "hands", "ports_mask", "dev_cards", "new_dev_cards",
        "used_knights", "longest_road_len", "discards_required", "roads_built",
        "settlements_built", "cities_built", "current_player_idx",
        "current_player_turn_idx", "in_setup", "setup_turn_idx",
        "trade_offer_from", "trade_offer_give", "trade_offer_take",
        "trade_accept_mask", "longest_road_owner", "largest_army_owner",
        "has_rolled", "dev_card_used", "winner", "turn_index", "prompt",
        "action_log_len",
    )

    def __init__(self, gs):
        b = gs.board
        self.road_owner = b.road_owner.tolist()
        self.settlement_owner = b.settlement_owner.tolist()
        self.settlement_type = b.settlement_type.tolist()
        self.robber_hex = int(b.robber_hex)
        self.dev_deck = b.dev_deck.tolist()
        self.bank = b.bank_res.tolist()

        self.hands = [p.hand.tolist() for p in gs.players]
        self.ports_mask = [int(p.ports_mask) for p in gs.players]
        self.dev_cards = [p.dev_cards.tolist() for p in gs.players]
        self.new_dev_cards = [p.new_dev_cards.tolist() for p in gs.players]
        self.used_knights = [int(p.used_knights) for p in gs.players]
        self.longest_road_len = [int(p.longest_road_len) for p in gs.players]
        self.discards_required = [int(p.discards_required) for p in gs.players]
        self.roads_built = [int(p.roads_built) for p in gs.players]
        self.settlements_built = [int(p.settlements_built) for p in gs.players]
        self.cities_built = [int(p.cities_built) for p in gs.players]

        self.current_player_idx = int(gs.current_player_idx)
        self.current_player_turn_idx = int(gs.current_player_turn_idx)
        self.in_setup = bool(gs.in_setup)
        self.setup_turn_idx = int(gs.setup_turn_idx)
        self.trade_offer_from = int(gs.trade_offer_from)
        self.trade_offer_give = None if gs.trade_offer_give is None else gs.trade_offer_give.tolist()
        self.trade_offer_take = None if gs.trade_offer_take is None else gs.trade_offer_take.tolist()
        self.trade_accept_mask = None if gs.trade_accept_mask is None else [bool(x) for x in gs.trade_accept_mask]
        self.longest_road_owner = int(gs.longest_road_owner)
        self.largest_army_owner = int(gs.largest_army_owner)
        self.has_rolled = bool(gs.has_rolled)
        self.dev_card_used = bool(gs.dev_card_used)
        self.winner = int(gs.winner)
        self.turn_index = int(gs.turn_index)
        self.prompt = int(gs.prompt)
        self.action_log_len = len(gs.action_log)

    def total_cards(self, pid: int) -> int:
        return sum(self.hands[pid])


# --- board geometry ---------------------------------------------------------

def hex_numbers(topo) -> list[int]:
    return list(topo.py_hex_number)


def hex_resources(topo) -> list[int]:
    return list(topo.py_hex_resource)


def occupied_nodes(snap: Snapshot) -> set[int]:
    return {s for s, o in enumerate(snap.settlement_owner) if o != -1}


def adjacent_occupied(topo, snap: Snapshot, sid: int) -> list[int]:
    """Neighbouring intersections that hold a settlement or city."""
    return [n for n in topo.py_sett_adj_sett[sid] if snap.settlement_owner[n] != -1]


def distance_rule_ok(topo, snap: Snapshot, sid: int) -> bool:
    """Rulebook p.5: a settlement may only go where all 3 adjacent
    intersections are vacant -- including your own."""
    return not adjacent_occupied(topo, snap, sid)


# --- longest road -----------------------------------------------------------

def longest_road_length(topo, snap: Snapshot, pid: int) -> int:
    """Longest continuous road for `pid`, forks not counted.

    A trail may not pass *through* an intersection occupied by an opponent, but
    it may start or end at one -- an opponent's settlement breaks the road, it
    does not delete the segments on either side of it.
    """
    my_roads = [r for r, o in enumerate(snap.road_owner) if o == pid]
    if not my_roads:
        return 0

    blocked = {s for s, o in enumerate(snap.settlement_owner)
               if o != -1 and o != pid}

    incident: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for r in my_roads:
        a, b = topo.py_road_adj_sett[r]
        if a < 0 or b < 0:
            continue  # a path with a missing endpoint cannot extend a trail
        incident[a].append((r, b))
        incident[b].append((r, a))

    best = 0
    used: set[int] = set()

    def walk(node: int) -> int:
        longest = 0
        for road, nxt in incident[node]:
            if road in used:
                continue
            used.add(road)
            # continue past `nxt` only if an opponent has not built there
            onward = walk(nxt) if nxt not in blocked else 0
            used.discard(road)
            if 1 + onward > longest:
                longest = 1 + onward
        return longest

    # Every incident node is a candidate start, blocked ones included: a trail
    # may *begin* at an opponent's settlement.
    for start in incident:
        length = walk(start)
        if length > best:
            best = length
    return best


def all_longest_roads(topo, snap: Snapshot, n_players: int) -> list[int]:
    return [longest_road_length(topo, snap, pid) for pid in range(n_players)]


def acceptable_longest_road_owners(lengths: list[int]) -> set[int]:
    """Owners the Longest Road card could legitimately sit with.

    Who holds it depends on history (the incumbent keeps it on a tie), so this
    returns every defensible answer. Anything else is wrong regardless of how
    the game got here.
    """
    best = max(lengths) if lengths else 0
    if best < R.LONGEST_ROAD_MIN:
        # "Do the same [set the card aside] if no one has a 5+ segment road."
        return {NONE_PLAYER}
    leaders = {p for p, length in enumerate(lengths) if length == best}
    if len(leaders) == 1:
        return leaders
    # Tied: the incumbent keeps it, or it is set aside if the incumbent is not
    # among the leaders.
    return leaders | {NONE_PLAYER}


def acceptable_largest_army_owners(knights: list[int]) -> set[int]:
    best = max(knights) if knights else 0
    if best < R.LARGEST_ARMY_MIN:
        return {NONE_PLAYER}
    leaders = {p for p, k in enumerate(knights) if k == best}
    if len(leaders) == 1:
        return leaders
    return leaders | {NONE_PLAYER}


# --- victory points ---------------------------------------------------------

def victory_points(topo, snap: Snapshot, pid: int, vp_dev_index: int) -> int:
    """VPs read off the board, not off the engine's counters."""
    vp = 0
    for sid, owner in enumerate(snap.settlement_owner):
        if owner != pid:
            continue
        vp += R.VP_CITY if snap.settlement_type[sid] == CITY else R.VP_SETTLEMENT
    vp += snap.dev_cards[pid][vp_dev_index] + snap.new_dev_cards[pid][vp_dev_index]
    if snap.longest_road_owner == pid:
        vp += R.VP_LONGEST_ROAD
    if snap.largest_army_owner == pid:
        vp += R.VP_LARGEST_ARMY
    return vp


# --- production -------------------------------------------------------------

def raw_production(topo, snap: Snapshot, roll: int, n_players: int) -> list[list[int]]:
    """What each player is *entitled* to for `roll`, before the bank is checked."""
    gains = [[0] * R.N_RESOURCES for _ in range(n_players)]
    numbers = topo.py_hex_number
    resources = topo.py_hex_resource
    for hid, nodes in enumerate(topo.py_hex_settlement):
        if numbers[hid] != roll or hid == snap.robber_hex:
            continue
        res = resources[hid]
        if res == DESERT:
            continue
        for sid in nodes:
            owner = snap.settlement_owner[sid]
            if owner < 0:
                continue
            gains[owner][res] += 2 if snap.settlement_type[sid] == CITY else 1
    return gains


def official_production(topo, snap: Snapshot, roll: int, n_players: int) -> list[list[int]]:
    """`raw_production` with the rulebook's bank-shortage rule applied.

    "If there are not enough of a given resource in the supply to fulfill
     everyone's production, then no one receives any of that resource during
     that turn (unless it only affects 1 player)."   -- rulebook p.4
    """
    gains = raw_production(topo, snap, roll, n_players)
    for res in range(R.N_RESOURCES):
        demand = sum(gains[p][res] for p in range(n_players))
        supply = snap.bank[res]
        if demand <= supply:
            continue
        entitled = [p for p in range(n_players) if gains[p][res] > 0]
        if len(entitled) == 1:
            # only one player affected -> they take whatever is left
            gains[entitled[0]][res] = supply
        else:
            for p in range(n_players):
                gains[p][res] = 0
    return gains


# --- harbours ---------------------------------------------------------------

def port_mask_from_board(topo, snap: Snapshot, pid: int) -> int:
    """Rebuild a player's harbour bitmask from where their buildings actually are."""
    mask = 0
    for sid, owner in enumerate(snap.settlement_owner):
        if owner != pid:
            continue
        bit = topo.py_port_bit.get(sid)
        if bit is not None:
            mask |= 1 << bit
    return mask


def maritime_rate(ports_mask: int, resource: int) -> int:
    """Rulebook p.4: 2:1 at that resource's harbour, else 3:1 with a generic
    harbour, else always 4:1. A specific harbour gives no discount on anything
    else -- "not even 3:1"."""
    if (ports_mask >> (resource + 1)) & 1:
        return R.SPECIFIC_HARBOUR_RATE
    if ports_mask & 1:
        return R.GENERIC_HARBOUR_RATE
    return R.BANK_TRADE_RATE


# --- legal placements -------------------------------------------------------

def legal_setup_settlements(topo, snap: Snapshot) -> set[int]:
    """During setup: any vacant intersection obeying the distance rule."""
    return {
        sid for sid, owner in enumerate(snap.settlement_owner)
        if owner == -1 and distance_rule_ok(topo, snap, sid)
    }


def legal_settlements(topo, snap: Snapshot, pid: int) -> set[int]:
    """Post-setup: distance rule, plus "each of your settlements must connect
    to at least 1 of your own roads"."""
    out = set()
    for sid, owner in enumerate(snap.settlement_owner):
        if owner != -1 or not distance_rule_ok(topo, snap, sid):
            continue
        if any(snap.road_owner[r] == pid for r in topo.py_sett_adj_roads[sid]):
            out.add(sid)
    return out


def legal_roads(topo, snap: Snapshot, pid: int, extra_road: int = -1) -> set[int]:
    """Vacant paths touching one of your roads, settlements or cities.

    A path reached only *through* an intersection an opponent occupies does not
    count -- your network does not continue past their building.
    """
    owned_roads = [r for r, o in enumerate(snap.road_owner) if o == pid]
    if extra_road >= 0:
        owned_roads.append(extra_road)

    reachable_nodes = set()
    for r in owned_roads:
        for sid in topo.py_road_adj_sett[r]:
            if sid >= 0 and snap.settlement_owner[sid] in (-1, pid):
                reachable_nodes.add(sid)
    for sid, owner in enumerate(snap.settlement_owner):
        if owner == pid:
            reachable_nodes.add(sid)

    out = set()
    for sid in reachable_nodes:
        for r in topo.py_sett_adj_roads[sid]:
            if snap.road_owner[r] == -1 and r != extra_road:
                out.add(r)
    return out


def legal_cities(snap: Snapshot, pid: int) -> set[int]:
    """"You may only establish a city by upgrading one of your settlements."""
    return {
        sid for sid, owner in enumerate(snap.settlement_owner)
        if owner == pid and snap.settlement_type[sid] == SETTLEMENT
    }


def legal_robber_hexes(topo, snap: Snapshot) -> set[int]:
    """"You must move the robber immediately to ... any other terrain hex"."""
    return {hid for hid in range(len(topo.py_hex_settlement)) if hid != snap.robber_hex}


def robbable_players(topo, snap: Snapshot, hid: int, pid: int) -> set[int]:
    """Opponents with a building on `hid`. Note the rulebook allows stealing
    from a player with no cards (you simply get nothing), so card count is not
    part of eligibility."""
    return {
        snap.settlement_owner[sid]
        for sid in topo.py_hex_settlement[hid]
        if snap.settlement_owner[sid] not in (-1, pid)
    }


# --- network connectivity ---------------------------------------------------

def network_components(topo, snap: Snapshot, pid: int,
                       respect_opponents: bool = False) -> list[set[int]]:
    """Connected components of a player's roads, as sets of road ids.

    By default this is *physical* adjacency: two roads are connected when they
    meet at an intersection, whoever is standing on it. That is the version
    with a useful invariant attached -- setup lays down two separate stretches
    and every later road must attach to one, so physically there are never more
    than two, and an opponent building on a junction later cannot change that.

    With `respect_opponents` the traversal stops at opponents' buildings, which
    is the connectivity that governs where you may *extend* next.
    """
    my_roads = [r for r, o in enumerate(snap.road_owner) if o == pid]
    if not my_roads:
        return []
    blocked = (
        {s for s, o in enumerate(snap.settlement_owner) if o != -1 and o != pid}
        if respect_opponents else set()
    )

    node_roads: dict[int, list[int]] = defaultdict(list)
    for r in my_roads:
        for sid in topo.py_road_adj_sett[r]:
            if sid >= 0 and sid not in blocked:
                node_roads[sid].append(r)

    parent = {r: r for r in my_roads}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for roads in node_roads.values():
        for other in roads[1:]:
            union(roads[0], other)

    groups: dict[int, set[int]] = defaultdict(set)
    for r in my_roads:
        groups[find(r)].add(r)
    return list(groups.values())
