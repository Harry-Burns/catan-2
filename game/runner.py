import time
import traceback
from importlib import import_module

from catan.ids import PLY2STR, NONE_PLAYER, N_PLAYERS
from catan.actions import PROMPT2STR, RESPONSE2STR, unpack_action

from game.engine import Engine
from catan.engine import playable_moves
from players.player import Player


def display_function(game_state):
    try:
        from display.web.web_app import gamestate2api, GameState, Input  # heavy deps, so import only when required
        import requests
    except Exception:
        return  # skip if UI not available

    board_state = gamestate2api(game_state)
    payload = Input(board=board_state)

    resp = requests.post(
        "http://127.0.0.1:8000/state",
        json=payload.dict(),
    )
    
    if resp.status_code != 200:
        print(resp.status_code)


# --- Used for quick-parallel runs ---
def _resolve_class(path: str):
    mod, name = path.split(":")
    return getattr(import_module(mod), name)

def pure_runner(seed: int, player_cls_paths: list[str], max_steps: int = 10_000, offset: int=0):
    from game.engine import Engine
    from catan.engine import playable_moves, apply_action_inplace
    from catan.ids import NONE_PLAYER
    
    PlayerClasses = [_resolve_class(p) for p in player_cls_paths]
    eng = Engine(seed=int(seed))
    gs = eng.gs
    players = [PlayerClasses[i](player_id=i) for i in range(N_PLAYERS)]

    steps = 0
    while gs.winner == NONE_PLAYER and steps < max_steps:
        moves = playable_moves(gs)
        a = players[int(gs.current_player_idx)].decide(gs, moves)
        apply_action_inplace(gs, a, eng.rng)
        steps += 1

    return {"steps": steps, "winner": int(gs.winner), "turns": gs.turn_index, "offset": offset}
# --- ---------------------------- ---

class GameRunner:
    def __init__(self, engine: Engine, player_controllers: list[Player]):
        self.engine = engine
        self.player_controllers = player_controllers

        self.steps = 0; self.max_steps = 100000

    def play_game(self, pause=False, delay=0, display=False, verbose=True):
        try:
            while self.engine.gs.winner == NONE_PLAYER and self.steps < self.max_steps:
                self.play_action(verbose=verbose)
                self.steps += 1

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
            
        if verbose:
            print("⛔ Game Over! ⛔")
            winner = self.engine.gs.winner
            print(f"⛔ Winner: {winner}: {PLY2STR[winner] if winner != NONE_PLAYER else 'None'} ⛔")

    def play_action(self, verbose=False):
        gs = self.engine.gs
        pid = gs.current_player_idx
        player_ctrl = self.player_controllers[pid]

        if verbose: print(f"\nPlayer: {pid}:\"{PLY2STR[pid].upper()}\" | Action Prompt: \"{PROMPT2STR[gs.prompt]}\"")

        possible_moves = playable_moves(gs)
        selected_action = player_ctrl.decide(gs, possible_moves)

        action_name,_,_ = unpack_action(selected_action)
        if verbose: print(f"Selected Action: \"{RESPONSE2STR[action_name]}\"")
        if verbose: print(f"Actions Taken: {len(gs.action_log)}")

        self.engine.step(selected_action)