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

def pure_runner(seed: int, player_cls_paths: list[str], max_steps: int = 10_000, offset: int=0, return_gs: bool=False):
    from game.engine import Engine
    from catan.engine import playable_moves, apply_action_inplace
    from catan.ids import NONE_PLAYER
    
    PlayerClasses = [_resolve_class(p) for p in player_cls_paths]
    eng = Engine(seed=int(seed))
    gs = eng.gs
    # Derive each player's stream from the game seed so the run is reproducible.
    players = [PlayerClasses[i](player_id=i, seed=int(seed) * N_PLAYERS + i)
               for i in range(N_PLAYERS)]

    steps = 0
    while gs.winner == NONE_PLAYER and steps < max_steps:
        moves = playable_moves(gs)
        a = players[int(gs.current_player_idx)].decide(gs, moves)
        apply_action_inplace(gs, a, eng.rng)
        steps += 1

    return {"steps": steps, "winner": int(gs.winner), "turns": gs.turn_index, "offset": offset},(gs if return_gs else None)
# --- ---------------------------- ---

class GameRunner:
    def __init__(self, engine: Engine, player_controllers: list[Player]):
        self.engine = engine
        self.player_controllers = player_controllers

        self.steps = 0; self.max_steps = 100000

    def play_game(self, pause=False, delay=0, display=False, verbose=True):
        try:
            while self.engine.gs.winner == NONE_PLAYER and self.steps < self.max_steps:
                if verbose:
                    print(f"\nLongest Road: {self.engine.gs.longest_road_owner} | Largest Army: {self.engine.gs.largest_army_owner}")
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

    # ------------------------------------------------------------------ #
    #  Interactive viewer                                                  #
    # ------------------------------------------------------------------ #
    def _advance(self, n=1, display=True, verbose=False):
        """Run up to `n` moves. Only refresh the display after the last one."""
        for i in range(n):
            if self.engine.gs.winner != NONE_PLAYER or self.steps >= self.max_steps:
                break
            self.play_action(verbose=verbose)
            self.steps += 1
        if display:
            display_function(self.engine.gs)

    def _status_line(self):
        gs = self.engine.gs
        pid = gs.current_player_idx
        return (f"[move {self.steps}] {PLY2STR[pid].upper()} to act "
                f"| prompt: {PROMPT2STR[gs.prompt]} "
                f"| LR: {gs.longest_road_owner} LA: {gs.largest_army_owner}")

    def play_game_interactive(self, delay=0.5, display=True, verbose=False):
        """Watch the game interactively.

        Controls (single keypress, no Enter needed on Windows):
            → / n / Enter : advance one move
            p             : toggle autoplay (press any key to pause)
            f             : fast-forward — prompts for how many moves to skip
            + / -         : increase / decrease autoplay delay
            d             : force a display refresh
            q / Esc       : quit
        """
        try:
            import msvcrt
        except ImportError:
            msvcrt = None

        SPECIALS = {b'M': 'RIGHT', b'K': 'LEFT', b'H': 'UP', b'P': 'DOWN'}

        def read_key_blocking():
            """Block until a key is pressed; return a token string ('' if unknown)."""
            if msvcrt is None:
                return input("cmd (enter=next, <n>=skip n, p=play, q=quit)> ").strip()
            ch = msvcrt.getch()
            if ch in (b'\x00', b'\xe0'):            # arrow / function key prefix
                return SPECIALS.get(msvcrt.getch(), '')
            if ch == b'\r':
                return 'ENTER'
            if ch == b'\x1b':
                return 'ESC'
            return ch.decode('utf-8', 'ignore')

        def key_pressed():
            """Non-blocking: return a key token if one is waiting, else None."""
            if msvcrt is None:
                return None
            if msvcrt.kbhit():
                return read_key_blocking()
            return None

        def prompt_int(msg, default=10):
            try:
                raw = input(msg).strip()
                return int(raw) if raw else default
            except (ValueError, EOFError):
                return default

        print(self.play_game_interactive.__doc__)
        if display:
            display_function(self.engine.gs)
        print(self._status_line())

        playing = False
        gs = self.engine.gs
        while gs.winner == NONE_PLAYER and self.steps < self.max_steps:
            try:
                if playing:
                    # advance one move, then wait `delay`s watching for a keypress
                    self._advance(1, display=display, verbose=verbose)
                    print(self._status_line())
                    t_end = time.time() + delay
                    key = None
                    while time.time() < t_end:
                        key = key_pressed()
                        if key is not None:
                            break
                        time.sleep(0.01)
                    if key is None:
                        continue          # timed out -> keep autoplaying
                    playing = False       # any key pauses
                else:
                    key = read_key_blocking()

                # -- dispatch --------------------------------------------------
                if key in ('RIGHT', 'ENTER', 'n', ' ', ''):
                    self._advance(1, display=display, verbose=verbose)
                    print(self._status_line())
                elif key == 'p':
                    playing = True
                    print(f"▶ playing at {delay}s/move — press any key to pause")
                elif key == 'f':
                    n = prompt_int("Skip how many moves? [10]: ", default=10)
                    print(f"⏩ skipping {n} moves…")
                    self._advance(n, display=display, verbose=verbose)
                    print(self._status_line())
                elif key.isdigit():
                    n = int(key)
                    self._advance(n, display=display, verbose=verbose)
                    print(self._status_line())
                elif key == '+':
                    delay = round(delay + 0.1, 2)
                    print(f"delay = {delay}s/move")
                elif key == '-':
                    delay = round(max(0.0, delay - 0.1), 2)
                    print(f"delay = {delay}s/move")
                elif key == 'd':
                    display_function(self.engine.gs)
                    print(self._status_line())
                elif key in ('q', 'ESC'):
                    print("⛔ Quitting viewer")
                    break

            except KeyboardInterrupt:
                print("\n⛔ Game interrupted by user ⛔")
                break
            except Exception as e:
                print(f"\n⛔ERROR⛔\nError: {e}\nTraceback: {traceback.format_exc()}")
                break

        winner = self.engine.gs.winner
        if winner != NONE_PLAYER:
            print(f"⛔ Game Over! Winner: {winner}: {PLY2STR[winner]} ⛔")

    def play_action(self, verbose=False):
        gs = self.engine.gs
        pid = gs.current_player_idx
        player_ctrl = self.player_controllers[pid]

        if verbose: print(f"\nPlayer: {pid}:\"{PLY2STR[pid].upper()}\" | Action Prompt: \"{PROMPT2STR[gs.prompt]}\"")

        possible_moves = playable_moves(gs)
        selected_action = player_ctrl.decide(gs, possible_moves)

        if verbose: 
            action_name,_,_ = unpack_action(selected_action)
            print(f"Selected Action: \"{RESPONSE2STR[action_name]}\"")
            print(f"Actions Taken: {len(gs.action_log)}")

        self.engine.step(selected_action)