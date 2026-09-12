
from dataclasses import dataclass
import os
from typing import List, Type, Tuple, Dict, Any
import numpy as np
from concurrent.futures import ProcessPoolExecutor

from catan.ids import N_PLAYERS, NONE_PLAYER
from catan.state import GameState
from players.player import Player

from game.runner import pure_runner


@dataclass
class BatchSummary:
    games: int
    turn_win_rates: List[int]
    player_win_rates: List[int]    
    avg_steps: List[int]
    avg_turns: List[int]


@dataclass
class BatchRunnerSettings:
    player_cls_paths: List[str]
    num_games: int = 1_000
    base_seed: int = 42
    max_steps: int = 10_000
    verbose: bool = False

    shuffle: bool = False

    chunksize: int = 2


def _worker(args: Tuple[int, List[str], int, int]) -> Tuple[Dict[str, Any],GameState]:
    seed, cls_paths, max_steps, offset = args
    result, _ = pure_runner(seed, cls_paths, max_steps, offset, return_gs=False)
    return result, None


def _multi_worker(args: Tuple[List[int], List[str], int, List[int]]) -> List[Dict[str, Any]]:
    from game.engine import Engine
    from catan.engine import playable_moves, apply_action_inplace
    from catan.ids import NONE_PLAYER
    from game.runner import _resolve_class

    seeds, cls_paths, max_steps, offsets = args
    PlayerClasses = [_resolve_class(p) for p in cls_paths]  # resolve once per worker

    results = []
    for seed, offset in zip(seeds, offsets):
        eng = Engine(seed=int(seed))
        gs = eng.gs
        rotated = PlayerClasses[offset:] + PlayerClasses[:offset]
        # Derive each player's stream from the game seed so the run is reproducible.
        players = [rotated[i](player_id=i, seed=int(seed) * len(rotated) + i)
                   for i in range(len(rotated))]
        steps = 0
        while gs.winner == NONE_PLAYER and steps < max_steps:
            moves = playable_moves(gs)
            a = players[int(gs.current_player_idx)].decide(gs, moves)
            apply_action_inplace(gs, a, eng.rng)
            steps += 1
        results.append({"steps": steps, "winner": int(gs.winner), "turns": gs.turn_index, "offset": offset})
    return results


def run_batch(settings: BatchRunnerSettings) -> Tuple[BatchSummary, List[Dict[str,Any]]]:
    player_cls_paths = settings.player_cls_paths
    num_games = settings.num_games

    assert len(settings.player_cls_paths) == 4, "Need four players!"

    rng = np.random.default_rng(settings.base_seed)
    seeds = rng.integers(1, 2**31 - 1, size=num_games, dtype=np.int64).tolist()
    offsets = rng.integers(0,4,size=num_games, dtype=np.int64).tolist() if settings.shuffle else [0]*num_games

    chunksize = settings.chunksize
    chunks = [range(i, min(i + chunksize, num_games)) for i in range(0, num_games, chunksize)]
    worker_args = [
        ([int(seeds[i]) for i in chunk], player_cls_paths, settings.max_steps, [int(offsets[i]) for i in chunk])
        for chunk in chunks
    ]

    with ProcessPoolExecutor(os.cpu_count()) as ex:
        output = list(ex.map(_multi_worker, worker_args))

    results = [r for slab_results in output for r in slab_results]
    gamestates = []

    turn_wins = np.zeros((N_PLAYERS+1), dtype=np.int32) # 0..3 Players, 4 No Winner
    player_wins = np.zeros((N_PLAYERS+1), dtype=np.int32) # 0..3 Players, 4 No Winner

    for result in results:
        winner = int(result["winner"])
        player_winner = int((winner + result["offset"]) % N_PLAYERS)

        if winner == NONE_PLAYER:
            turn_wins[4] += 1
            player_wins[4] += 1
        else:
            turn_wins[winner] += 1
            player_wins[player_winner] += 1
        
    avg_steps = float(np.mean([d["steps"] for d in results])) if results else 0.0
    avg_turns = float(np.mean([d["turns"] for d in results])) if results else 0.0

    summary = BatchSummary(
        games=num_games,
        turn_win_rates=[w / num_games for w in turn_wins],
        player_win_rates=[w / num_games for w in player_wins],
        avg_steps=avg_steps,
        avg_turns=avg_turns
    )

    return summary,results,gamestates
