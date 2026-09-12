# Rule verification

A wrapper you put a game inside. It re-derives everything the engine computes —
production, placement legality, longest road, harbours, victory points — from the
board and the rulebook, then checks the engine's answer after **every single
action**.

Nothing here is fast and none of it is meant to be. It exists to be run now and
then and answer one question: *is the engine still playing Catan?*

The rules are transcribed from the official 2020 base-game rulebook:
<https://www.catan.com/sites/default/files/2021-06/catan_base_rules_2020_200707.pdf>

## Running it

```bash
python tools/verify.py                      # 25 games, JSettlers bot
python tools/verify.py --games 200
python tools/verify.py --players chaos      # uniform over every legal move
python tools/verify.py --stop-on-finding    # halt at the first RULE finding
```

`--players chaos` is the one that finds things. A bot that plays well never
visits the odd corners — bank shortages, road-builder edge cases, seven-card
discards with an empty bank — and that is where the bugs are.

Check the checker:

```bash
python tools/verify_selftest.py
```

That breaks the engine on purpose, one fault at a time, and asserts the right
finding comes out. A checker that never fires looks exactly like a checker that
works.

## Using it in code

Wrap a whole game:

```python
from game.engine import Engine
from players.player_jsettlers import JSettlersPlayer
from verification import VerifiedGame

report = VerifiedGame(Engine(seed=1), [JSettlersPlayer(i) for i in range(4)], seed=1).play()
print(report.summary())
```

Or drive your own loop:

```python
from verification import GameVerifier

v = GameVerifier(seed=1)
v.begin(gs)
while not over:
    moves = playable_moves(gs)
    v.before_move(gs, moves)          # audits the legal-move list
    action = player.decide(gs, moves)
    apply_action_inplace(gs, action, rng)
    v.after_move(gs, action)          # audits the transition, then the position
report = v.finish(gs)
```

`report.counts()` gives per-code totals, `report.by_severity(...)` the stored
examples, `report.ok` is true when nothing worse than a deviation was found.

## How findings are graded

| Severity | Meaning |
|---|---|
| `CORRUPTION` | The position is internally impossible — cards conjured or lost, a counter disagreeing with the board. Always an engine bug. |
| `RULE` | Self-consistent but against the rules of Catan, or a move offered/accepted that the rules forbid. |
| `DEVIATION` | A deliberate-looking simplification. Reported **once** per run with the rulebook text, so it is a decision on record rather than a surprise. |
| `NOTE` | Worth knowing, not wrong. |

A single broken invariant can fire on every step of every game, so the report
stores at most five examples per code and counts the rest — one noisy finding
cannot bury the others.

## What gets checked

**`board.py`** — once per game, before any move. Terrain mix (4/4/4/3/3 + desert),
the exact 18 number tokens with no 7, desert has no token, 9 harbours as 4 generic
plus one per resource, 54 intersections and 72 paths, adjacency tables symmetric
and consistent, every hex a hexagon, opening bank and deck composition, robber on
the desert. It also compares the engine's own constants in `catan/ids.py` against
the rulebook.

**`invariants.py`** — after every action. Resource conservation (19 of each, always),
development-card conservation including progress cards played and gone, piece
counters against what is actually on the board, piece limits, the distance rule at
every occupied intersection, settlements connected to their owner's roads, road
networks never in more than the two pieces setup creates, harbour masks matching
the buildings, Longest Road and Largest Army held by someone who could legitimately
hold them, victory points, and prompt/phase coherence.

**`transitions.py`** — for each action, the exact expected delta *and* that nothing
else moved. A build that charges the right resources but also silently clears a
trade offer is still a bug, and only a whole-state diff catches it. Production is
checked by asking which dice rolls could explain the payout: if none can, the
payout is wrong.

**`legality.py`** — the generated move list against an independently derived one,
in both directions. Offering an illegal move lets a bot cheat; omitting a legal
one means every bot is playing a subtly different game.

## A note on independence

`reference.py` never calls `catan.interface`. A check that asks the engine whether
the engine is right proves nothing. Likewise `rules.py` does not import
`catan.ids` — it states the rulebook's numbers itself, and the board auditor
compares the two.

The one deliberate exception is `LONGEST_ROAD_ALGORITHM`, which calls the engine's
`_calculate_longest_road` on purpose so that a wrong *algorithm* can be told apart
from a correct algorithm that simply has not been re-run.

## Layout

| File | Job |
|---|---|
| `rules.py` | The rulebook as constants, with citations. No engine imports. |
| `reference.py` | Independent re-derivations, and the plain-Python `Snapshot`. |
| `board.py` | Static board audit, run once per game. |
| `invariants.py` | Properties of any legal position, checked every step. |
| `transitions.py` | Per-action deltas and the whole-state diff. |
| `legality.py` | The move list, in both directions. |
| `verifier.py` | `GameVerifier`, `VerifiedGame`, `verify_games`, dice test. |
| `report.py` | `Violation`, `Severity`, `VerificationReport`. |
