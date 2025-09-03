# api_adapter.py
import numpy as np
from typing import Optional

from catan.ids import RES2STR, PLY2STR, EMPTY, CITY, N_RES
from catan.state import GameState

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

# New model for displaying player data in the API
class PlayerDisplayData(BaseModel):
    player_id: str  # e.g., 'red', 'blue'
    resources: Dict[FastResource, int]  # e.g., {'wood': 2, 'brick': 1, ...}
    dev_card_count: int
    is_current_player: bool = False

# Extended BoardState model
class ExtendedBoardState(BaseModel): # Renamed to avoid conflict if old BoardState is used elsewhere
    hexes: List[HexState]
    settlements: List[SettlementState] = []
    roads: List[RoadState] = []
    ports: List[PortState] = []
    # New fields for player info and dice roll
    players_data: List[PlayerDisplayData] = []
    last_dice_roll: Optional[tuple[int, int]] = None
    # You could add more global game state info here if needed, e.g.:
    # current_turn_player_id: Optional[str] = None
    # game_over: bool = False




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

    # ---------- Players (public display) ----------
    players_api = []
    for pid, p in enumerate(gs.players):
        players_api.append(
            PlayerDisplayData(
                player_id=PLY2STR[pid],
                resources={RES2STR[i]: int(p.hand[i]) for i in range(N_RES)},
                dev_card_count=int(np.int32(p.dev_cards.sum())),
                is_current_player=(pid == int(gs.current_player_idx)),
            )
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
    )