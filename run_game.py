from game.engine import Engine
from game.runner import GameRunner
from players.player import Player, RandomPlayer, RandomPlayerDistributed, RandomDistributedNoTrades
from players.player_jsettlers import JSettlersPlayer 

engine = Engine()
players = [
    JSettlersPlayer(0), 
    JSettlersPlayer(1), 
    JSettlersPlayer(2), 
    JSettlersPlayer(3)
]

runner = GameRunner(engine, players)
runner.play_game_interactive(delay=0.4, verbose=True, display=True)