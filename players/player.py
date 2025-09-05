import numpy as np
from catan.state import GameState
from catan.actions import unpack_action

class Player:
    def __init__(self, player_id):
        self.player_id = player_id

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