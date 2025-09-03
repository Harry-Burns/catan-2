import numpy as np
from dataclasses import replace

from catan.ids import WOOD,BRICK,SHEEP,WHEAT,ORE, DESERT, N_RES, N_PLAYERS, EMPTY
from catan.ids import KNIGHT, YEAR_OF_PLENTY, MONOPOLY, ROAD_BUILDER, VICTORY_POINT
from catan.actions import SETUP_TURN

from catan.topology import generate_topology
from catan.state import PlayerState, BoardState, GameState

HEX_RING = np.array([0,1,2,6,11,15,18,17,16,12,7,3,4,5,10,14,13,8,9], dtype=np.int16)
PORT_LOCATIONS = np.array([(0,5),(1,0),(6,0),(11,1),(15,2),(17,2),(16,3),(12,4),(3,4)], dtype=np.int16)  # (settlement_id,road_id)

RESOURCE_DIST = np.array([*( [WOOD]*4 ), *( [WHEAT]*4 ), *( [SHEEP]*4 ), *( [BRICK]*3 ), *( [ORE]*3 ), DESERT], dtype=np.int8)
NUMBER_DIST = np.array([5,2,6,3,8,10,9,12,11,4,8,10,9,4,5,6,3,11][::-1], dtype=np.int8)

PORT_TYPES = np.array([-1,-1,-1,-1, WOOD,BRICK,SHEEP,WHEAT,ORE], dtype=np.int8)  # -1 means 3:1 any
DEV_DIST = np.array([KNIGHT]*14 + [YEAR_OF_PLENTY]*2 + [ROAD_BUILDER]*2 + [MONOPOLY]*2 + [VICTORY_POINT]*5, dtype=np.uint8)

BANK_RESOURCE_STOCK = 19


def _shuffle_copy(arr: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = arr.copy(); rng.shuffle(out); return out

def _place_numbers_ring(hex_res: np.ndarray) -> np.ndarray:
    out = np.zeros_like(hex_res, dtype=np.int8)
    nums = NUMBER_DIST.copy()
    i = 0
    for h in HEX_RING:
        if hex_res[h] != 5: 
            out[h] = nums[i]
            i += 1
    return out

def initialize_game(rng: np.random.Generator) -> GameState:
    topo_template = generate_topology()

    # randomize per-game arrays
    hex_res  = _shuffle_copy(RESOURCE_DIST, rng)
    port_ids = _shuffle_copy(PORT_TYPES, rng)
    hex_num  = _place_numbers_ring(hex_res)

    # create a NEW frozen topology with swapped-in arrays
    topo = replace(
        topo_template,
        hex_resource=hex_res,
        hex_number=hex_num,
        port_res_id=port_ids,
    )

    # mutable board + players
    n_sett = topo.settlement_adj_roads.shape[0]
    n_road = topo.road_adj_settlement.shape[0]
    robber_hex = int(np.where(hex_res == 5)[0][0])

    dev_deck = _shuffle_copy(DEV_DIST, rng)

    board = BoardState(
        road_owner=np.full(n_road, -1, dtype=np.int8),
        settlement_owner=np.full(n_sett, -1, dtype=np.int8),
        settlement_type=np.full(n_sett, EMPTY, dtype=np.uint8),
        robber_hex=np.uint16(robber_hex),
        dev_deck=dev_deck,
        bank_res=np.full(N_RES, BANK_RESOURCE_STOCK, dtype=np.int16),
    )

    players = [
        PlayerState(
            hand=np.zeros(N_RES, dtype=np.int8),
            ports_mask=np.uint8(0),
            dev_cards=np.zeros(5, dtype=np.int8),
            new_dev_cards=np.zeros(5, dtype=np.int8),
            used_knights=np.uint8(0),
            longest_road_len=np.uint8(0),
            discards_required=np.uint8(0),
            roads_built=np.uint8(0),
            settlements_built=np.uint8(0),
            cities_built=np.uint8(0),
        )
        for _ in range(N_PLAYERS)
    ]

    return GameState(
        topology=topo,
        board=board,
        players=players,
        current_player_idx=np.uint8(0),
        current_player_turn_idx=np.uint8(0),
        in_setup=True,
        setup_turn_idx=np.int16(0),
        trade_offer_from=-1,
        trade_offer_give=None,
        trade_offer_take=None,
        trade_accept_mask=None,
        has_rolled=False,
        dev_card_used=False,
        winner=-1,
        turn_index=np.int32(0),
        prompt=np.uint8(SETUP_TURN),
        action_log=[],
    )