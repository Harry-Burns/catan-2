import time
import traceback

from catan.ids import PLY2STR, NONE_PLAYER
from catan.actions import PROMPT2STR, RESPONSE2STR, unpack_action

from game.engine import Engine
from catan.engine import playable_moves
from players.player import Player


import requests
from web.web_app import gamestate2api, GameState, Input
def display_function(game_state: GameState):
    board_state = gamestate2api(game_state)
    payload = Input(board=board_state)

    resp = requests.post(
        "http://127.0.0.1:8000/state",
        json=payload.dict(),
    )
    
    if resp.status_code != 200:
        print(resp.status_code)



class GameRunner:
    def __init__(self, engine: Engine, player_controllers: list[Player]):
        self.engine = engine
        self.player_controllers = player_controllers

    def play_game(self, pause=False, delay=0, display=False):
        try:
            while self.engine.gs.winner == NONE_PLAYER:
                self.play_action()

                if display:
                    display_function(self.engine.gs)
                if pause:
                    if input("Continue to Next Action? - Press Enter to Confirm:").upper() == "NO":
                        break
                elif delay:
                    time.sleep(delay)

        except KeyboardInterrupt as ki:
            print("\n⛔ Game interrupted by user ⛔")
        except Exception as e:
            print(f"\n⛔ERROR⛔\nError: {e}\nTraceback: {traceback.format_exc()}")
        print("⛔ Game Over! ⛔")
        print(f"⛔ Winner: {self.engine.gs.winner} ⛔")

    def play_action(self):
        gs = self.engine.gs
        pid = gs.current_player_idx
        player_ctrl = self.player_controllers[pid]

        print(f"\nPlayer: {pid}:\"{PLY2STR[pid].upper()}\" | Action Prompt: \"{PROMPT2STR[gs.prompt]}\"")

        possible_moves = playable_moves(gs)
        selected_action = player_ctrl.decide(gs, possible_moves)

        action_name,_,_ = unpack_action(selected_action)
        print(f"Selected Action: \"{RESPONSE2STR[action_name]}\"")
        print(f"Action Number: {len(gs.action_log)}")

        self.engine.step(selected_action)