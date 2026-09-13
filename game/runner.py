import random
import time
import traceback
from importlib import import_module
from typing import Optional

from catan.ids import PLY2STR, NONE_PLAYER, N_PLAYERS
from catan.actions import PROMPT2STR, RESPONSE2STR, unpack_action

from game.engine import Engine
from catan.engine import playable_moves
from players.player import Player

DISPLAY_URL = "http://127.0.0.1:8000"

# How often the runner talks to the display server while idling. Low enough
# that a button press in the browser feels immediate, high enough that a paused
# runner isn't spinning on HTTP.
SYNC_PERIOD = 0.06
# After a failed request, wait this long before trying the server again rather
# than eating a connection error on every tick.
RETRY_PERIOD = 2.0


class WebLink:
    """Connection to the display server, which may simply not be running.

    Every method is best-effort: if the server is down the link goes quiet and
    retries occasionally, so a game never dies because a viewer was closed.
    """

    def __init__(self, base: str = DISPLAY_URL):
        self.base = base.rstrip("/")
        self.available = True
        self._next_retry = 0.0
        self._session = None
        self._adapter = None

    def _load(self):
        """Import the heavy display deps only once something actually needs them."""
        if self._session is None:
            import requests
            from display.web import api_adapter
            self._session = requests.Session()
            self._adapter = api_adapter
        return self._session, self._adapter

    def _usable(self) -> bool:
        if self.available:
            return True
        if time.time() >= self._next_retry:
            self.available = True          # probe again
            return True
        return False

    def _failed(self, exc: Exception, what: str) -> None:
        if self.available:
            print(f"[display] {what} failed ({type(exc).__name__}); "
                  f"retrying every {RETRY_PERIOD:.0f}s. Is web_app running?")
        self.available = False
        self._next_retry = time.time() + RETRY_PERIOD

    def push(self, gs, move_log=None, roll_counts=None, runner=None) -> bool:
        """Send the current board state. Returns whether it landed."""
        if not self._usable():
            return False
        try:
            session, adapter = self._load()
            board = adapter.gamestate2api(
                gs, move_log=move_log, roll_counts=roll_counts, runner=runner)
            resp = session.post(f"{self.base}/state",
                                json={"board": board.model_dump()}, timeout=2.0)
            if resp.status_code != 200:
                print(f"[display] POST /state -> {resp.status_code}: {resp.text[:200]}")
                return False
            self.available = True
            return True
        except Exception as exc:
            self._failed(exc, "state push")
            return False

    def sync(self, runner) -> list[dict]:
        """Publish runner status, collect whatever the browser queued up."""
        if not self._usable():
            return []
        try:
            session, _ = self._load()
            resp = session.post(f"{self.base}/sync",
                                json={"runner": runner.model_dump()}, timeout=2.0)
            if resp.status_code != 200:
                return []
            self.available = True
            return resp.json().get("commands", [])
        except Exception as exc:
            self._failed(exc, "sync")
            return []


_link: Optional[WebLink] = None


def _get_link() -> WebLink:
    global _link
    if _link is None:
        _link = WebLink()
    return _link


def display_function(game_state, move_log=None, roll_counts=None, runner=None):
    """Push `game_state` to the web display. No-op if the display isn't up."""
    _get_link().push(game_state, move_log=move_log,
                     roll_counts=roll_counts, runner=runner)


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
    def __init__(self, engine: Engine, player_controllers: list[Player], record_moves: bool = True):
        self.engine = engine
        self.player_controllers = player_controllers

        self.steps = 0
        self.max_steps = 100000

        # Decoded history for the display. `gs.action_log` keeps only packed
        # ints, so the actor and the dice result have to be captured as we go.
        self.recorder = None
        if record_moves:
            try:
                from display.web.movelog import MoveRecorder
                self.recorder = MoveRecorder()
            except Exception:
                self.recorder = None

    # ------------------------------------------------------------------ #
    #  Status                                                              #
    # ------------------------------------------------------------------ #
    @property
    def finished(self) -> bool:
        return self.engine.gs.winner != NONE_PLAYER or self.steps >= self.max_steps

    def _runner_status(self, playing: bool = False, delay: float = 0.0):
        from display.web.api_adapter import RunnerStatus
        return RunnerStatus(
            connected=True,
            playing=playing,
            delay=round(float(delay), 3),
            steps=self.steps,
            max_steps=self.max_steps,
            finished=self.finished,
            seed=getattr(self.engine, "seed", None),
            players=[type(p).__name__ for p in self.player_controllers],
        )

    def _push(self, playing: bool = False, delay: float = 0.0):
        display_function(
            self.engine.gs,
            move_log=self.recorder.as_dicts() if self.recorder else [],
            roll_counts=self.recorder.roll_counts if self.recorder else [],
            runner=self._runner_status(playing, delay),
        )

    # ------------------------------------------------------------------ #
    #  Playing                                                             #
    # ------------------------------------------------------------------ #
    def play_game(self, pause=False, delay=0, display=False, verbose=True):
        try:
            while self.engine.gs.winner == NONE_PLAYER and self.steps < self.max_steps:
                if verbose:
                    print(f"\nLongest Road: {self.engine.gs.longest_road_owner} | Largest Army: {self.engine.gs.largest_army_owner}")
                self.play_action(verbose=verbose)
                self.steps += 1

                if display:
                    self._push()
                if pause:
                    if input("Continue to Next Action? - Press Enter to Confirm:").upper() == "NO":
                        break
                elif delay:
                    time.sleep(delay)

        except KeyboardInterrupt:
            print("\n! Game interrupted by user !")
        except Exception as e:
            print(f"\n!ERROR!\nError: {e}\nTraceback: {traceback.format_exc()}")

        if verbose:
            print("! Game Over !")
            winner = self.engine.gs.winner
            print(f"! Winner: {winner}: {PLY2STR[winner] if winner != NONE_PLAYER else 'None'} !")

    def _advance(self, n=1, display=True, verbose=False, playing=False, delay=0.0):
        """Run up to `n` moves. Only refresh the display after the last one."""
        for _ in range(n):
            if self.finished:
                break
            self.play_action(verbose=verbose)
            self.steps += 1
        if display:
            self._push(playing=playing, delay=delay)

    def restart(self, seed: Optional[int] = None):
        """Start a fresh game with the same player controllers."""
        if seed is None:
            seed = random.randint(1, 10_000)
        self.engine = Engine(seed=int(seed))
        self.steps = 0
        if self.recorder:
            self.recorder.reset()
        return seed

    def _status_line(self):
        gs = self.engine.gs
        pid = gs.current_player_idx
        return (f"[move {self.steps}] {PLY2STR[pid].upper()} to act "
                f"| prompt: {PROMPT2STR[gs.prompt]} "
                f"| LR: {gs.longest_road_owner} LA: {gs.largest_army_owner}")

    # ------------------------------------------------------------------ #
    #  Interactive viewer                                                  #
    # ------------------------------------------------------------------ #
    def play_game_interactive(self, delay=0.5, display=True, verbose=False,
                              web_control=True, quiet=False):
        """Watch the game, driving it from the browser or the terminal.

        Browser: open http://127.0.0.1:8000/board and use the control bar.

        Terminal keys:
            -> / n / Enter : advance one move
            p / space      : toggle autoplay
            f              : fast-forward -- prompts for how many moves to skip
            + / -          : increase / decrease autoplay delay
            d              : force a display refresh
            R              : restart with a fresh board
            q / Esc        : quit
        """
        try:
            import msvcrt
            msvcrt.kbhit()      # raises unless stdin is a real console
        except Exception:
            msvcrt = None       # notebook / piped stdin: browser control only

        SPECIALS = {b'M': 'RIGHT', b'K': 'LEFT', b'H': 'UP', b'P': 'DOWN'}

        def key_pressed():
            """Non-blocking: a key token if one is waiting, else None."""
            if msvcrt is None:
                return None
            try:
                if not msvcrt.kbhit():
                    return None
                ch = msvcrt.getch()
            except Exception:
                return None
            if ch in (b'\x00', b'\xe0'):            # arrow / function key prefix
                return SPECIALS.get(msvcrt.getch(), '')
            if ch == b'\r':
                return 'ENTER'
            if ch == b'\x1b':
                return 'ESC'
            return ch.decode('utf-8', 'ignore')

        def prompt_int(msg, default=10):
            try:
                raw = input(msg).strip()
                return int(raw) if raw else default
            except (ValueError, EOFError):
                return default

        if not quiet:
            print(self.play_game_interactive.__doc__)
            if web_control:
                print(f"  Display: {DISPLAY_URL}/board")

        link = _get_link()
        playing = False
        quitting = False
        next_move_at = 0.0
        last_sync = 0.0

        if display:
            self._push(playing=playing, delay=delay)
        if not quiet:
            print(self._status_line())

        if msvcrt is None and not web_control:
            # No keyboard polling and no browser: fall back to running it out.
            self.play_game(display=display, verbose=verbose)
            return

        while not quitting:
            try:
                now = time.time()

                # --- commands from the browser ---------------------------- #
                if web_control and now - last_sync >= SYNC_PERIOD:
                    last_sync = now
                    for command in link.sync(self._runner_status(playing, delay)):
                        cmd = command.get("cmd")
                        value = command.get("value", 0.0) or 0.0

                        if cmd == "step":
                            playing = False
                            n = max(1, int(value or 1))
                            self._advance(n, display=display, verbose=verbose, delay=delay)
                            if not quiet:
                                print(self._status_line())
                        elif cmd == "play":
                            playing = True
                            next_move_at = time.time()
                        elif cmd == "pause":
                            playing = False
                        elif cmd == "toggle":
                            playing = not playing
                            next_move_at = time.time()
                        elif cmd == "speed":
                            delay = max(0.0, round(float(value), 3))
                        elif cmd == "restart":
                            seed = self.restart(int(value) if value else None)
                            playing = False
                            if not quiet:
                                print(f"-- restarted with seed {seed} --")
                            self._push(playing=playing, delay=delay)
                        elif cmd == "quit":
                            quitting = True
                        if quitting:
                            break
                    if quitting:
                        break

                # --- keys from the terminal -------------------------------- #
                key = key_pressed()
                if key is not None:
                    if key in ('RIGHT', 'ENTER', 'n', ''):
                        playing = False
                        self._advance(1, display=display, verbose=verbose, delay=delay)
                        if not quiet:
                            print(self._status_line())
                    elif key in ('p', ' '):
                        playing = not playing
                        next_move_at = time.time()
                        if not quiet:
                            print(f"{'playing' if playing else 'paused'} at {delay}s/move")
                    elif key == 'f':
                        playing = False
                        n = prompt_int("Skip how many moves? [10]: ", default=10)
                        if not quiet:
                            print(f"skipping {n} moves...")
                        self._advance(n, display=display, verbose=verbose, delay=delay)
                        if not quiet:
                            print(self._status_line())
                    elif key.isdigit():
                        playing = False
                        self._advance(int(key), display=display, verbose=verbose, delay=delay)
                        if not quiet:
                            print(self._status_line())
                    elif key == '+':
                        delay = round(delay + 0.1, 2)
                        print(f"delay = {delay}s/move")
                    elif key == '-':
                        delay = round(max(0.0, delay - 0.1), 2)
                        print(f"delay = {delay}s/move")
                    elif key == 'd':
                        self._push(playing=playing, delay=delay)
                        if not quiet:
                            print(self._status_line())
                    elif key == 'R':
                        seed = self.restart()
                        playing = False
                        print(f"-- restarted with seed {seed} --")
                        self._push(playing=playing, delay=delay)
                    elif key in ('q', 'ESC'):
                        print("Quitting viewer")
                        break

                # --- autoplay ---------------------------------------------- #
                if playing:
                    if self.finished:
                        playing = False
                        self._push(playing=playing, delay=delay)
                    elif now >= next_move_at:
                        self._advance(1, display=display, verbose=verbose,
                                      playing=True, delay=delay)
                        next_move_at = time.time() + delay
                        if not quiet:
                            print(self._status_line())

                time.sleep(0.005)

            except KeyboardInterrupt:
                print("\n! Game interrupted by user !")
                break
            except Exception as e:
                print(f"\n!ERROR!\nError: {e}\nTraceback: {traceback.format_exc()}")
                break

        winner = self.engine.gs.winner
        if winner != NONE_PLAYER:
            print(f"! Game Over! Winner: {winner}: {PLY2STR[winner]} !")
        if display:
            self._push(playing=False, delay=delay)

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

        if self.recorder is not None:
            from display.web.movelog import snapshot
            pre = snapshot(gs)
            self.engine.step(selected_action)
            self.recorder.record(pre, selected_action, gs)
        else:
            self.engine.step(selected_action)
