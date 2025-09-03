import numpy as np
from catan.state import GameState

class Player:
    def __init__(self, player_id):
        self.player_id = player_id

    def decide(self, gs: GameState, playable_moves: np.ndarray):
        return playable_moves[0]
    
class RandomPlayer(Player):
    def decide(self, gs: GameState, playable_moves: np.ndarray):
        return playable_moves[np.random.randint(len(playable_moves))]