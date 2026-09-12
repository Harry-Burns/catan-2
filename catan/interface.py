import numpy as np

from catan.state import GameState

from catan.actions import (
    DISCARD, MOVE_ROBBER, PLAY_PRETURN, SETUP_TURN, BUILD_SETTLEMENT, BUILD_ROAD, PLAY_ROAD_BUILDER,

    act_setup, act_table_trade_propose,
    unpack_action,

    ACT_PLAY_KNIGHT, ACT_PURCHASE_DEV, ACT_TRADE_ACCEPT, ACT_TRADE_REJECT,
    MONOPOLY_TABLE, YOP_TABLE_FLAT, ROAD_BUILDER_TABLE,
    BUILD_ROAD_TABLE, BUILD_SETTLEMENT_TABLE, BUILD_CITY_TABLE,
    SELECT_ROBBER_TABLE, TABLE_TRADE_SELECT_TABLE, PORT_TRADE_TABLE,
)

from catan.ids import (
    N_PLAYERS, N_RES, COSTS, NONE_PLAYER,
    DESERT, SETTLEMENT, CITY, 
    KNIGHT, MONOPOLY, YEAR_OF_PLENTY, ROAD_BUILDER, VICTORY_POINT,
    ROADS_ALLOWED, SETTLEMENTS_ALLOWED, CITIES_ALLOWED, VICTORY_POINTS_REQUIRED,
    OBJ_ROAD, OBJ_SETTLEMENT, OBJ_CITY, OBJ_DEV, BANK_STOCK
)


### ---------------------------- ---------------
### --- Player Helper Functions  ---------------
### ---------------------------- ---------------
def get_total_cards(gs: GameState, pid: int) -> int:
    return sum(gs.players[pid].hand.tolist())

def get_random_card(gs: GameState, rng: np.random.Generator, pid: int):
    hand = gs.players[pid].hand; 
    total_cards = int(hand.sum())
    return rng.choice(N_RES, p=hand / total_cards) if total_cards > 0 else -1
    
def get_victory_points(gs: GameState, pid: int) -> int:
    vps = gs.players[pid].settlements_built + gs.players[pid].cities_built * 2
    vps += gs.players[pid].dev_cards[VICTORY_POINT] + gs.players[pid].new_dev_cards[VICTORY_POINT]

    if gs.longest_road_owner == pid: vps += 2
    if gs.largest_army_owner == pid: vps += 2
    
    return vps

def _place_settlement(gs: GameState, pid: np.uint8, sid: int):
    gs.players[pid].settlements_built += 1
    gs.board.settlement_owner[sid] = pid
    gs.board.settlement_type[sid] = SETTLEMENT

    bit = gs.topology.py_port_bit.get(sid)
    if bit is not None:
        gs.players[pid].ports_mask |= 1 << bit

def _place_road(gs: GameState, pid: np.uint8, rid: int):
    gs.players[pid].roads_built += 1
    gs.board.road_owner[rid] = pid

### ------------------------ -------------------
### --- Game State Altering   (apply_action) ---
### ------------------------ -------------------

# --- Roll Mechanics
def roll_dice(gs: GameState, rng: np.random.Generator) -> int:
    d1 = int(rng.integers(1,7))
    d2 = int(rng.integers(1,7))
    gs.has_rolled = True
    return d1 + d2

def handle_7(gs: GameState) -> np.ndarray:
    totals = [get_total_cards(gs, pid) for pid in range(N_PLAYERS)]
    discarders = [t > 7 for t in totals]

    if not(any(discarders)):
        gs.prompt = MOVE_ROBBER
    else:
        for pid,d in enumerate(discarders):
            if d:
                to_discard = totals[pid] // 2
                gs.players[pid].discards_required = to_discard

        gs.current_player_idx = discarders.index(True)
        gs.prompt = DISCARD

def distribute_resources(gs: GameState, total: int) -> None:
    producers = gs.topology.py_roll_hexes.get(total)
    if producers is None:
        return

    board = gs.board
    robber_hex = int(board.robber_hex)
    owners = board.settlement_owner.tolist()
    types = board.settlement_type.tolist()

    gains = None  # lazily built: gains[pid][res]
    for hid, res, sids in producers:
        if hid == robber_hex:
            continue
        for sid in sids:
            owner = owners[sid]
            if owner < 0:
                continue
            if gains is None:
                gains = [[0] * N_RES for _ in range(N_PLAYERS)]
            gains[owner][res] += 2 if types[sid] == CITY else 1

    if gains is None:
        return

    bank = board.bank_res
    for res in range(N_RES):
        want = gains[0][res] + gains[1][res] + gains[2][res] + gains[3][res]
        if want == 0:
            continue
        have = int(bank[res])
        if want > have:
            # Bank short: deal round-robin until it runs dry.
            target = [gains[p][res] for p in range(N_PLAYERS)]
            temp = [0] * N_PLAYERS
            given = 0
            p = 0
            while given < have:
                if temp[p] < target[p]:
                    temp[p] += 1
                    given += 1
                p = (p + 1) % N_PLAYERS
            for _p in range(N_PLAYERS):
                gains[_p][res] = temp[_p]
            want = given
        bank[res] = have - want

    for pid in range(N_PLAYERS):
        g = gains[pid]
        if g[0] or g[1] or g[2] or g[3] or g[4]:
            hand = gs.players[pid].hand
            for res in range(N_RES):
                if g[res]:
                    hand[res] += g[res]

# --- Turn Mechanics
def pass_turn(gs: GameState):
    pid = int(gs.current_player_turn_idx)
    next_player = (pid + 1) % N_PLAYERS
    
    # Release new dev cards
    gs.players[pid].dev_cards += gs.players[pid].new_dev_cards
    gs.players[pid].new_dev_cards.fill(0)

    gs.current_player_turn_idx = next_player
    gs.current_player_idx = next_player

    gs.turn_index += 1

    gs.dev_card_used = False
    gs.has_rolled = False


def purchase_road(gs: GameState, rid: int):
    pid = int(gs.current_player_idx)

    _pay_bank(gs, pid, COSTS[0])
    _place_road(gs,pid,rid)


def purchase_settlement(gs: GameState, sid: int):
    pid = int(gs.current_player_idx)

    _pay_bank(gs, pid, COSTS[1])
    _place_settlement(gs, pid, sid)

def purchase_city(gs: GameState, cid: int):
    pid = gs.current_player_idx

    _pay_bank(gs, pid, COSTS[2])

    gs.players[pid].cities_built += 1
    gs.players[pid].settlements_built -= 1
    gs.board.settlement_type[cid] = CITY

def purchase_dev_card(gs: GameState):
    pid = gs.current_player_idx

    _pay_bank(gs, pid, COSTS[3])

    dev_card = int(gs.board.dev_deck[-1])
    gs.board.dev_deck = gs.board.dev_deck[:-1]
    gs.players[pid].new_dev_cards[dev_card] += 1

def _pay_bank(gs: GameState, pid: int, resources: np.ndarray):
    gs.board.bank_res = gs.board.bank_res + resources
    gs.players[pid].hand = gs.players[pid].hand - resources


def play_knight(gs: GameState):
    pid = int(gs.current_player_idx)
    gs.players[pid].dev_cards[KNIGHT] -= 1
    gs.players[pid].used_knights += 1
    gs.dev_card_used = True
    gs.prompt = MOVE_ROBBER

def play_monopoly(gs: GameState, res: int):
    pid = int(gs.current_player_idx)
    gs.players[pid].dev_cards[MONOPOLY] -= 1

    total_gain = 0
    for _pid in range(N_PLAYERS):
        if _pid == pid:
            continue
        total_gain += gs.players[_pid].hand[res]
        gs.players[_pid].hand[res] = 0
    gs.players[pid].hand[res] += int(total_gain)

    gs.dev_card_used = True

def play_year_of_plenty(gs: GameState, res1: int, res2: int):
    pid = int(gs.current_player_idx)
    gs.players[pid].dev_cards[YEAR_OF_PLENTY] -= 1

    if gs.board.bank_res[res1] > 0:
        gs.board.bank_res[res1] -= 1
        gs.players[pid].hand[res1] += 1

    if gs.board.bank_res[res2] > 0:
        gs.board.bank_res[res2] -= 1
        gs.players[pid].hand[res2] += 1

    gs.dev_card_used = True

def play_road_builder(gs: GameState, rid1: int, rid2: int):
    pid = int(gs.current_player_idx)
    gs.players[pid].dev_cards[ROAD_BUILDER] -= 1

    
    
    if rid1 >= 0:
        _place_road(gs,pid,rid1)
    if rid2 >= 0 and rid1 != rid2:
        _place_road(gs,pid,rid2)

    gs.dev_card_used = True


def setup_response(gs: GameState, sid: int, rid: int):
    pid = int(gs.current_player_idx)

    _place_settlement(gs, pid, sid)
    gs.board.road_owner[rid] = pid
    gs.players[pid].roads_built += 1

    if gs.setup_turn_idx == (N_PLAYERS * 2) - 1:
        gs.in_setup = False
        gs.current_player_idx = 0
        gs.current_player_turn_idx = 0
        gs.prompt = PLAY_PRETURN
    else:
        order = list(range(N_PLAYERS)) + list(range(N_PLAYERS))[::-1]
        gs.setup_turn_idx += 1
        current_player_idx = order[gs.setup_turn_idx]

        gs.current_player_idx = current_player_idx
        gs.current_player_turn_idx = current_player_idx

        gs.prompt = SETUP_TURN


# --- Trade Based
def port_trade(gs: GameState, give: int, rate: int, take: int):
    pid = int(gs.current_player_idx)
    gs.players[pid].hand[give] -= rate
    gs.players[pid].hand[take] += 1

    gs.board.bank_res[give] += rate
    gs.board.bank_res[take] -= 1

    assert (gs.board.bank_res >= 0).all(), f"Bank negative: {gs.board.bank_res} |"


def trade(gs: GameState, pid1: int, pid2: int, give: np.ndarray, take: np.ndarray):
    gs.players[pid1].hand = gs.players[pid1].hand - give
    gs.players[pid1].hand = gs.players[pid1].hand + take

    gs.players[pid2].hand = gs.players[pid2].hand + give
    gs.players[pid2].hand = gs.players[pid2].hand - take


# --- Robber-based
def move_robber(gs: GameState, rng: np.random.Generator, hex_id: int, victim_pid: int):
    if victim_pid != NONE_PLAYER:
        random_card = get_random_card(gs, rng, victim_pid)

        if random_card != -1:
            gs.players[gs.current_player_idx].hand[random_card] += 1
            gs.players[victim_pid].hand[random_card] -= 1

    gs.board.robber_hex = np.uint16(hex_id)
    




### ---------------------------- ------------------------------
### --- Game State Analysing ()   (generate_playable_moves) ---
### ---------------------------- ------------------------------

## --- Helpers
def _get_placeable_roads_from(gs: GameState, extra_road: int=-1) -> np.ndarray:
    pid = int(gs.current_player_idx)

    road_owners = gs.board.road_owner.tolist()
    settlement_owners = gs.board.settlement_owner.tolist()

    topo = gs.topology
    r_adj_sett = topo.py_road_adj_sett
    sett_adj_r = topo.py_sett_adj_roads

    my_roads = [rid for rid, o in enumerate(road_owners) if o == pid]
    if extra_road >= 0:
        my_roads.append(extra_road)

    spaces: set[int] = set()
    for rid in my_roads:
        for sid in r_adj_sett[rid]:
            if sid >= 0 and settlement_owners[sid] in (-1, pid):
                for _rid in sett_adj_r[sid]:
                    if road_owners[_rid] == -1 and _rid != extra_road:
                        spaces.add(_rid)

    return np.fromiter(spaces, dtype=np.int32, count=len(spaces))

def _generate_playable_road_builder(gs: GameState) -> np.ndarray:
    pid = int(gs.current_player_idx)
    if gs.players[pid].roads_built >= ROADS_ALLOWED:
        return np.asarray([], dtype=np.int32)
    
    first_roads = _get_placeable_roads_from(gs)

    if gs.players[pid].roads_built == ROADS_ALLOWED - 1:
        return np.stack([first_roads, first_roads], axis=1)

    spaces = []
    for rid1 in first_roads:
        second_roads = _get_placeable_roads_from(gs, rid1)
        for rid2 in second_roads:
            if rid1 != rid2:
                spaces.append((int(rid1), int(rid2)))
    return np.asarray(spaces, dtype=np.int32)


## --- Setup Turn
def generate_playable_setup_moves(gs: GameState) -> list[int]:
    actions: list[int] = []

    sett_owners = gs.board.settlement_owner.tolist()
    topo = gs.topology
    adj_settlements = topo.py_sett_adj_sett
    adj_roads = topo.py_sett_adj_roads

    for sid, sOwned in enumerate(sett_owners):
        if sOwned != -1:
            continue
        if any(sett_owners[n] != -1 for n in adj_settlements[sid]):
            continue
        for rid in adj_roads[sid]:
            actions.append(act_setup(sid, rid))
    return actions

## --- Dev Cards
def generate_playable_dev_card_moves(gs: GameState) -> list[int]:
    actions: list[int] = []

    if gs.dev_card_used:
        return actions

    pid = gs.current_player_idx
    dev_cards = gs.players[pid].dev_cards

    if dev_cards[KNIGHT] > 0:
        actions.append(ACT_PLAY_KNIGHT)

    if dev_cards[MONOPOLY] > 0:
        actions.extend(MONOPOLY_TABLE)

    if dev_cards[YEAR_OF_PLENTY] > 0:
        actions.extend(YOP_TABLE_FLAT)

    if dev_cards[ROAD_BUILDER] > 0:
        road_pairs = _generate_playable_road_builder(gs)
        actions.extend(ROAD_BUILDER_TABLE[rid1][rid2] for rid1,rid2 in road_pairs)

    return actions

# --- Building / Purchasing
def _get_placeable_roads(gs: GameState) -> np.ndarray:
    return _get_placeable_roads_from(gs)

def _get_placeable_settlements(gs: GameState) -> np.ndarray:
    pid = int(gs.current_player_idx)

    sett_owner = gs.board.settlement_owner.tolist()
    road_owner = gs.board.road_owner.tolist()

    topo = gs.topology
    adj_sett = topo.py_sett_adj_sett
    adj_roads = topo.py_sett_adj_roads

    spaces = []
    for sid, owner in enumerate(sett_owner):
        if owner != -1:
            continue
        if any(sett_owner[n] != -1 for n in adj_sett[sid]):
            continue          # distance rule
        for r in adj_roads[sid]:
            if road_owner[r] == pid:
                spaces.append(sid)   # must touch one of my roads
                break

    return np.asarray(spaces, dtype=np.int32)

def _get_placeable_cities(gs: GameState) -> np.ndarray:
    pid = int(gs.current_player_idx)
    own = (gs.board.settlement_owner == pid)
    is_settlement = (gs.board.settlement_type == SETTLEMENT)
    return np.where(own & is_settlement)[0].astype(np.int32)

def generate_playable_purchases(gs: GameState) -> list[int]:
    actions: list[int] = []
    pid = gs.current_player_idx
    player = gs.players[pid]
    w, b, sh, wh, o = player.hand.tolist()

    if w >= 1 and b >= 1:
        if player.roads_built < ROADS_ALLOWED:
            actions.extend(BUILD_ROAD_TABLE[int(rid)] for rid in _get_placeable_roads(gs))
        if sh >= 1 and wh >= 1 and player.settlements_built < SETTLEMENTS_ALLOWED:
            actions.extend(BUILD_SETTLEMENT_TABLE[int(sid)] for sid in _get_placeable_settlements(gs))

    if wh >= 2 and o >= 3 and player.cities_built < CITIES_ALLOWED:
        actions.extend(BUILD_CITY_TABLE[int(cid)] for cid in _get_placeable_cities(gs))

    # dev_deck holds card *ids*, so test how many cards are left, not their
    # values -- KNIGHT is id 0, and .any() reads an all-knight deck as empty.
    if sh >= 1 and wh >= 1 and o >= 1 and gs.board.dev_deck.size:
        actions.append(ACT_PURCHASE_DEV)

    return actions


def generate_robber_moves(gs: GameState) -> list[int]:
    actions: list[int] = []

    pid = int(gs.current_player_idx)
    curr_hex = int(gs.board.robber_hex)
    hex_sett = gs.topology.py_hex_settlement
    sett_owners = gs.board.settlement_owner.tolist()

    for hid, neighbours in enumerate(hex_sett):
        if hid == curr_hex:
            continue

        row = SELECT_ROBBER_TABLE[hid]
        victims = set()
        for sid in neighbours:
            owner = sett_owners[sid]
            if owner != -1 and owner != pid:
                victims.add(owner)

        actions.extend(row[_pid] for _pid in victims)
        actions.append(row[NONE_PLAYER])

    return actions

# --- Trading

# Preloading
PLAYER_TRADE_TABLE = {}
for give_res in range(N_RES):
    for take_res in range(N_RES):
        if give_res == take_res:
            continue
        for give_amt in (1, 2):
            give = np.zeros(N_RES, dtype=np.int16)
            take = np.zeros(N_RES, dtype=np.int16)
            give[give_res] = give_amt
            take[take_res] = 1
            PLAYER_TRADE_TABLE[(give_res, give_amt, take_res)] = act_table_trade_propose(give=give, take=take)


def _generate_port_trades(gs: GameState) -> list[int]:
    pid = gs.current_player_idx
    hand = gs.players[pid].hand.tolist()
    ports = int(gs.players[pid].ports_mask)
    bank = gs.board.bank_res.tolist()
    actions: list[int] = []

    for _res in range(N_RES):
        if (ports >> (_res + 1)) & 1:
            rate = 2
        elif ports & 1:
            rate = 3
        else:
            rate = 4

        if hand[_res] >= rate:
            row = PORT_TRADE_TABLE[_res][rate - 2]
            for _res_recieved in range(N_RES):
                if _res_recieved == _res or bank[_res_recieved] < 1:
                    continue
                actions.append(row[_res_recieved])
    return actions

def _generate_player_trades(gs: GameState) -> list[int]:
    pid = gs.current_player_idx
    hand = gs.players[pid].hand.tolist()
    bank = gs.board.bank_res.tolist()
    actions: list[int] = []

    for give_res in range(N_RES):
        held = hand[give_res]
        if held == 0:
            continue
        for take_res in range(N_RES):
            if give_res == take_res or bank[take_res] == BANK_STOCK:
                continue
            actions.append(PLAYER_TRADE_TABLE[(give_res, 1, take_res)])
            if held >= 2:
                actions.append(PLAYER_TRADE_TABLE[(give_res, 2, take_res)])

    return actions

def generate_playable_trades(gs: GameState) -> list[int]:
    if not gs.players[int(gs.current_player_idx)].hand.any():
        return []
    return _generate_port_trades(gs) + _generate_player_trades(gs)

def trade_selection(gs: GameState) -> list[int]:
    proposer = int(gs.current_player_idx)
    candidate_mask = gs.trade_accept_mask
    
    actions = []
    for pid, accepted in enumerate(candidate_mask):
        if pid == proposer:
            continue
        if accepted:
            actions.append(TABLE_TRADE_SELECT_TABLE[pid])
    return actions

def trade_decision(gs: GameState) -> list[int]:
    hand = gs.players[int(gs.current_player_idx)].hand.tolist()
    take = gs.trade_offer_take.tolist()

    for h, t in zip(hand, take):
        if h < t:
            return [ACT_TRADE_REJECT]
    return [ACT_TRADE_ACCEPT, ACT_TRADE_REJECT]


## Helpers
def get_largest_army(gs: GameState, pid: np.uint8) -> np.uint8:
    army_size = gs.players[pid].used_knights

    if army_size < 3: return gs.largest_army_owner
    if gs.largest_army_owner in (NONE_PLAYER, pid): return pid
    
    larg_pid = gs.largest_army_owner

    return pid if army_size > gs.players[larg_pid].used_knights else larg_pid

def _calculate_longest_road(gs: GameState, pid: np.uint8) -> int:
    pid = int(pid)
    road_owner = gs.board.road_owner.tolist()
    sett_owner = gs.board.settlement_owner.tolist()
    road_adj_sett = gs.topology.py_road_adj_sett

    my_roads = [r for r, o in enumerate(road_owner) if o == pid]
    if not my_roads:
        return 0

    # settlements that block traversal (enemy)
    enemy_sett = {s for s, o in enumerate(sett_owner) if o != -1 and o != pid}

    # settlement -> list of *your* incident roads (only if settlement not blocked)
    sett_to_roads: dict[int, list[int]] = {}
    endpoints: dict[int, tuple[int, int]] = {}

    for r in my_roads:
        sA, sB = road_adj_sett[r]
        endpoints[r] = (sA, sB)
        if sA >= 0 and sA not in enemy_sett:
            sett_to_roads.setdefault(sA, []).append(r)
        if sB >= 0 and sB not in enemy_sett:
            sett_to_roads.setdefault(sB, []).append(r)

    used_edges: set[int] = set()

    def dfs_from_settlement(s: int) -> int:
        """Longest simple path length starting at settlement s (edges not reused)."""
        best = 0
        for r in sett_to_roads.get(s, ()):
            if r in used_edges:
                continue
            a, b = endpoints[r]
            t = b if s == a else a

            used_edges.add(r)
            cont = 0
            if t >= 0 and t not in enemy_sett:
                cont = dfs_from_settlement(t)
            used_edges.remove(r)

            length = 1 + cont
            if length > best:
                best = length
        return best

    longest = 0
    for s in sett_to_roads.keys():  # try all possible starts
        v = dfs_from_settlement(s)
        if v > longest:
            longest = v
    return int(longest)

def get_longest_road(gs: GameState, pid: np.uint8, a: int) -> np.uint8:
    action,arg1,_ = unpack_action(a)
    road_owner = gs.board.road_owner

    if action == BUILD_SETTLEMENT:
        sid = int(arg1)
        neigh_rids = gs.topology.settlement_adj_roads[sid]
        neigh_pids = [int(road_owner[r]) for r in neigh_rids if r >= 0 and int(road_owner[r]) not in (-1, pid)]
        
        if len(neigh_pids) < 2: return gs.longest_road_owner
        if neigh_pids[0] != neigh_pids[1]: return gs.longest_road_owner

        affected_pid = int(neigh_pids[0])
        new_length = _calculate_longest_road(gs, affected_pid)
        gs.players[affected_pid].longest_road_len = new_length
    
    elif action in (BUILD_ROAD, PLAY_ROAD_BUILDER):
        new_length = _calculate_longest_road(gs, pid)
        gs.players[pid].longest_road_len = new_length

    # Check all roads against each-other
    longest_pid = int(gs.longest_road_owner)
    lengths = [int(gs.players[p].longest_road_len) for p in range(N_PLAYERS)]

    longest_road = int(gs.players[longest_pid].longest_road_len) if longest_pid != NONE_PLAYER else 0

    _longest_pid = longest_pid
    for _pid,_length in enumerate(lengths):
        if _pid == longest_pid: continue
        if _length >= 5 and _length > longest_road:
            _longest_pid = _pid

    return _longest_pid

def win_check(gs: GameState, pid: np.uint8) -> np.uint8:
    vps = get_victory_points(gs, pid)

    if vps >= VICTORY_POINTS_REQUIRED:
        return pid
    return NONE_PLAYER