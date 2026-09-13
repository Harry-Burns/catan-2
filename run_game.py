"""Watch a game in the browser.

    python run_game.py

Starts the display server if it isn't already up, then opens an interactive
game you can drive from either the web page or this terminal. Press ? in the
browser for the full list of shortcuts.
"""

import os
import webbrowser

from display.web.web_app import ensure_server
from game.engine import Engine
from game.runner import GameRunner
from players.player import Player, RandomPlayer, RandomPlayerDistributed, RandomDistributedNoTrades
from players.player_jsettlers import JSettlersPlayer

# Set CATAN_OPEN_BROWSER=0 to keep the page from popping open on every run.
OPEN_BROWSER = os.environ.get("CATAN_OPEN_BROWSER", "1") != "0"

if __name__ == "__main__":
    url = ensure_server()
    print(f"Display: {url}")
    if OPEN_BROWSER:
        webbrowser.open(url)

    engine = Engine()
    players = [
        JSettlersPlayer(0),
        JSettlersPlayer(1),
        JSettlersPlayer(2),
        JSettlersPlayer(3),
    ]

    runner = GameRunner(engine, players)
    runner.play_game_interactive(delay=0.4, verbose=False, display=True)
