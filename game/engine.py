import random
import numpy as np

from catan.state import GameState
from catan.init import initialize_game
from catan.engine import apply_action, apply_action_inplace

class Engine:
    def __init__(self, game_state: GameState | None = None, seed: int | None = None):
        if seed is None:
            seed = random.randint(1, 10000)
        rng = np.random.default_rng(seed)

        if game_state is None:
            game_state = initialize_game(rng)

        self.seed = int(seed)
        self.rng = rng
        self.gs = game_state

    def step(self, action: int):
        apply_action_inplace(self.gs, action, self.rng)