import random

import numpy as np

from catan.state import GameState
from catan.actions import unpack_action
from catan.actions import BUILD_CITY, BUILD_SETTLEMENT, BUILD_ROAD
from catan.actions import TABLE_TRADE_ACCEPT, TABLE_TRADE_PROPOSE, TABLE_TRADE_REJECT, TABLE_TRADE_SELECT


class Player:
    def __init__(self, player_id, seed=None):
        self.player_id = player_id
        # Each player draws from its own stream rather than the global `random` /
        # `np.random`, so a seeded run is actually reproducible. Leaving seed as
        # None keeps the old behaviour of seeding from OS entropy.
        self.rng = random.Random(seed)
        self.set_constants()

    def set_constants(self):
        pass

    def decide(self, gs: GameState, playable_moves: list[int]):
        return playable_moves[0]


class RandomPlayer(Player):
    def decide(self, gs: GameState, playable_moves: list[int]):
        return playable_moves[self.rng.randrange(len(playable_moves))]


class RandomPlayerDistributed(Player):
    def decide(self, gs: GameState, playable_moves: list[int]):
        playable_actions = [unpack_action(a)[0] for a in playable_moves]
        unique_actions = sorted({a for a in playable_actions})
        selected_action = self.rng.choice(unique_actions)
        _playable_moves = [a for i, a in enumerate(playable_moves) if playable_actions[i] == selected_action]
        return _playable_moves[self.rng.randrange(len(_playable_moves))]


class RandomPlayerWithRules(Player):
    def decide(self, gs: GameState, playable_moves: list[int]):
        move_actions = [unpack_action(a)[0] for a in playable_moves]
        unique_actions = sorted({a for a in move_actions})

        if BUILD_CITY in unique_actions:
            selected_action = BUILD_CITY
        elif BUILD_SETTLEMENT in unique_actions:
            selected_action = BUILD_SETTLEMENT
        elif BUILD_ROAD in unique_actions:
            selected_action = BUILD_ROAD
        else:
            selected_action = self.rng.choice(unique_actions)

        _playable_moves = [a for i, a in enumerate(playable_moves) if move_actions[i] == selected_action]
        return _playable_moves[self.rng.randrange(len(_playable_moves))]


class RandomDistributedNoTrades(Player):
    def set_constants(self):
        self.action_mask = {TABLE_TRADE_ACCEPT, TABLE_TRADE_PROPOSE, TABLE_TRADE_REJECT, TABLE_TRADE_SELECT}

    def decide(self, gs: GameState, playable_moves: list[int]):
        # unpack once, keep pairs
        unpacked = [(a, unpack_action(a)[0]) for a in playable_moves]
        masked = [(a, action) for a, action in unpacked if action not in self.action_mask]

        if not masked:
            masked = unpacked

        # sorted() rather than list(set): set iteration order is an implementation
        # detail, and this choice has to be stable for a given seed.
        selected_action = self.rng.choice(sorted({action for _, action in masked}))
        candidates = [a for a, action in masked if action == selected_action]
        return self.rng.choice(candidates)
