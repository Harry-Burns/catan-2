# api_adapter.py
import numpy as np
from typing import Optional

from catan.ids import (
    RES2STR, PLY2STR, DEV2STR, EMPTY, CITY, N_RES, N_DEV_CARDS,
    KNIGHT, VICTORY_POINT,
    ROADS_ALLOWED, SETTLEMENTS_ALLOWED, CITIES_ALLOWED,
    VICTORY_POINTS_REQUIRED, NONE_PLAYER,
)
from catan.state import GameState
from catan.interface import get_victory_points
from catan.actions import PROMPT2STR

from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict

FastResource = Literal["wood", "brick", "sheep", "wheat", "ore", "desert"]

class PortLocation(BaseModel):
    hexId: int
    edge: int  # 0-5

class SettlementLocation(BaseModel):
    hexId: int
    vertex: int # 0-5

class RoadLocation(BaseModel):
    hexId: int
    edge: int # 0-5

class HexState(BaseModel):
    id: int
    resource: Literal['wood', 'brick', 'sheep', 'wheat', 'ore', 'desert']
    number: Optional[int] = None
    hasRobber: bool = False

class PortState(BaseModel):
    type: Literal['wood', 'brick', 'sheep', 'wheat', 'ore', 'generic']
    ratio: int
    location: PortLocation

class SettlementState(BaseModel):
    player: str
    type: Literal['settlement', 'city']
    location: SettlementLocation

class RoadState(BaseModel):
    player: str
    location: RoadLocation

# A single port a player has access to (for the player panel).
class PlayerPort(BaseModel):
    type: Literal['wood', 'brick', 'sheep', 'wheat', 'ore', 'generic']
    ratio: int  # 2 or 3

# Rich per-player display data.
class PlayerDisplayData(BaseModel):
    player_id: str                       # e.g. 'red', 'blue'
    is_current_player: bool = False
    is_winner: bool = False

    # Victory points
    victory_points: int = 0              # true total (incl. hidden VP dev cards)
    victory_points_public: int = 0       # what opponents can see (excludes hidden VP cards)

    # Resources
    resources: Dict[FastResource, int]   # e.g. {'wood': 2, 'brick': 1, ...}
    resource_total: int = 0

    # Development cards
    dev_cards: Dict[str, int] = {}       # playable dev cards by type
    new_dev_cards: Dict[str, int] = {}   # bought this turn (not yet playable)
    dev_card_count: int = 0              # grand total of all dev cards held

    # Awards / army / roads
    knights_played: int = 0
    longest_road_length: int = 0
    has_longest_road: bool = False
    has_largest_army: bool = False

    # Buildings (built / limit / remaining)
    settlements_built: int = 0
    cities_built: int = 0
    roads_built: int = 0
    settlements_remaining: int = 0
    cities_remaining: int = 0
    roads_remaining: int = 0

    # Ports the player can trade through
    ports: List[PlayerPort] = []

    # Misc
    discards_required: int = 0

# Global game / board information.
class GameInfo(BaseModel):
    turn_index: int = 0
    current_player: Optional[str] = None
    phase: str = ""
    in_setup: bool = False
    winner: Optional[str] = None
    vp_to_win: int = VICTORY_POINTS_REQUIRED

    bank: Dict[FastResource, int] = {}
    dev_cards_remaining: int = 0

    longest_road_owner: Optional[str] = None
    longest_road_length: int = 0
    largest_army_owner: Optional[str] = None
    largest_army_size: int = 0

# Extended BoardState model
class ExtendedBoardState(BaseModel): # Renamed to avoid conflict if old BoardState is used elsewhere
    hexes: List[HexState]
    settlements: List[SettlementState] = []
    roads: List[RoadState] = []
    ports: List[PortState] = []
    # Player info, dice roll and global game info
    players_data: List[PlayerDisplayData] = []
    last_dice_roll: Optional[tuple[int, int]] = None
    game_info: Optional[GameInfo] = None


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


def gamestate2api(gs: GameState) -> ExtendedBoardState:
    topo = gs.topology
    board = gs.board

    # ---------- Hexes ----------
    robber_h = int(board.robber_hex)
    hex_states = []
    for h in range(topo.hex_resource.shape[0]):
        res_id = int(topo.hex_resource[h])  # 0..4 or 5 (desert)
        resource = "desert" if res_id == 5 else RES2STR[res_id]
        number = int(topo.hex_number[h]) if topo.hex_number[h] != 0 else None
        hex_states.append(
            HexState(
                id=h, resource=resource, number=number, hasRobber=(h == robber_h)
            )
        )

    # ---------- Precompute primary (hex,vertex) for each settlement ----------
    n_sett = topo.settlement_adj_roads.shape[0]
    sett_primary_hex = np.full(n_sett, -1, dtype=np.int16)
    sett_primary_vtx = np.full(n_sett, -1, dtype=np.int8)
    for h in range(topo.hex_settlement_ix.shape[0]):
        for v in range(6):
            s = int(topo.hex_settlement_ix[h, v])
            if s >= 0 and sett_primary_hex[s] == -1:
                sett_primary_hex[s] = h
                sett_primary_vtx[s] = v

    # ---------- Settlements (owned only) ----------
    settlement_states = []
    for s in range(n_sett):
        owner = int(board.settlement_owner[s])
        typ   = int(board.settlement_type[s])
        if owner < 0 or typ == EMPTY:
            continue
        player_str = PLY2STR[owner]
        stype_str  = "city" if typ == CITY else "settlement"
        h = int(sett_primary_hex[s])
        v = int(sett_primary_vtx[s])
        settlement_states.append(
            SettlementState(
                player=player_str,
                type=stype_str,
                location=SettlementLocation(hexId=h, vertex=(v + 4) % 6)
            )
        )

    # ---------- Roads (owned only) ----------
    road_states = []
    n_road = topo.road_adj_settlement.shape[0]
    for r in range(n_road):
        owner = int(board.road_owner[r])
        if owner < 0:
            continue
        # choose a touching hex then find which edge on that hex
        h0 = int(topo.road_adj_hex[r, 0])
        e_idx = int(np.where(topo.hex_road_ix[h0] == r)[0][0])
        road_states.append(
            RoadState(
                player=PLY2STR[owner],
                location=RoadLocation(hexId=h0, edge=(e_idx + 4) % 6)
            )
        )

    # ---------- Ports ----------
    # Need a (hex, edge) location for each port. We have its two corner settlements.
    # Find a hex where those two corners are adjacent.
    port_states = []
    n_port = topo.port_settlement_ix.shape[0]
    for p in range(n_port):
        sA, sB = map(int, topo.port_settlement_ix[p])
        # Try hexes that touch sA; check if sB is at v+/-1 there.
        hex_found: Optional[int] = None
        edge_found: Optional[int] = None
        # iterate all hexes; early exit when found
        for h in range(topo.hex_settlement_ix.shape[0]):
            # positions where sA appears on this hex (usually 0 or 1)
            vs = np.where(topo.hex_settlement_ix[h] == sA)[0]
            if vs.size == 0:
                continue
            v = int(vs[0])
            v_plus  = (v + 1) % 6
            v_minus = (v - 1) % 6
            if int(topo.hex_settlement_ix[h, v_plus]) == sB:
                hex_found, edge_found = h, v  # edge is between v and v+1 → index v
                break
            if int(topo.hex_settlement_ix[h, v_minus]) == sB:
                hex_found, edge_found = h, v_minus  # edge is between v-1 and v → index v-1
                break

        if hex_found is None:
            # fallback: use road endpoints mapping (shouldn't happen if topology is wired)
            continue

        res_id = int(topo.port_res_id[p])  # -1 generic, else 0..4
        api_type = "generic" if res_id == -1 else RES2STR[res_id]
        ratio = 3 if res_id == -1 else 2

        port_states.append(
            PortState(
                type=api_type,
                ratio=ratio,
                location=PortLocation(hexId=int(hex_found), edge=int((edge_found + 4) % 6))
            )
        )

    # ---------- Players (rich display) ----------
    lr_owner = int(gs.longest_road_owner)
    la_owner = int(gs.largest_army_owner)
    winner = int(gs.winner)

    players_api = []
    for pid, p in enumerate(gs.players):
        playable_dev = {DEV2STR[i]: int(p.dev_cards[i]) for i in range(N_DEV_CARDS)}
        new_dev = {DEV2STR[i]: int(p.new_dev_cards[i]) for i in range(N_DEV_CARDS)}
        dev_total = int(p.dev_cards.sum()) + int(p.new_dev_cards.sum())

        vp_true = int(get_victory_points(gs, pid))
        hidden_vp = int(p.dev_cards[VICTORY_POINT]) + int(p.new_dev_cards[VICTORY_POINT])
        vp_public = vp_true - hidden_vp

        players_api.append(
            PlayerDisplayData(
                player_id=PLY2STR[pid],
                is_current_player=(pid == int(gs.current_player_idx)),
                is_winner=(pid == winner),
                victory_points=vp_true,
                victory_points_public=vp_public,
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
                settlements_remaining=SETTLEMENTS_ALLOWED - int(p.settlements_built),
                cities_remaining=CITIES_ALLOWED - int(p.cities_built),
                roads_remaining=ROADS_ALLOWED - int(p.roads_built),
                ports=_decode_ports(p.ports_mask),
                discards_required=int(p.discards_required),
            )
        )

    # ---------- Global game info ----------
    prompt_idx = int(gs.prompt)
    phase = PROMPT2STR[prompt_idx] if 0 <= prompt_idx < len(PROMPT2STR) else str(prompt_idx)

    lr_len = int(gs.players[lr_owner].longest_road_len) if lr_owner != NONE_PLAYER else 0
    la_size = int(gs.players[la_owner].used_knights) if la_owner != NONE_PLAYER else 0

    game_info = GameInfo(
        turn_index=int(gs.turn_index),
        current_player=PLY2STR[int(gs.current_player_idx)],
        phase=phase,
        in_setup=bool(gs.in_setup),
        winner=(PLY2STR[winner] if winner != NONE_PLAYER else None),
        vp_to_win=VICTORY_POINTS_REQUIRED,
        bank={RES2STR[i]: int(board.bank_res[i]) for i in range(N_RES)},
        dev_cards_remaining=int(len(board.dev_deck)),
        longest_road_owner=(PLY2STR[lr_owner] if lr_owner != NONE_PLAYER else None),
        longest_road_length=lr_len,
        largest_army_owner=(PLY2STR[la_owner] if la_owner != NONE_PLAYER else None),
        largest_army_size=la_size,
    )

    # ---------- Optional last dice (if you store it) ----------
    last_roll = getattr(gs, "last_dice_roll_values", None)

    return ExtendedBoardState(
        hexes=hex_states,
        settlements=settlement_states,
        roads=road_states,
        ports=port_states,
        players_data=players_api,
        last_dice_roll=last_roll,
        game_info=game_info,
    )
