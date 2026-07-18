import time
from multiprocessing import Pool, cpu_count

def run_game_mine(_):
    from game.engine import Engine
    from game.runner import GameRunner
    from players.player import RandomDistributedNoTrades

    players = [
        RandomDistributedNoTrades(0),
        RandomDistributedNoTrades(1),
        RandomDistributedNoTrades(2),
        RandomDistributedNoTrades(3)
    ]
    engine = Engine()
    runner = GameRunner(engine, players)
    runner.play_game(verbose=False)
    return runner.engine.gs.turn_index

def run_game_catanatron(_):
    from catanatron import Game, RandomPlayer, Color

    players = [
        RandomPlayer(Color.RED),
        RandomPlayer(Color.BLUE),
        RandomPlayer(Color.WHITE),
        RandomPlayer(Color.ORANGE),
    ]
    game = Game(players)
    game.play()
    return game.state.num_turns

def benchmark(fn, n, label):
    with Pool(cpu_count()) as pool:
        start = time.perf_counter()
        results = pool.map(fn, range(n))
        elapsed = time.perf_counter() - start

    avg_turns = sum(results) / len(results)
    print(f"\n{label}")
    print(f"  {n} games in {elapsed:.2f}s")
    print(f"  {n / elapsed:.0f} games/sec")
    print(f"  avg turns/game: {avg_turns:.1f}")

if __name__ == "__main__":
    NUM_GAMES = 2000

    benchmark(run_game_mine,        NUM_GAMES, "My Engine")
    benchmark(run_game_catanatron,  NUM_GAMES, "Catanatron")