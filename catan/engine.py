import numpy as np

from catan.state import GameState
from catan.actions import unpack_action

from catan.interface import (
    setup_response, pass_turn, roll_dice, distribute_resources, handle_7, 
    trade, port_trade,
    purchase_road, purchase_settlement, purchase_city, purchase_dev_card, 
    play_knight, play_monopoly, play_year_of_plenty, play_road_builder, move_robber, 
    
    win_check, get_largest_army, get_longest_road,
    generate_playable_setup_moves, generate_playable_dev_card_moves, generate_playable_purchases,
    generate_robber_moves, 
    generate_playable_trades, trade_decision, trade_selection
)

from catan.actions import *
from catan.actions import ACT_PASS, ACT_ROLL, DISCARD_TABLE
from catan.ids import (
    N_PLAYERS, N_RES
)


def apply_action_inplace(gs: GameState, a: int, rng: np.random.Generator) -> None:
    # unpack_action() inlined: this runs once per step and the call alone costs
    # more than the three shifts.
    action = (a >> 24) & 0xFF
    arg1 = (a >> 12) & 0xFFF
    arg2 = a & 0xFFF
    player = gs.current_player_idx

    # --- Setup Logic
    if action == SETUP_RESPONSE:
        setup_response(gs, arg1, arg2)


    # --- Turn / Fundamentals
    elif action == PASS:
        pass_turn(gs)
        gs.prompt = PLAY_PRETURN

    elif action == ROLL:
        roll = roll_dice(gs, rng)
        if roll == 7:
            handle_7(gs)
        else:
            distribute_resources(gs, roll)
            gs.prompt = PLAY_TURN


    # --- Robber-based
    elif action == SELECT_ROBBER_RESPONSE:
        hex_id,victim_pid = arg1, arg2
        move_robber(gs, rng, hex_id, victim_pid)
        gs.prompt = PLAY_TURN


    # --- Purchases
    elif action == BUILD_ROAD:
        purchase_road(gs, arg1)
        gs.prompt = PLAY_TURN
    elif action == BUILD_SETTLEMENT:
        purchase_settlement(gs, arg1)
        gs.prompt = PLAY_TURN
    elif action == BUILD_CITY:
        purchase_city(gs, arg1)
        gs.prompt = PLAY_TURN
    elif action == PURCHASE_DEV_CARD:
        purchase_dev_card(gs)
        gs.prompt = PLAY_TURN


    # --- Dev Cards
    elif action == PLAY_KNIGHT:
        play_knight(gs)
    elif action == PLAY_MONOPOLY:
        play_monopoly(gs, arg1)
        gs.prompt = PLAY_TURN if gs.has_rolled else PLAY_PRETURN
    elif action == PLAY_YEAR_OF_PLENTY:
        play_year_of_plenty(gs, arg1, arg2)
        gs.prompt = PLAY_TURN if gs.has_rolled else PLAY_PRETURN
    elif action == PLAY_ROAD_BUILDER:
        play_road_builder(gs, arg1, arg2)
        gs.prompt = PLAY_TURN if gs.has_rolled else PLAY_PRETURN


    # --- Trades
    elif action == PORT_TRADE:
        give,rate,take = unpack_port_trade(a)
        port_trade(gs,give,rate,take)
        gs.prompt = PLAY_TURN

    elif action == TABLE_TRADE_PROPOSE:
        give,take = unpack_table_trade(a)

        gs.trade_offer_give = np.array(give, dtype=np.int8)
        gs.trade_offer_take = np.array(take, dtype=np.int8)
        gs.trade_offer_from = int(gs.current_player_idx)
        gs.trade_accept_mask.fill(False)

        gs.current_player_idx = (gs.current_player_idx + 1) % N_PLAYERS
        gs.prompt = DECIDE_TRADE
    elif action == TABLE_TRADE_SELECT:
        if arg1 != -1:
            pid1, pid2 = gs.current_player_idx, arg1
            give, take = gs.trade_offer_give, gs.trade_offer_take

            trade(gs, pid1, pid2, give, take)

        gs.trade_offer_from = -1
        gs.trade_accept_mask.fill(False)
        gs.trade_offer_give.fill(0)
        gs.trade_offer_take.fill(0)

        gs.prompt = PLAY_TURN
    elif action in [TABLE_TRADE_ACCEPT, TABLE_TRADE_REJECT]:
        if action == TABLE_TRADE_ACCEPT:
            gs.trade_accept_mask[player] = True

        gs.current_player_idx = (player + 1) % N_PLAYERS
        
        if gs.current_player_idx != gs.current_player_turn_idx:
            gs.prompt = DECIDE_TRADE
        else:
            if any(gs.trade_accept_mask):
                gs.prompt = DECIDE_ACCEPTEES
            else:
                gs.trade_offer_from = -1
                gs.trade_accept_mask.fill(False)
                gs.trade_offer_give.fill(0)
                gs.trade_offer_take.fill(0)
                gs.prompt = PLAY_TURN
        

    # --- Discard Logic
    elif action == DISCARD_RESOURCE:
        pid, res = gs.current_player_idx, arg1

        gs.players[pid].hand[res] -= 1
        gs.board.bank_res[res] += 1

        gs.players[pid].discards_required -= 1

        if gs.players[pid].discards_required == 0:
            players_requiring_discards = [gs.players[_pid].discards_required > 0 for _pid in range(N_PLAYERS)]

            if any(players_requiring_discards):
                gs.current_player_idx = players_requiring_discards.index(True) # Get first 'True'
                gs.prompt = DISCARD
            else:
                gs.current_player_idx = gs.current_player_turn_idx
                gs.prompt = MOVE_ROBBER
        else:
            gs.prompt = DISCARD


    else:
        raise ValueError(f"WTF? Bad action?: \"{action}\"")

    # At the end of every action do the following:
    if action in LARGEST_ARMY_ACTIONS:
        gs.largest_army_owner = get_largest_army(gs, player)

    if action in LONGEST_ROAD_ACTIONS:
        gs.longest_road_owner = get_longest_road(gs, player, a)

    if action in VICTORY_POINT_ACTIONS:
        gs.winner = win_check(gs, player)
    gs.action_log.append(a)

def apply_action(gs: GameState, a: int, rng: np.random.Generator) -> GameState:
    _gs = gs.clone_shallow()
    apply_action_inplace(_gs, a, rng)
    return _gs


def playable_moves(gs: GameState) -> list[int]:
    """Legal actions for the current prompt.

    Branches are ordered by how often each prompt comes up. Returns a plain list:
    nothing downstream indexes it as an array, so boxing it into an ndarray only
    costs the caller an unbox per move.
    """
    prompt = gs.prompt

    if prompt == PLAY_TURN:
        actions = generate_playable_dev_card_moves(gs)
        actions.extend(generate_playable_purchases(gs))
        actions.extend(generate_playable_trades(gs))
        actions.append(ACT_PASS)
        return actions

    if prompt == PLAY_PRETURN:
        actions = generate_playable_dev_card_moves(gs)
        actions.append(ACT_ROLL)
        return actions

    if prompt == DECIDE_TRADE:
        return trade_decision(gs)

    if prompt == MOVE_ROBBER:
        return generate_robber_moves(gs)

    if prompt == DISCARD:
        hand = gs.players[gs.current_player_idx].hand
        return [DISCARD_TABLE[res] for res in range(N_RES) if hand[res] > 0]

    if prompt == SETUP_TURN:
        return generate_playable_setup_moves(gs)

    if prompt == DECIDE_ACCEPTEES:
        return trade_selection(gs)

    raise ValueError(f"No move generator for prompt: {prompt}")
