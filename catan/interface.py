import numpy as np
from itertools import product

from catan.state import GameState

from catan.actions import (
    DISCARD, MOVE_ROBBER, PLAY_PRETURN, SETUP_TURN, BUILD_SETTLEMENT, BUILD_ROAD, PLAY_ROAD_BUILDER,

    act_setup, 
    act_play_knight, act_play_monopoly, act_play_road_builder, act_play_yop,
    act_build_road, act_build_settlement, act_build_city, act_purchase_dev,
    act_select_robber_response, 
    act_table_trade_accept, act_table_trade_reject, act_table_trade_select, act_table_trade_propose,
    unpack_action
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
    return int(gs.players[pid].hand.sum())

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
    gs.board.settlement_owner[sid] = np.int8(pid)
    gs.board.settlement_type[sid] = np.int8(SETTLEMENT)

    # Add ports
    port_sett = gs.topology.port_settlement_ix
    for _portid, _sids in enumerate(port_sett):
        if sid in _sids:
            gs.players[pid].ports_mask[_portid] = True
            break


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
        gs.prompt = np.uint8(MOVE_ROBBER)
    else:
        for pid,d in enumerate(discarders):
            if d:
                to_discard = totals[pid] // 2
                gs.players[pid].discards_required = to_discard

        gs.current_player_idx = discarders.index(True)
        gs.prompt = np.uint8(DISCARD)

def distribute_resources(gs: GameState, total: int) -> None:
    topo  = gs.topology; board = gs.board

    player_gains = np.zeros((N_PLAYERS, N_RES), dtype=np.int16)
    bank_losses  = np.zeros(N_RES, dtype=np.int16)

    robber_hex = int(board.robber_hex)

    for hid, num in enumerate(topo.hex_number):
        if hid == robber_hex or int(num) != total:
            continue

        res = int(topo.hex_resource[hid])  # 0..4; 5 = desert
        if res == DESERT:
            continue

        # walk the 6 corners around this hex
        for v in range(6):
            sid = int(topo.hex_settlement_ix[hid, v])
            if sid < 0:
                continue

            owner = int(board.settlement_owner[sid])
            if owner < 0:
                continue

            stype = int(board.settlement_type[sid])
            qty = 2 if stype == CITY else (1 if stype == SETTLEMENT else 0)
            if qty == 0:
                continue

            player_gains[owner, res] += qty
            bank_losses[res] += qty

    bank_hand = gs.board.bank_res
    for _res in range(N_RES):
        if bank_losses[_res] <= bank_hand[_res]:
            continue

        # Case for when bank doesnt have enough.
        _maximum = int(bank_hand[_res]); _player = 0
        target_gains = player_gains[:, _res].astype(np.int32)
        temp_gains = np.zeros(N_PLAYERS, dtype=np.int16)

        while temp_gains.sum() < _maximum:
            if temp_gains[_player] < target_gains[_player]:
                temp_gains[_player] += 1
            _player = (_player + 1) % N_PLAYERS
        player_gains[:,_res] = temp_gains
        bank_losses[_res] = int(temp_gains.sum())

    for pid in range(N_PLAYERS):
        if player_gains[pid].any():
            gs.players[pid].hand[:] = gs.players[pid].hand + player_gains[pid]

    if bank_losses.any():
        board.bank_res[:] = board.bank_res - bank_losses

# --- Turn Mechanics
def pass_turn(gs: GameState):
    pid = int(gs.current_player_turn_idx)
    next_player = (pid + 1) % N_PLAYERS
    
    # Release new dev cards
    gs.players[pid].dev_cards += gs.players[pid].new_dev_cards
    gs.players[pid].new_dev_cards.fill(0)

    gs.current_player_turn_idx = np.uint8(next_player)
    gs.current_player_idx = np.uint8(next_player)

    gs.turn_index += 1

    gs.dev_card_used = False
    gs.has_rolled = False


def purchase_road(gs: GameState, rid: int):
    pid = gs.current_player_idx

    _pay_bank(gs, pid, COSTS[0])

    gs.players[pid].roads_built += 1
    gs.board.road_owner[rid] = np.int8(pid)

def purchase_settlement(gs: GameState, sid: int):
    pid = int(gs.current_player_idx)

    _pay_bank(gs, pid, COSTS[1])
    _place_settlement(gs, pid, sid)

def purchase_city(gs: GameState, cid: int):
    pid = gs.current_player_idx

    _pay_bank(gs, pid, COSTS[2])

    gs.players[pid].cities_built += 1
    gs.players[pid].settlements_built -= 1
    gs.board.settlement_type[cid] = np.int8(CITY)

def purchase_dev_card(gs: GameState):
    pid = gs.current_player_idx

    _pay_bank(gs, pid, COSTS[3])

    dev_card = int(gs.board.dev_deck[-1])
    gs.board.dev_deck = gs.board.dev_deck[:-1]
    gs.players[pid].new_dev_cards[dev_card] += 1

def _pay_bank(gs: GameState, pid: int, resources: np.ndarray):
    gs.board.bank_res[:] = gs.board.bank_res + resources
    gs.players[pid].hand[:] = gs.players[pid].hand - resources


def play_knight(gs: GameState):
    pid = int(gs.current_player_idx)
    gs.players[pid].dev_cards[KNIGHT] -= 1
    gs.players[pid].used_knights += 1
    gs.dev_card_used = True
    gs.prompt = np.uint8(MOVE_ROBBER)

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
        gs.players[pid].roads_built += 1
        gs.board.road_owner[rid1] = np.int8(pid)
    if rid2 >= 0 and rid1 != rid2:
        gs.players[pid].roads_built += 1
        gs.board.road_owner[rid2] = np.int8(pid)

    gs.dev_card_used = True


def setup_response(gs: GameState, sid: int, rid: int):
    pid = int(gs.current_player_idx)

    _place_settlement(gs, pid, sid)
    gs.board.road_owner[rid] = np.int8(pid)
    gs.players[pid].roads_built += 1

    if gs.setup_turn_idx == (N_PLAYERS * 2) - 1:
        gs.in_setup = False
        gs.current_player_idx = np.uint8(0)
        gs.current_player_turn_idx = np.uint8(0)
        gs.prompt = np.uint8(PLAY_PRETURN)
    else:
        order = list(range(N_PLAYERS)) + list(range(N_PLAYERS))[::-1]
        gs.setup_turn_idx += 1
        current_player_idx = order[gs.setup_turn_idx]

        gs.current_player_idx = np.uint8(current_player_idx)
        gs.current_player_turn_idx = np.uint8(current_player_idx)

        gs.prompt = np.uint8(SETUP_TURN)


# --- Trade Based
def port_trade(gs: GameState, give: int, rate: int, take: int):
    pid = int(gs.current_player_idx)

    gs.players[pid].hand[give] -= rate
    gs.players[pid].hand[take] += 1

    gs.board.bank_res[give] += rate
    gs.board.bank_res[take] -= 1

def trade(gs: GameState, pid1: int, pid2: int, give: np.ndarray, take: np.ndarray):
    gs.players[pid1].hand[:] = gs.players[pid1].hand - give
    gs.players[pid1].hand[:] = gs.players[pid1].hand + take

    gs.players[pid2].hand[:] = gs.players[pid2].hand + give
    gs.players[pid2].hand[:] = gs.players[pid2].hand - take


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
    spaces: set[int] = set()

    pid = int(gs.current_player_idx)

    road_owners = gs.board.road_owner
    settlement_owners = gs.board.settlement_owner

    r_adj_sett = gs.topology.road_adj_settlement
    sett_adj_r = gs.topology.settlement_adj_roads

    my_roads = np.where(road_owners == pid)[0]
    if extra_road >= 0:
        my_roads = np.concatenate([my_roads, np.array([extra_road], dtype=my_roads.dtype)])

    for rid in my_roads:
        for sid in r_adj_sett[rid]:
            if sid >= 0 and (settlement_owners[int(sid)] in (-1, pid)):
                for _rid in sett_adj_r[int(sid)]:
                    if _rid >= 0 and road_owners[int(_rid)] == -1 and _rid != extra_road:
                        spaces.add(int(_rid))

    return np.fromiter(spaces, dtype=np.int32) 

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

    sett_owners = gs.board.settlement_owner
    adj_settlements = gs.topology.settlement_adj_settlement
    adj_roads = gs.topology.settlement_adj_roads

    for sid,sOwned in enumerate(sett_owners):
        if sOwned != -1:
            continue
        
        neigh = adj_settlements[sid]
        mask = neigh >= 0
        if mask.any() and (sett_owners[neigh[mask]] != -1).any():
            continue

        for rid in adj_roads[sid]:
            if rid >= 0:
                actions.append(act_setup(int(sid), int(rid)))
    return actions

## --- Dev Cards
def generate_playable_dev_card_moves(gs: GameState) -> list[int]:
    actions: list[int] = []

    if gs.dev_card_used:
        return actions

    pid = gs.current_player_idx
    dev_cards = gs.players[pid].dev_cards

    if dev_cards[KNIGHT] > 0:
        actions.append(act_play_knight())

    if dev_cards[MONOPOLY] > 0:
        actions.extend([act_play_monopoly(res) for res in range(N_RES)])

    if dev_cards[YEAR_OF_PLENTY] > 0:
        actions.extend([act_play_yop(res1, res2) for res1,res2 in product(range(N_RES), range(N_RES))])

    if dev_cards[ROAD_BUILDER] > 0:
        road_pairs = _generate_playable_road_builder(gs)
        actions.extend([act_play_road_builder(rid1, rid2) for rid1,rid2 in road_pairs])

    return actions

# --- Building / Purchasing
def _get_placeable_roads(gs: GameState) -> np.ndarray:
    return _get_placeable_roads_from(gs)

def _get_placeable_settlements(gs: GameState) -> np.ndarray:
    pid = int(gs.current_player_idx)

    sett_owner = gs.board.settlement_owner            # (N_SETT,)
    road_owner = gs.board.road_owner                  # (N_ROAD,)

    adj_sett  = gs.topology.settlement_adj_settlement # (N_SETT,3)
    adj_roads = gs.topology.settlement_adj_roads      # (N_SETT,3)

    spaces = []
    for sid, owner in enumerate(sett_owner):
        if owner != -1:
            continue

        # distance rule
        neigh = adj_sett[sid]
        ok = True
        for n in neigh:
            if n >= 0 and sett_owner[int(n)] != -1:
                ok = False
                break
        if not ok:
            continue

        # must touch at least one of my roads
        connected = False
        for r in adj_roads[sid]:
            if r >= 0 and road_owner[int(r)] == pid:
                connected = True
                break
        if connected:
            spaces.append(int(sid))

    return np.asarray(spaces, dtype=np.int32)

def _get_placeable_cities(gs: GameState) -> np.ndarray:
    pid = int(gs.current_player_idx)
    own = (gs.board.settlement_owner == pid)
    is_settlement = (gs.board.settlement_type == SETTLEMENT)
    return np.where(own & is_settlement)[0].astype(np.int32)

def generate_playable_purchases(gs: GameState) -> list[int]:
    actions: list[int] = []
    pid = gs.current_player_idx
    hand = gs.players[pid].hand
    afford =  (hand[None, :] >= COSTS).all(axis=1)

    if afford[OBJ_ROAD] and gs.players[pid].roads_built < ROADS_ALLOWED:
        actions.extend([act_build_road(int(rid)) for rid in _get_placeable_roads(gs)])

    if afford[OBJ_SETTLEMENT] and gs.players[pid].settlements_built < SETTLEMENTS_ALLOWED:
        actions.extend([act_build_settlement(int(sid)) for sid in _get_placeable_settlements(gs)])

    if afford[OBJ_CITY] and gs.players[pid].cities_built < CITIES_ALLOWED:
        actions.extend([act_build_city(int(cid)) for cid in _get_placeable_cities(gs)])

    if afford[OBJ_DEV] and gs.board.dev_deck.any():
        actions.append(act_purchase_dev())

    return actions



def generate_robber_moves(gs: GameState) -> list[int]:
    actions: list[int] = []

    pid = gs.current_player_idx
    curr_hex = gs.board.robber_hex
    hex_sett = gs.topology.hex_settlement_ix
    sett_owners = gs.board.settlement_owner

    for hid,neighbours in enumerate(hex_sett):
        if hid == curr_hex:
            continue
        
        victims = set()
        for sid in neighbours:
            if sid < 0:
                continue
            owner = sett_owners[sid]
            if owner in (-1, int(pid)):
                continue
            victims.add(owner)

        actions.extend([act_select_robber_response(int(hid),int(_pid)) for _pid in victims])
        actions.append(act_select_robber_response(int(hid),int(NONE_PLAYER)))

    return actions

# --- Trading
def generate_playable_trades(gs: GameState) -> list[int]:
    # TODO: Implement - No trading moves allowed for now.
    return []

def trade_selection(gs: GameState) -> list[int]:
    proposer = int(gs.current_player_idx)
    candidate_mask = gs.trade_accept_mask
    
    actions = []
    for pid, accepted in enumerate(candidate_mask):
        if pid == proposer:
            continue
        if accepted:
            actions.append(act_table_trade_select(pid))
    return actions

def trade_decision(gs: GameState) -> list[int]:
    pid = int(gs.current_player_idx)
    hand = gs.players[pid].hand
    take = gs.trade_offer_take

    actions = [act_table_trade_reject()]

    if np.all(hand >= take):
        actions.insert(0, act_table_trade_accept())
    return actions


## Helpers
def get_largest_army(gs: GameState, pid: np.uint8) -> np.uint8:
    army_size = gs.players[pid].used_knights

    if army_size < 3: return gs.largest_army_owner
    if gs.largest_army_owner in (NONE_PLAYER, pid): return np.uint8(pid)
    
    larg_pid = gs.largest_army_owner

    return np.uint8(pid) if army_size > gs.players[larg_pid].used_knights else np.uint8(larg_pid)

def _calculate_longest_road(gs: GameState, pid: np.uint8) -> int:
    road_owner = gs.board.road_owner
    sett_owner = gs.board.settlement_owner
    road_adj_sett = gs.topology.road_adj_settlement
    
    my_roads = np.where(road_owner == pid)[0]
    enemy_sett = set(int(s) for s in np.where((sett_owner != -1) & (sett_owner != pid))[0])

    # build settlement -> my roads incidence for fast neighbor lookup
    sett_to_my_roads = {}
    for r in my_roads:
        sA, sB = int(road_adj_sett[r,0]), int(road_adj_sett[r,1])
        if sA >= 0 and sA not in enemy_sett:
            sett_to_my_roads.setdefault(sA, []).append(r)
        if sB >= 0 and sB not in enemy_sett:
            sett_to_my_roads.setdefault(sB, []).append(r)

    # DFS over edges (roads) without reusing edges
    visited = set()

    def dfs(cur_road: int, came_from_sett: int) -> int:
        visited.add(cur_road)
        best = 1  # count this road
        sA, sB = int(road_adj_sett[cur_road,0]), int(road_adj_sett[cur_road,1])

        # Explore from each endpoint that is not blocked
        for s in (sA, sB):
            if s < 0 or s in enemy_sett:
                continue
            # Next roads are my roads incident on this settlement
            for nxt in sett_to_my_roads.get(s, []):
                if nxt in visited or nxt == cur_road:
                    continue
                # Move along to next edge
                length = 1 + dfs(nxt, s)
                if length > best:
                    best = length

        visited.remove(cur_road)
        return best

    longest = 0
    # Try each owned road as a starting edge
    for r in my_roads:
        val = dfs(r, -1)
        if val > longest:
            longest = val

    return int(longest)

def get_longest_road(gs: GameState, pid: np.uint8, a: int) -> np.uint8:
    action,arg1,_ = unpack_action(a)
    road_owner = gs.board.road_owner

    if action == BUILD_SETTLEMENT:
        sid = int(arg1)
        neigh_rids = gs.topology.settlement_adj_roads[sid]
        neigh_pids = [int(road_owner[r]) for r in neigh_rids if r >= 0 and int(road_owner[r]) not in (-1, pid)]
        
        if len(neigh_pids) < 3: return gs.longest_road_owner

        if neigh_pids[0] == neigh_pids[1] or neigh_pids[1] == neigh_pids[2] or neigh_pids[2] == neigh_pids[0]:
            blocked_neigh = neigh_pids[0] if neigh_pids[0] == neigh_pids[1] or neigh_pids[0] == neigh_pids[2] else neigh_pids[1]
            new_length = _calculate_longest_road(gs, blocked_neigh)
            gs.players[blocked_neigh].longest_road_len = new_length
    
    elif action in (BUILD_ROAD, PLAY_ROAD_BUILDER):
        length = _calculate_longest_road(gs, pid)
        gs.players[pid].longest_road_len = length

    # Check all roads against each-other
    longest_pid = gs.longest_road_owner
    longest_road = gs.players[longest_pid].longest_road_len if longest_pid != NONE_PLAYER else 0

    _longest_pid = longest_pid
    for _pid in range(N_PLAYERS):
        if _pid == longest_pid: continue
        _length = gs.players[_pid].longest_road_len
        if _length >= 5 and _length > longest_road:
            _longest_pid = _pid

    return np.uint8(_longest_pid)

def win_check(gs: GameState, pid: np.uint8) -> np.uint8:
    vps = get_victory_points(gs, pid)

    if vps >= VICTORY_POINTS_REQUIRED:
        return pid
    return NONE_PLAYER