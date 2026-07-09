import numpy as np
from catan.state import GameState
from catan.actions import unpack_action

class Player:
    def __init__(self, player_id):
        self.player_id = player_id
        self.set_constants()

    def set_constants(self):
        pass

    def decide(self, gs: GameState, playable_moves: np.ndarray):
        return playable_moves[0]
    

class RandomPlayer(Player):
    def decide(self, gs: GameState, playable_moves: np.ndarray):
        return playable_moves[np.random.randint(len(playable_moves))]



class RandomPlayerDistributed(Player):
    def decide(self, gs: GameState, playable_moves: np.ndarray):
        playable_actions = [unpack_action(a)[0] for a in playable_moves]
        unique_actions = np.unique(playable_actions)
        selected_action = np.random.choice(unique_actions) 
        _playable_moves = [a for i,a in enumerate(playable_moves) if playable_actions[i] == selected_action]
        return _playable_moves[np.random.randint(len(_playable_moves))]



from catan.actions import BUILD_CITY, BUILD_SETTLEMENT, BUILD_ROAD

class RandomPlayerWithRules(Player):
    def decide(self, gs: GameState, playable_moves: np.ndarray):
        move_actions = [unpack_action(a)[0] for a in playable_moves]
        unique_actions = np.unique(move_actions)

        if BUILD_CITY in unique_actions:
            selected_action = BUILD_CITY
        elif BUILD_SETTLEMENT in unique_actions:
            selected_action = BUILD_SETTLEMENT
        elif BUILD_ROAD in unique_actions:
            selected_action = BUILD_ROAD
        else:
            selected_action = np.random.choice(unique_actions)

        _playable_moves = [a for i,a in enumerate(playable_moves) if move_actions[i] == selected_action]
        return _playable_moves[np.random.randint(len(_playable_moves))]
    
import random
from catan.actions import TABLE_TRADE_ACCEPT, TABLE_TRADE_PROPOSE, TABLE_TRADE_REJECT, TABLE_TRADE_SELECT

class RandomDistributedNoTrades(Player):
    def set_constants(self):
        self.action_mask = {TABLE_TRADE_ACCEPT, TABLE_TRADE_PROPOSE, TABLE_TRADE_REJECT, TABLE_TRADE_SELECT}

    def decide(self, gs: GameState, playable_moves: np.ndarray):
        # unpack once, keep pairs
        unpacked = [(a, unpack_action(a)[0]) for a in playable_moves]
        masked = [(a, action) for a, action in unpacked if action not in self.action_mask]

        if not masked:
            masked = unpacked

        # set instead of np.unique, random instead of np.random
        selected_action = random.choice(list({action for _, action in masked}))
        candidates = [a for a, action in masked if action == selected_action]
        return random.choice(candidates)