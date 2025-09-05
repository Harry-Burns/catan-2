
from dataclasses import dataclass
from typing import List, Type, Tuple, Dict, Any
import numpy as np

from catan.ids import N_PLAYERS, NONE_PLAYER
from players.player import Player

from game.engine import Engine
from game.runner import GameRunner


@dataclass
class BatchSummary:
    games: int
    wins: List[int]
    win_rates: List[float]
    avg_steps: List[int]

def run_batch(
    player_types: List[Type[Player]],
    num_games: int,
    base_seed: int,
    max_steps: 100_000,
    verbose: bool = False,
) -> BatchSummary:
    
    rng = np.random.default_rng(base_seed)
    wins = np.zeros(N_PLAYERS, dtype=np.int32)

    details = []

    for gid in range(num_games):
        seed = int(rng.integers(1, 2**31 - 1))
        engine = Engine(seed=seed)
        players = [player_types[pid](player_id=pid) for pid in range(N_PLAYERS)]

        runner = GameRunner(engine=engine, player_controllers=players)
        runner.max_steps = max_steps
        runner.play_game(verbose=verbose)

        gs = runner.engine.gs

        winner = int(gs.winner)
        if winner != NONE_PLAYER:
            wins[winner] += 1
        
        details.append({
            'gid': gid,
            'winner': winner,
            'steps': runner.steps,
            'seed': seed
        })

    avg_steps = float(np.mean([d["steps"] for d in details])) if details else 0.0
    summary = BatchSummary(
        games=num_games,
        wins=wins,
        win_rates=[w / num_games for w in wins],
        avg_steps=avg_steps,
    )

    return summary
