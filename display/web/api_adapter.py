# api_adapter.py
"""Flattens a `GameState` into the JSON the web display consumes.

Two things worth knowing before editing this file:

1. **Index rotation.** The engine and the renderer number a hex's corners and
   edges from different starting points. The engine's `EDGE_OFFSETS` put edge 0
   at the up-right neighbour, while the renderer places its vertices at
   30/90/.../330 degrees, putting display edge 0 at the bottom-right. Working
   through both orderings gives a constant offset in each case:

       display_index = (engine_index + 4) % 6
       engine_index  = (display_index + 2) % 6

   Everything leaving this module is already in *display* order, so the
   frontend never has to know the engine's convention.

2. **Every slot is emitted, not just the occupied ones.** All 54 nodes and all
   72 edges go out with their engine ids attached, so the UI can label empty
   build sites and show what the topology thinks is adjacent to what.
"""

import numpy as np
from typing import Optional

from catan.ids import (
    RES2STR, PLY2STR, DEV2STR, EMPTY, CITY, N_RES, N_DEV_CARDS, N_PLAYERS,
    KNIGHT, VICTORY_POINT,
    ROADS_ALLOWED, SETTLEMENTS_ALLOWED, CITIES_ALLOWED,
    VICTORY_POINTS_REQUIRED, NONE_PLAYER,
)
from catan.state import GameState
from catan.interface import get_victory_points
from catan.actions import (
    PROMPT2STR, RESPONSE2STR, unpack_action,
    unpack_port_trade, unpack_table_trade,
    SETUP_RESPONSE, ROLL, PASS, BUILD_ROAD, BUILD_SETTLEMENT, BUILD_CITY,
    PURCHASE_DEV_CARD, PLAY_KNIGHT, PLAY_MONOPOLY, PLAY_YEAR_OF_PLENTY,
    PLAY_ROAD_BUILDER, PORT_TRADE, TABLE_TRADE_PROPOSE, TABLE_TRADE_SELECT,
    TABLE_TRADE_ACCEPT, TABLE_TRADE_REJECT, DISCARD_RESOURCE,
    SELECT_ROBBER_RESPONSE,
)

from pydantic import BaseModel
from typing import List, Literal, Dict

FastResource = Literal["wood", "brick", "sheep", "wheat", "ore", "desert"]
PortType = Literal["wood", "brick", "sheep", "wheat", "ore", "generic"]


def _to_display(engine_idx: int) -> int:
    return (engine_idx + 4) % 6


def _to_engine(display_idx: int) -> int:
    return (display_idx + 2) % 6


# --------------------------------------------------------------------------- #
#  Geometry / board detail                                                     #
# --------------------------------------------------------------------------- #

class PortLocation(BaseModel):
    hexId: int
    edge: int  # 0-5, display order


class SettlementLocation(BaseModel):
    hexId: int
    vertex: int  # 0-5, display order


class RoadLocation(BaseModel):
    hexId: int
    edge: int  # 0-5, display order


class HexState(BaseModel):
    id: int
    resource: Literal['wood', 'brick', 'sheep', 'wheat', 'ore', 'desert']
    number: Optional[int] = None
    hasRobber: bool = False
    # Engine ids of the six corners / six edges, in display order.
    nodes: List[int] = []
    edges: List[int] = []
    # 6 - |7 - number|, i.e. dots on the number token. 0 for desert.
    pips: int = 0


class NodeState(BaseModel):
    """One of the 54 intersections, occupied or not."""
    id: int
    owner: Optional[str] = None
    type: Literal['empty', 'settlement', 'city'] = 'empty'
    port: Optional[PortType] = None
    port_ratio: Optional[int] = None
    adj_hexes: List[int] = []
    adj_nodes: List[int] = []
    adj_roads: List[int] = []
    # Sum of pips on the hexes this node touches -- how productive the spot is.
    pip_total: int = 0
    location: SettlementLocation


class EdgeState(BaseModel):
    """One of the 72 road slots, occupied or not."""
    id: int
    owner: Optional[str] = None
    nodes: List[int] = []
    adj_hexes: List[int] = []
    adj_roads: List[int] = []
    location: RoadLocation


class PortState(BaseModel):
    id: int
    type: PortType
    ratio: int
    nodes: List[int] = []
    location: PortLocation


# Kept for compatibility with anything still reading the old shape.
class SettlementState(BaseModel):
    player: str
    type: Literal['settlement', 'city']
    location: SettlementLocation


class RoadState(BaseModel):
    player: str
    location: RoadLocation


# --------------------------------------------------------------------------- #
#  Players                                                                     #
# --------------------------------------------------------------------------- #

class PlayerPort(BaseModel):
    type: PortType
    ratio: int  # 2 or 3


class VPBreakdown(BaseModel):
    settlements: int = 0
    cities: int = 0
    longest_road: int = 0
    largest_army: int = 0
    dev_cards: int = 0     # hidden victory-point cards
    total: int = 0


class PlayerDisplayData(BaseModel):
    player_id: str                       # e.g. 'red', 'blue'
    index: int = 0
    is_current_player: bool = False      # holds the current prompt
    is_turn_player: bool = False         # whose turn it actually is
    is_winner: bool = False

    victory_points: int = 0              # true total (incl. hidden VP dev cards)
    victory_points_public: int = 0       # what opponents can see
    vp_breakdown: VPBreakdown = VPBreakdown()

    resources: Dict[FastResource, int]
    resource_total: int = 0

    dev_cards: Dict[str, int] = {}       # playable dev cards by type
    new_dev_cards: Dict[str, int] = {}   # bought this turn (not yet playable)
    dev_card_count: int = 0

    knights_played: int = 0
    longest_road_length: int = 0
    has_longest_road: bool = False
    has_largest_army: bool = False

    settlements_built: int = 0
    cities_built: int = 0
    roads_built: int = 0
    settlements_remaining: int = 0
    cities_remaining: int = 0
    roads_remaining: int = 0

    ports: List[PlayerPort] = []
    discards_required: int = 0

    # Board footprint, for the ownership overlay.
    owned_nodes: List[int] = []
    owned_edges: List[int] = []


# --------------------------------------------------------------------------- #
#  Global game info / legality / trade                                         #
# --------------------------------------------------------------------------- #

class MoveOption(BaseModel):
    """One playable move, decoded far enough to read at a glance."""
    action: str                      # RESPONSE2STR name, e.g. "BUILD_ROAD"
    text: str                        # "settlement on N12"
    raw: int                         # the packed action int, for copy/paste


class LegalMoves(BaseModel):
    """What the player holding the prompt may legally do, by board target."""
    settlement_nodes: List[int] = []
    city_nodes: List[int] = []
    road_edges: List[int] = []
    robber_hexes: List[int] = []
    setup_nodes: List[int] = []
    setup_edges: List[int] = []
    # Action name -> how many distinct moves of that kind are available.
    action_counts: Dict[str, int] = {}
    # Every playable move, decoded. Capped: road-builder and setup prompts can
    # run to thousands of pairs and the page only has to stay readable.
    moves: List[MoveOption] = []
    moves_truncated: int = 0
    total: int = 0
    error: Optional[str] = None


class TradeOffer(BaseModel):
    pending: bool = False
    proposer: Optional[str] = None
    give: Dict[FastResource, int] = {}      # proposer gives these away
    take: Dict[FastResource, int] = {}      # proposer wants these
    accepted_by: List[str] = []
    awaiting: Optional[str] = None          # who the prompt is waiting on


class GameInfo(BaseModel):
    turn_index: int = 0
    current_player: Optional[str] = None    # holds the prompt
    turn_player: Optional[str] = None       # whose turn it is
    phase: str = ""
    in_setup: bool = False
    setup_turn_idx: int = 0
    winner: Optional[str] = None
    vp_to_win: int = VICTORY_POINTS_REQUIRED
    has_rolled: bool = False
    dev_card_used: bool = False
    moves_played: int = 0

    bank: Dict[FastResource, int] = {}
    dev_cards_remaining: int = 0
    dev_deck_breakdown: Dict[str, int] = {}   # spoiler; UI hides it by default

    longest_road_owner: Optional[str] = None
    longest_road_length: int = 0
    largest_army_owner: Optional[str] = None
    largest_army_size: int = 0

    robber_hex: int = 0
    # Identifies the board layout, so the UI can tell a restart from a move.
    board_id: str = ""


class RunnerStatus(BaseModel):
    """What the process driving the game is doing right now."""
    connected: bool = False
    playing: bool = False
    delay: float = 0.5
    steps: int = 0
    max_steps: int = 0
    finished: bool = False
    seed: Optional[int] = None
    players: List[str] = []


class ExtendedBoardState(BaseModel):
    hexes: List[HexState]
    nodes: List[NodeState] = []
    edges: List[EdgeState] = []
    ports: List[PortState] = []

    # Occupied-only views, kept for compatibility.
    settlements: List[SettlementState] = []
    roads: List[RoadState] = []

    players_data: List[PlayerDisplayData] = []
    last_dice_roll: Optional[tuple[int, int]] = None
    game_info: Optional[GameInfo] = None
    legal: LegalMoves = LegalMoves()
    trade: TradeOffer = TradeOffer()
    move_log: List[dict] = []
    roll_counts: List[int] = []
    runner: RunnerStatus = RunnerStatus()


# --------------------------------------------------------------------------- #
#  Conversion                                                                  #
# --------------------------------------------------------------------------- #

def _decode_ports(ports_mask: int) -> List[PlayerPort]:
    """ports_mask: bit0 = 3:1 generic, bits 1..5 = 2:1 for resource (res+1)."""
    ports: List[PlayerPort] = []
    mask = int(ports_mask)
    if mask & 1:
        ports.append(PlayerPort(type="generic", ratio=3))
    for res in range(N_RES):
        if (mask >> (res + 1)) & 1:
            ports.append(PlayerPort(type=RES2STR[res], ratio=2))
    return ports


def _pips(number: Optional[int]) -> int:
    if not number:
        return 0
    return 6 - abs(7 - int(number))


# How many decoded moves to ship to the page. Setup and road-builder prompts
# enumerate pairs, so the list can run to thousands; the counts above still
# report the true total.
MAX_LISTED_MOVES = 400


def _describe_move(gs: GameState, a: int) -> str:
    """One readable line for a move that has *not* been played yet.

    movelog.py phrases moves from the state change they caused; this has only
    the packed action, so the two can't share code.
    """
    action, arg1, arg2 = unpack_action(a)

    if action == SETUP_RESPONSE:      return f"settle N{arg1}, road E{arg2}"
    if action == ROLL:                return "roll the dice"
    if action == PASS:                return "end turn"
    if action == BUILD_ROAD:          return f"road on E{arg1}"
    if action == BUILD_SETTLEMENT:    return f"settlement on N{arg1}"
    if action == BUILD_CITY:          return f"city on N{arg1}"
    if action == PURCHASE_DEV_CARD:   return "buy a development card"
    if action == PLAY_KNIGHT:         return "play knight"
    if action == PLAY_MONOPOLY:       return f"monopoly on {RES2STR[arg1]}"
    if action == PLAY_YEAR_OF_PLENTY: return f"year of plenty: {RES2STR[arg1]} + {RES2STR[arg2]}"
    if action == PLAY_ROAD_BUILDER:   return f"road building: E{arg1}, E{arg2}"
    if action == DISCARD_RESOURCE:    return f"discard 1 {RES2STR[arg1]}"
    if action == TABLE_TRADE_ACCEPT:  return "accept the offer"
    if action == TABLE_TRADE_REJECT:  return "reject the offer"

    if action == PORT_TRADE:
        give, rate, take = unpack_port_trade(a)
        return f"{rate} {RES2STR[give]} -> 1 {RES2STR[take]} ({rate}:1)"

    if action == TABLE_TRADE_PROPOSE:
        give, take = unpack_table_trade(a)
        return f"offer {_bag_phrase(give)} for {_bag_phrase(take)}"

    if action == TABLE_TRADE_SELECT:
        if arg1 >= N_PLAYERS:
            return "close the offer, trade with nobody"
        return f"trade with {PLY2STR[arg1].upper()}"

    if action == SELECT_ROBBER_RESPONSE:
        if arg2 == NONE_PLAYER or arg2 >= N_PLAYERS:
            return f"robber to H{arg1}, rob nobody"
        return f"robber to H{arg1}, rob {PLY2STR[arg2].upper()}"

    return f"arg1={arg1} arg2={arg2}"


def _bag_phrase(counts) -> str:
    parts = [f"{n} {RES2STR[i]}" for i, n in enumerate(counts) if n]
    return " + ".join(parts) if parts else "nothing"


def _legal_targets(gs: GameState) -> LegalMoves:
    """Decode `playable_moves` into board targets the UI can highlight."""
    from catan.engine import playable_moves  # local: avoids an import cycle

    legal = LegalMoves()
    try:
        moves = playable_moves(gs)
    except Exception as exc:           # an unknown prompt shouldn't kill the UI
        legal.error = f"{type(exc).__name__}: {exc}"
        return legal

    settlement_nodes, city_nodes, road_edges = set(), set(), set()
    robber_hexes, setup_nodes, setup_edges = set(), set(), set()
    counts: Dict[str, int] = {}

    for a in moves:
        action, arg1, arg2 = unpack_action(a)
        name = RESPONSE2STR[action] if action < len(RESPONSE2STR) else str(action)
        counts[name] = counts.get(name, 0) + 1

        if action == BUILD_SETTLEMENT:
            settlement_nodes.add(arg1)
        elif action == BUILD_CITY:
            city_nodes.add(arg1)
        elif action == BUILD_ROAD:
            road_edges.add(arg1)
        elif action == SELECT_ROBBER_RESPONSE:
            robber_hexes.add(arg1)
        elif action == SETUP_RESPONSE:
            setup_nodes.add(arg1)
            setup_edges.add(arg2)
        elif action == PLAY_ROAD_BUILDER:
            road_edges.add(arg1)
            road_edges.add(arg2)

    legal.settlement_nodes = sorted(settlement_nodes)
    legal.city_nodes = sorted(city_nodes)
    legal.road_edges = sorted(road_edges)
    legal.robber_hexes = sorted(robber_hexes)
    legal.setup_nodes = sorted(setup_nodes)
    legal.setup_edges = sorted(setup_edges)
    legal.action_counts = dict(sorted(counts.items()))
    legal.total = len(moves)
    legal.moves = [
        MoveOption(
            action=RESPONSE2STR[unpack_action(a)[0]] if unpack_action(a)[0] < len(RESPONSE2STR)
            else str(unpack_action(a)[0]),
            text=_describe_move(gs, a),
            raw=int(a),
        )
        for a in moves[:MAX_LISTED_MOVES]
    ]
    legal.moves_truncated = max(0, len(moves) - MAX_LISTED_MOVES)
    return legal


def _board_id(topo) -> str:
    """Cheap fingerprint of the layout, so the UI can detect a new game."""
    raw = (tuple(int(v) for v in topo.hex_resource)
           + tuple(int(v) for v in topo.hex_number)
           + tuple(int(v) for v in topo.port_res_id))
    return f"{hash(raw) & 0xFFFFFFFF:08x}"


def gamestate2api(
    gs: GameState,
    move_log: Optional[List[dict]] = None,
    roll_counts: Optional[List[int]] = None,
    runner: Optional[RunnerStatus] = None,
) -> ExtendedBoardState:
    topo = gs.topology
    board = gs.board

    n_hex = topo.hex_resource.shape[0]
    n_sett = topo.settlement_adj_roads.shape[0]
    n_road = topo.road_adj_settlement.shape[0]

    sett_owner = board.settlement_owner.tolist()
    sett_type = board.settlement_type.tolist()
    road_owner = board.road_owner.tolist()

    # ---------- Hexes ----------
    robber_h = int(board.robber_hex)
    hex_states: List[HexState] = []
    for h in range(n_hex):
        res_id = int(topo.hex_resource[h])            # 0..4, or 5 for desert
        resource = "desert" if res_id == 5 else RES2STR[res_id]
        number = int(topo.hex_number[h]) or None
        hex_states.append(HexState(
            id=h,
            resource=resource,
            number=number,
            hasRobber=(h == robber_h),
            nodes=[int(topo.hex_settlement_ix[h, _to_engine(d)]) for d in range(6)],
            edges=[int(topo.hex_road_ix[h, _to_engine(d)]) for d in range(6)],
            pips=_pips(number),
        ))

    # ---------- A (hex, display index) home for every node and edge ----------
    # Shared slots appear on 2-3 hexes; the first one found is as good as any.
    node_home: Dict[int, tuple[int, int]] = {}
    edge_home: Dict[int, tuple[int, int]] = {}
    for h in range(n_hex):
        for e_idx in range(6):
            s = int(topo.hex_settlement_ix[h, e_idx])
            if s >= 0:
                node_home.setdefault(s, (h, _to_display(e_idx)))
            r = int(topo.hex_road_ix[h, e_idx])
            if r >= 0:
                edge_home.setdefault(r, (h, _to_display(e_idx)))

    # ---------- Ports ----------
    port_bit_res = {}      # node id -> (type, ratio)
    port_states: List[PortState] = []
    n_port = topo.port_settlement_ix.shape[0]
    for p in range(n_port):
        sA, sB = map(int, topo.port_settlement_ix[p])
        res_id = int(topo.port_res_id[p])             # -1 generic, else 0..4
        api_type = "generic" if res_id == -1 else RES2STR[res_id]
        ratio = 3 if res_id == -1 else 2
        port_bit_res[sA] = (api_type, ratio)
        port_bit_res[sB] = (api_type, ratio)

        # The port sits on the edge joining its two nodes; find a hex carrying it.
        hex_found = edge_found = None
        for h in range(n_hex):
            vs = np.where(topo.hex_settlement_ix[h] == sA)[0]
            if vs.size == 0:
                continue
            v = int(vs[0])
            if int(topo.hex_settlement_ix[h, (v + 1) % 6]) == sB:
                hex_found, edge_found = h, v
                break
            if int(topo.hex_settlement_ix[h, (v - 1) % 6]) == sB:
                hex_found, edge_found = h, (v - 1) % 6
                break
        if hex_found is None:
            continue

        port_states.append(PortState(
            id=p, type=api_type, ratio=ratio, nodes=[sA, sB],
            location=PortLocation(hexId=hex_found, edge=_to_display(edge_found)),
        ))

    # ---------- Nodes (all 54) ----------
    hex_pips = [hs.pips for hs in hex_states]
    node_states: List[NodeState] = []
    settlement_states: List[SettlementState] = []
    owned_nodes: List[List[int]] = [[] for _ in range(N_PLAYERS)]

    for s in range(n_sett):
        owner = sett_owner[s]
        typ = sett_type[s]
        h, v = node_home.get(s, (0, 0))
        loc = SettlementLocation(hexId=h, vertex=v)

        adj_hexes = list(topo.py_sett_adj_hex[s])
        port = port_bit_res.get(s)

        if owner >= 0 and typ != EMPTY:
            type_str = "city" if typ == CITY else "settlement"
            owned_nodes[owner].append(s)
            settlement_states.append(SettlementState(
                player=PLY2STR[owner], type=type_str, location=loc))
        else:
            type_str = "empty"

        node_states.append(NodeState(
            id=s,
            owner=PLY2STR[owner] if owner >= 0 and typ != EMPTY else None,
            type=type_str,
            port=port[0] if port else None,
            port_ratio=port[1] if port else None,
            adj_hexes=adj_hexes,
            adj_nodes=list(topo.py_sett_adj_sett[s]),
            adj_roads=list(topo.py_sett_adj_roads[s]),
            pip_total=sum(hex_pips[hh] for hh in adj_hexes),
            location=loc,
        ))

    # ---------- Edges (all 72) ----------
    edge_states: List[EdgeState] = []
    road_states: List[RoadState] = []
    owned_edges: List[List[int]] = [[] for _ in range(N_PLAYERS)]

    for r in range(n_road):
        owner = road_owner[r]
        h, e = edge_home.get(r, (0, 0))
        loc = RoadLocation(hexId=h, edge=e)

        if owner >= 0:
            owned_edges[owner].append(r)
            road_states.append(RoadState(player=PLY2STR[owner], location=loc))

        edge_states.append(EdgeState(
            id=r,
            owner=PLY2STR[owner] if owner >= 0 else None,
            nodes=[int(v) for v in topo.road_adj_settlement[r]],
            adj_hexes=[int(v) for v in topo.road_adj_hex[r] if v >= 0],
            adj_roads=[int(v) for v in topo.road_adj_roads[r] if v >= 0],
            location=loc,
        ))

    # ---------- Players ----------
    lr_owner = int(gs.longest_road_owner)
    la_owner = int(gs.largest_army_owner)
    winner = int(gs.winner)
    turn_pid = int(gs.current_player_turn_idx)
    prompt_pid = int(gs.current_player_idx)

    players_api: List[PlayerDisplayData] = []
    for pid, p in enumerate(gs.players):
        playable_dev = {DEV2STR[i]: int(p.dev_cards[i]) for i in range(N_DEV_CARDS)}
        new_dev = {DEV2STR[i]: int(p.new_dev_cards[i]) for i in range(N_DEV_CARDS)}
        dev_total = int(p.dev_cards.sum()) + int(p.new_dev_cards.sum())

        vp_true = int(get_victory_points(gs, pid))
        hidden_vp = int(p.dev_cards[VICTORY_POINT]) + int(p.new_dev_cards[VICTORY_POINT])

        breakdown = VPBreakdown(
            settlements=int(p.settlements_built),
            cities=int(p.cities_built) * 2,
            longest_road=2 if lr_owner == pid else 0,
            largest_army=2 if la_owner == pid else 0,
            dev_cards=hidden_vp,
            total=vp_true,
        )

        players_api.append(PlayerDisplayData(
            player_id=PLY2STR[pid],
            index=pid,
            is_current_player=(pid == prompt_pid),
            is_turn_player=(pid == turn_pid),
            is_winner=(pid == winner),
            victory_points=vp_true,
            victory_points_public=vp_true - hidden_vp,
            vp_breakdown=breakdown,
            resources={RES2STR[i]: int(p.hand[i]) for i in range(N_RES)},
            resource_total=int(p.hand.sum()),
            dev_cards=playable_dev,
            new_dev_cards=new_dev,
            dev_card_count=dev_total,
            knights_played=int(p.used_knights),
            longest_road_length=int(p.longest_road_len),
            has_longest_road=(lr_owner == pid),
            has_largest_army=(la_owner == pid),
            settlements_built=int(p.settlements_built),
            cities_built=int(p.cities_built),
            roads_built=int(p.roads_built),
            # `settlements_built` already drops by one on a city upgrade, which
            # mirrors getting the settlement piece back into your supply.
            settlements_remaining=SETTLEMENTS_ALLOWED - int(p.settlements_built),
            cities_remaining=CITIES_ALLOWED - int(p.cities_built),
            roads_remaining=ROADS_ALLOWED - int(p.roads_built),
            ports=_decode_ports(p.ports_mask),
            discards_required=int(p.discards_required),
            owned_nodes=owned_nodes[pid],
            owned_edges=owned_edges[pid],
        ))

    # ---------- Global info ----------
    prompt_idx = int(gs.prompt)
    phase = PROMPT2STR[prompt_idx] if 0 <= prompt_idx < len(PROMPT2STR) else str(prompt_idx)

    lr_len = int(gs.players[lr_owner].longest_road_len) if lr_owner != NONE_PLAYER else 0
    la_size = int(gs.players[la_owner].used_knights) if la_owner != NONE_PLAYER else 0

    deck_breakdown: Dict[str, int] = {}
    for card in board.dev_deck.tolist():
        name = DEV2STR[int(card)]
        deck_breakdown[name] = deck_breakdown.get(name, 0) + 1

    game_info = GameInfo(
        turn_index=int(gs.turn_index),
        current_player=PLY2STR[prompt_pid],
        turn_player=PLY2STR[turn_pid],
        phase=phase,
        in_setup=bool(gs.in_setup),
        setup_turn_idx=int(gs.setup_turn_idx),
        winner=(PLY2STR[winner] if winner != NONE_PLAYER else None),
        vp_to_win=VICTORY_POINTS_REQUIRED,
        has_rolled=bool(gs.has_rolled),
        dev_card_used=bool(gs.dev_card_used),
        moves_played=len(gs.action_log),
        bank={RES2STR[i]: int(board.bank_res[i]) for i in range(N_RES)},
        dev_cards_remaining=int(len(board.dev_deck)),
        dev_deck_breakdown=deck_breakdown,
        longest_road_owner=(PLY2STR[lr_owner] if lr_owner != NONE_PLAYER else None),
        longest_road_length=lr_len,
        largest_army_owner=(PLY2STR[la_owner] if la_owner != NONE_PLAYER else None),
        largest_army_size=la_size,
        robber_hex=robber_h,
        board_id=_board_id(topo),
    )

    # ---------- Trade on the table ----------
    offer_from = int(gs.trade_offer_from)
    trade = TradeOffer()
    if offer_from >= 0:
        accept_mask = gs.trade_accept_mask
        trade = TradeOffer(
            pending=True,
            proposer=PLY2STR[offer_from],
            give={RES2STR[i]: int(gs.trade_offer_give[i]) for i in range(N_RES)},
            take={RES2STR[i]: int(gs.trade_offer_take[i]) for i in range(N_RES)},
            accepted_by=[PLY2STR[i] for i in range(N_PLAYERS) if bool(accept_mask[i])],
            awaiting=PLY2STR[prompt_pid],
        )

    return ExtendedBoardState(
        hexes=hex_states,
        nodes=node_states,
        edges=edge_states,
        ports=port_states,
        settlements=settlement_states,
        roads=road_states,
        players_data=players_api,
        last_dice_roll=gs.last_dice_roll,
        game_info=game_info,
        legal=_legal_targets(gs),
        trade=trade,
        move_log=move_log or [],
        roll_counts=roll_counts or [],
        runner=runner or RunnerStatus(),
    )
