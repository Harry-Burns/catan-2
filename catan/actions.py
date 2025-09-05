from typing import Final, Tuple

## --- ActionPrompt

SETUP_TURN: Final[int]   = 0   # place opening settlement+road
PLAY_PRETURN: Final[int] = 1   # before rolling (dev cards playable)
PLAY_TURN: Final[int]    = 2   # after rolling (build/trade/dev/pass)
MOVE_ROBBER: Final[int]  = 3   # choose hex & victim
DISCARD: Final[int]      = 4   # discard one resource when required
DECIDE_TRADE: Final[int] = 5   # other players accept/reject an offer
DECIDE_ACCEPTEES: Final[int] = 6  # proposer picks among acceptors

PROMPT2STR: Final[list[str]] = [
    "SETUP_TURN", "PLAY_PRETURN", "PLAY_TURN",
    "MOVE_ROBBER", "DISCARD", "DECIDE_TRADE", "DECIDE_ACCEPTEES"
]


## --- ActionResponse

# Setup Turn
SETUP_RESPONSE: Final[int]          = 0   # arg1=settlement_id, arg2=road_id

# Preturn
ROLL: Final[int]                    = 1   # -
PLAY_KNIGHT: Final[int]             = 2   # arg1=hex_id, arg2=victim_player_id (0..3, or 15 for none)
PLAY_MONOPOLY: Final[int]           = 3   # arg1=resource_id (0..4)
PLAY_YEAR_OF_PLENTY: Final[int]     = 4   # arg1=res1_id, arg2=res2_id
PLAY_ROAD_BUILDER: Final[int]       = 5   # arg1=road_id1, arg2=road_id2

# Turn
PASS: Final[int]                    = 6   # -
BUILD_ROAD: Final[int]              = 7   # arg1=road_id
BUILD_SETTLEMENT: Final[int]        = 8   # arg1=settlement_id
BUILD_CITY: Final[int]              = 9   # arg1=settlement_id
PURCHASE_DEV_CARD: Final[int]       = 10  # -


# Trading
PORT_TRADE: Final[int]              = 11  # arg1=(give_res<<4)|rate_k (k∈{2,3,4}), arg2=take_res
TABLE_TRADE_PROPOSE: Final[int]    = 12  # arg1=candidate_index (into per-node candidate list)
TABLE_TRADE_SELECT: Final[int]     = 13  # arg1=player_id (0..3)

TABLE_TRADE_ACCEPT: Final[int]     = 14  # no args
TABLE_TRADE_REJECT: Final[int]     = 15  # no args

# Robber-based
DISCARD_RESOURCE: Final[int]        = 16  # arg1=resource_id
SELECT_ROBBER_RESPONSE: Final[int]  = 17  # arg1=hex_id, arg2=victim_player_id

RESPONSE2STR = [
    "SETUP_RESPONSE","ROLL","PLAY_KNIGHT","PLAY_MONOPOLY","PLAY_YEAR_OF_PLENTY",
    "PLAY_ROAD_BUILDER","PASS","BUILD_ROAD","BUILD_SETTLEMENT","BUILD_CITY",
    "PURCHASE_DEV_CARD","PORT_TRADE","TABLE_TRADE_PROPOSE","TABLE_TRADE_SELECT",
    "TABLE_TRADE_ACCEPT","TABLE_TRADE_REJECT","DISCARD_RESOURCE","SELECT_ROBBER_RESPONSE"
]


## --- Shortcuts
LONGEST_ROAD_ACTIONS = [BUILD_ROAD, PLAY_ROAD_BUILDER, BUILD_SETTLEMENT]
LARGEST_ARMY_ACTIONS = [PLAY_KNIGHT]
VICTORY_POINT_ACTIONS = [BUILD_CITY, PURCHASE_DEV_CARD] + LONGEST_ROAD_ACTIONS + LARGEST_ARMY_ACTIONS


## -- Action Functions - Packing actions & params into single ints
def pack_action(action: int, arg1: int = 0, arg2: int = 0) -> int:
    return (action << 24) | ((arg1 & 0xFFF) << 12) | (arg2 & 0xFFF)

def unpack_action(a: int) -> Tuple[int, int, int]:
    return (a >> 24) & 0xFF, (a >> 12) & 0xFFF, a & 0xFFF

def get_response(a: int) -> int: return (a>>24)&0xFF
def get_arg(a:int)->int:  return a&0xFFFFFF
def get_arg2(a:int)->int: return a&0xFFF


# Setup Turn
def act_setup(settlement_id: int, road_id: int) -> int: return pack_action(SETUP_RESPONSE, settlement_id, road_id)

# Preturn
def act_roll() -> int: return pack_action(ROLL)

def act_play_knight() -> int: return pack_action(PLAY_KNIGHT)
def act_play_monopoly(resource_id: int) -> int: return pack_action(PLAY_MONOPOLY, resource_id)
def act_play_yop(res1_id: int, res2_id: int) -> int: return pack_action(PLAY_YEAR_OF_PLENTY, res1_id, res2_id)
def act_play_road_builder(road_id1: int, road_id2: int) -> int: return pack_action(PLAY_ROAD_BUILDER, road_id1, road_id2)

# Turn
def act_pass() -> int: return pack_action(PASS)

def act_build_road(road_id: int) -> int: return pack_action(BUILD_ROAD, road_id)
def act_build_settlement(settlement_id: int) -> int: return pack_action(BUILD_SETTLEMENT, settlement_id)
def act_build_city(settlement_id: int) -> int: return pack_action(BUILD_CITY, settlement_id)
def act_purchase_dev() -> int: return pack_action(PURCHASE_DEV_CARD)

# Robber-based
def act_discard_resource(resource_id: int) -> int: return pack_action(DISCARD_RESOURCE, resource_id)
def act_select_robber_response(hex_id: int, victim_player_id: int) -> int: return pack_action(SELECT_ROBBER_RESPONSE, hex_id, victim_player_id & 0xF)

# Trading
def pack_table_trade(give: list[int], take: list[int]) -> int:
    # give/take = length-5 arrays with values 0..3
    g = sum((int(give[i]) & 0b11) << (i*2) for i in range(5))  # 10 bits
    t = sum((int(take[i]) & 0b11) << (i*2) for i in range(5))  # 10 bits
    arg = (g << 10) | t  # 20 bits total
    return (TABLE_TRADE_PROPOSE << 24) | (arg & 0xFFFFFF)  # store in low 24 bits

def unpack_table_trade(a: int) -> tuple[list[int], list[int]]:
    arg = a & 0xFFFFFF
    g = (arg >> 10) & 0x3FF
    t = arg & 0x3FF
    give = [(g >> (i*2)) & 0b11 for i in range(5)]
    take = [(t >> (i*2)) & 0b11 for i in range(5)]
    return give, take

def act_table_trade_propose(give:list[int], take:list[int])->int: return pack_table_trade(give,take)
def act_table_trade_select(player_id:int)->int: return pack_action(TABLE_TRADE_SELECT, player_id&0xFFF)
def act_table_trade_accept()->int: return pack_action(TABLE_TRADE_ACCEPT)
def act_table_trade_reject()->int: return pack_action(TABLE_TRADE_REJECT)

def act_port_trade(give_res:int, rate_k:int, take_res:int) -> int:
    arg = ((give_res & 0xF) << 8) | ((rate_k & 0xF) << 4) | (take_res & 0xF)
    return pack_action(PORT_TRADE, 0, arg)

def unpack_port_trade(a:int) -> tuple[int,int,int]:
    arg  = get_arg2(a)                 # <-- read arg2, where we packed it
    give = (arg >> 8) & 0xF
    rate = (arg >> 4) & 0xF
    take = arg & 0xF
    return give, rate, take




