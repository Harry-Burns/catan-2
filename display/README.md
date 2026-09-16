# Web display

A browser-based inspector for the engine. It draws the board, exposes the
engine's own ids for every hex, intersection and road slot, and lets you drive
the game from the page instead of the terminal.

```bash
python run_game.py          # starts the server if needed, opens the board
```

Or run the two halves separately:

```bash
python -m display.web.web_app     # terminal 1: server on :8000
python run_game.py                # terminal 2: the game
```

Then open <http://127.0.0.1:8000/board>.

## How the pieces fit together

```
GameRunner ──push state──▶  web_app  ◀──poll /state/version──  browser
    ▲                        (holds one board +                   │
    └────drain commands──────  one command queue)  ◀──POST /control
```

* `api_adapter.py` flattens a `GameState` into the JSON the page consumes.
* `movelog.py` records a readable history as the game plays. `gs.action_log`
  keeps only packed ints, so the actor, the dice result and who collected what
  have to be captured at step time.
* `web_app.py` is the only shared state: one board, one command queue, one
  version counter.
* `static/` holds the page. `board.js` draws the SVG, `panels.js` everything
  else, `main.js` polls and wires up the controls.

The browser polls `/state/version` (a few bytes) several times a second and
only refetches `/state` (~40 KB, since every node and edge is included) when the
version actually moves. Polling backs off while the tab is hidden.

Nothing here is on the engine's hot path: the adapter and the recorder only run
when a display is attached, and if the server isn't up the runner prints one
line and carries on.

## Endpoints

| Method | Path              | Used by | Purpose                                  |
|--------|-------------------|---------|------------------------------------------|
| GET    | `/board`, `/`     | browser | the page                                 |
| GET    | `/state`          | browser | full board state                         |
| GET    | `/state/version`  | browser | version counter + runner status (cheap)  |
| POST   | `/state`          | runner  | publish a new board                      |
| POST   | `/control`        | browser | queue a command                          |
| POST   | `/sync`           | runner  | heartbeat up, queued commands down       |
| GET    | `/runner`         | either  | runner status alone                      |

Commands: `step` (with `value` = how many), `play`, `pause`, `toggle`,
`speed` (seconds per move), `restart`, `quit`. Anything else is a 400.

## Overlays

| Toggle     | Key | Shows                                                        |
|------------|-----|--------------------------------------------------------------|
| Hex ids    | `H` | `H0`-`H18` on every tile                                      |
| Node ids   | `N` | all 54 intersection ids, empty ones included                  |
| Edge ids   | `E` | all 72 road-slot ids, empty ones included                     |
| Port ids   | `P` | `P0`-`P8`                                                     |
| Sites      | `G` | ghost markers on every unbuilt slot                           |
| Legal      | `L` | what the player holding the prompt may legally do             |
| Ownership  | `O` | hex rings and road glow coloured by owner                     |
| Inspect    | `I` | hover any hex/node/edge for its raw topology row              |
| Move log   | `M` | the history drawer                                            |
| Moves      | `A` | every playable move in this state, decoded and grouped        |
| Deck       | `K` | what is left in the dev deck (a spoiler, so it is off by default) |

`Sites` is independent of `Legal`: the legal overlay draws on its own layer, so
turning it on no longer forces the ghost markers on with it.

The `Moves` toggle swaps the "Legal moves" panel from per-action counts to the
moves themselves (`robber to H7, rob BLUE`, `2 ore -> 1 brick (2:1)`), grouped
by action. Hover a row for its packed action int, which is what you paste into a
test. Long prompts are capped at 400 listed moves; the count in the panel header
is always the true total.

Controls: `Space` play/pause, `→` step, `Shift+→` step ten, `+`/`-` faster /
slower, `Shift+R` restart, `0` reset zoom, `?` the full list. Scroll to zoom,
drag to pan, double-click to reset. Toggle states persist in `localStorage`.

The speed control is a **rate**, in moves per second, on a log scale from 0.5/s
up to 50/s with the top position meaning "no delay at all". The runner's own
protocol is still a delay in seconds per move -- the page converts. While
autoplaying, the readout also shows the rate actually achieved
(`7.1/s · 6.6 act`), which is the number to watch when profiling.

The terminal keys still work at the same time (`p`, `→`, `f`, `q`, and so on).

## Index rotation

The engine and the renderer number a hex's corners from different starting
points: the engine's corner 0 is the top vertex, the renderer's is the
lower-right one. The two differ by a constant:

```
display_index = (engine_index + 4) % 6
```

`api_adapter` applies this once, so everything reaching the page is already in
display order. `tools/test_display.py` recomputes the renderer's geometry in
Python and checks it against `catan/topology.py` — that shared corners land in
one place, that roads join their real endpoints, and that ports sit on the edge
between the two nodes that grant them.

```bash
python tools/test_display.py
```
