"""Web display server.

Three parties talk to this process:

* the **runner** (`game/runner.py`) pushes board state to ``POST /state`` and
  drains queued browser commands from ``POST /sync``;
* the **browser** polls ``GET /state/version`` (a few bytes) and only refetches
  ``GET /state`` when the version moves, then posts control commands to
  ``POST /control``;
* nothing else. There is no auth here -- it binds to 127.0.0.1 and is a local
  debugging tool.
"""

import time
from collections import deque
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from display.web.api_adapter import (
    ExtendedBoardState, HexState, RunnerStatus,
)

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
INDEX_HTML = HERE / "catan_board.html"

# Commands the browser may send. Anything else is rejected.
VALID_COMMANDS = {"step", "play", "pause", "toggle", "speed", "restart", "quit"}


class Input(BaseModel):
    board: ExtendedBoardState


class Command(BaseModel):
    cmd: str
    value: float = 0.0


class Sync(BaseModel):
    """Runner heartbeat: status up, pending commands down."""
    runner: RunnerStatus


class _Store:
    """Everything the server holds, which is one board and one command queue."""

    def __init__(self):
        self.board = ExtendedBoardState(
            hexes=[HexState(id=i, resource="desert", number=None, hasRobber=False)
                   for i in range(19)],
        )
        # Bumped on every state push; the browser polls this instead of the
        # whole board, which is ~40 KB once every node and edge is included.
        self.version = 0
        self.commands: deque[Command] = deque(maxlen=64)
        self.runner = RunnerStatus()
        # Wall-clock of the last runner contact. The page uses its age to tell
        # "paused" apart from "nothing is driving the game".
        self.runner_seen_at: Optional[float] = None

    def publish(self, board: ExtendedBoardState) -> None:
        self.board = board
        self.runner = board.runner
        self.runner_seen_at = time.monotonic()
        self.version += 1

    def touch_runner(self, runner: RunnerStatus) -> None:
        self.runner = runner
        self.runner_seen_at = time.monotonic()

    def runner_age(self) -> Optional[float]:
        if self.runner_seen_at is None:
            return None
        return round(time.monotonic() - self.runner_seen_at, 3)

    def drain(self) -> list[Command]:
        out = list(self.commands)
        self.commands.clear()
        return out


store = _Store()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Page ------------------------------------------------------------------ #
@app.get("/", response_class=HTMLResponse)
@app.get("/board", response_class=HTMLResponse)
async def display_board():
    if not INDEX_HTML.is_file():
        raise HTTPException(status_code=500, detail=f"Missing {INDEX_HTML}")
    return FileResponse(str(INDEX_HTML))


# --- State ----------------------------------------------------------------- #
@app.post("/state")
async def set_board(payload: Input):
    store.publish(payload.board)
    return {"ok": True, "version": store.version}


@app.get("/state")
async def get_board():
    return store.board


@app.get("/state/version")
async def get_version():
    """Tiny polling endpoint so the browser only refetches on real changes."""
    return {
        "version": store.version,
        "board_id": store.board.game_info.board_id if store.board.game_info else "",
        "runner": store.runner,
        "runner_age": store.runner_age(),
    }


# --- Control --------------------------------------------------------------- #
@app.post("/control")
async def post_control(command: Command):
    if command.cmd not in VALID_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown command: {command.cmd!r}")
    store.commands.append(command)
    return {"ok": True, "queued": len(store.commands)}


@app.post("/sync")
async def sync(payload: Sync):
    """Called by the runner on every tick: publish status, collect commands."""
    store.touch_runner(payload.runner)
    return {"commands": [c.model_dump() for c in store.drain()]}


@app.get("/runner")
async def get_runner():
    return store.runner


def serve(host: str = "127.0.0.1", port: int = 8000, log_level: str = "warning"):
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def is_up(host: str = "127.0.0.1", port: int = 8000, timeout: float = 0.4) -> bool:
    """Is something already listening there?"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def ensure_server(host: str = "127.0.0.1", port: int = 8000,
                  log_level: str = "warning") -> str:
    """Start the display in a daemon thread unless it is already running.

    Lets `python run_game.py` be the only command you need. Returns the board
    URL either way; an already-running server is left alone, so re-running a
    script does not fight over the port.
    """
    import threading

    url = f"http://{host}:{port}/board"
    if is_up(host, port):
        return url

    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    # Signal handlers can only be installed on the main thread.
    server.install_signal_handlers = lambda: None

    thread = threading.Thread(target=server.run, daemon=True, name="catan-display")
    thread.start()

    for _ in range(100):                       # up to ~5s for the socket to bind
        if server.started or is_up(host, port, timeout=0.1):
            break
        time.sleep(0.05)
    return url


if __name__ == "__main__":
    serve(log_level="info")

# --- To run this application (from the project root): ---
#   python -m display.web.web_app
# Then open http://127.0.0.1:8000/board
