"""Human-readable move history for the web display.

`gs.action_log` stores only packed action ints: no actor, no dice result, no
outcome. Replaying it to recover those is more work than watching the game as
it happens, so the runner hands us a small snapshot taken *before* each step
and we diff it against the state that comes back out.

Nothing here is on the engine's hot path -- `GameRunner.play_action` is the
only caller, and only when a display is attached.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from catan.actions import (
    PROMPT2STR, RESPONSE2STR, unpack_action, unpack_port_trade, unpack_table_trade,
    SETUP_RESPONSE, ROLL, PLAY_KNIGHT, PLAY_MONOPOLY, PLAY_YEAR_OF_PLENTY,
    PLAY_ROAD_BUILDER, PASS, BUILD_ROAD, BUILD_SETTLEMENT, BUILD_CITY,
    PURCHASE_DEV_CARD, PORT_TRADE, TABLE_TRADE_PROPOSE, TABLE_TRADE_SELECT,
    TABLE_TRADE_ACCEPT, TABLE_TRADE_REJECT, DISCARD_RESOURCE,
    SELECT_ROBBER_RESPONSE,
)
from catan.ids import N_PLAYERS, N_RES, NONE_PLAYER, PLY2STR, RES2STR
from catan.state import GameState

# Which coloured lane the entry sits in on the UI side.
CAT_SETUP, CAT_ROLL, CAT_BUILD = "setup", "roll", "build"
CAT_DEV, CAT_TRADE, CAT_ROBBER, CAT_TURN = "dev", "trade", "robber", "turn"

_CATEGORY = {
    SETUP_RESPONSE: CAT_SETUP,
    ROLL: CAT_ROLL,
    BUILD_ROAD: CAT_BUILD, BUILD_SETTLEMENT: CAT_BUILD, BUILD_CITY: CAT_BUILD,
    PURCHASE_DEV_CARD: CAT_DEV, PLAY_KNIGHT: CAT_DEV, PLAY_MONOPOLY: CAT_DEV,
    PLAY_YEAR_OF_PLENTY: CAT_DEV, PLAY_ROAD_BUILDER: CAT_DEV,
    PORT_TRADE: CAT_TRADE, TABLE_TRADE_PROPOSE: CAT_TRADE,
    TABLE_TRADE_SELECT: CAT_TRADE, TABLE_TRADE_ACCEPT: CAT_TRADE,
    TABLE_TRADE_REJECT: CAT_TRADE,
    DISCARD_RESOURCE: CAT_ROBBER, SELECT_ROBBER_RESPONSE: CAT_ROBBER,
    PASS: CAT_TURN,
}


@dataclass
class Snapshot:
    """The handful of values that only exist *before* an action is applied."""
    actor: int
    turn: int
    prompt: int
    hands: tuple[tuple[int, ...], ...]
    robber_hex: int
    dev_deck_size: int


def snapshot(gs: GameState) -> Snapshot:
    return Snapshot(
        actor=int(gs.current_player_idx),
        turn=int(gs.turn_index),
        prompt=int(gs.prompt),
        hands=tuple(tuple(int(v) for v in p.hand) for p in gs.players),
        robber_hex=int(gs.board.robber_hex),
        dev_deck_size=int(len(gs.board.dev_deck)),
    )


@dataclass
class MoveEntry:
    n: int
    turn: int
    player: str
    player_id: int
    prompt: str
    action: str
    category: str
    text: str
    dice: Optional[tuple[int, int]] = None
    deltas: dict[str, str] = field(default_factory=dict)


def _res_phrase(counts) -> str:
    """[0,2,0,1,0] becomes '2 brick, 1 wheat'."""
    parts = [f"{int(c)} {RES2STR[i]}" for i, c in enumerate(counts) if int(c)]
    return ", ".join(parts) if parts else "nothing"


def _delta_phrase(before, after) -> str:
    """'+2 wood, -1 ore', or '' when the hand is unchanged."""
    parts = []
    for i in range(N_RES):
        d = after[i] - before[i]
        if d:
            parts.append(f"{d:+d} {RES2STR[i]}")
    return ", ".join(parts)


class MoveRecorder:
    """Rolling window of decoded moves, newest last."""

    def __init__(self, limit: int = 400):
        self.limit = limit
        self.entries: deque[MoveEntry] = deque(maxlen=limit)
        self.n = 0
        self.roll_counts = [0] * 13   # index = dice total

    def reset(self) -> None:
        self.entries.clear()
        self.n = 0
        self.roll_counts = [0] * 13

    def record(self, pre: Snapshot, a: int, gs: GameState) -> MoveEntry:
        action, arg1, arg2 = unpack_action(a)
        self.n += 1

        after = tuple(tuple(int(v) for v in p.hand) for p in gs.players)
        dice = None
        text = RESPONSE2STR[action] if action < len(RESPONSE2STR) else str(action)

        if action == SETUP_RESPONSE:
            text = f"places settlement N{arg1} and road E{arg2}"

        elif action == ROLL:
            dice = gs.last_dice_roll
            total = sum(dice) if dice else None
            if total is not None and 2 <= total <= 12:
                self.roll_counts[total] += 1
            if total == 7:
                text = f"rolls {dice[0]}+{dice[1]} = 7 - robber"
            else:
                text = f"rolls {dice[0]}+{dice[1]} = {total}" if dice else "rolls"

        elif action == BUILD_ROAD:
            text = f"builds road E{arg1}"
        elif action == BUILD_SETTLEMENT:
            text = f"builds settlement N{arg1}"
        elif action == BUILD_CITY:
            text = f"upgrades N{arg1} to a city"
        elif action == PURCHASE_DEV_CARD:
            text = "buys a development card"

        elif action == PLAY_KNIGHT:
            text = "plays a Knight"
        elif action == PLAY_MONOPOLY:
            gained = after[pre.actor][arg1] - pre.hands[pre.actor][arg1]
            text = f"plays Monopoly on {RES2STR[arg1]} (+{gained})"
        elif action == PLAY_YEAR_OF_PLENTY:
            text = f"plays Year of Plenty: {RES2STR[arg1]} + {RES2STR[arg2]}"
        elif action == PLAY_ROAD_BUILDER:
            text = f"plays Road Building: E{arg1}, E{arg2}"

        elif action == PORT_TRADE:
            give, rate, take = unpack_port_trade(a)
            text = f"trades {rate} {RES2STR[give]} for 1 {RES2STR[take]} ({rate}:1)"
        elif action == TABLE_TRADE_PROPOSE:
            give, take = unpack_table_trade(a)
            text = f"offers {_res_phrase(give)} for {_res_phrase(take)}"
        elif action == TABLE_TRADE_ACCEPT:
            text = "accepts the offer"
        elif action == TABLE_TRADE_REJECT:
            text = "rejects the offer"
        elif action == TABLE_TRADE_SELECT:
            if arg1 >= N_PLAYERS:
                text = "closes the offer with no trade"
            else:
                text = f"trades with {PLY2STR[arg1].upper()}"

        elif action == DISCARD_RESOURCE:
            text = f"discards 1 {RES2STR[arg1]}"
        elif action == SELECT_ROBBER_RESPONSE:
            victim = "" if arg2 == NONE_PLAYER or arg2 >= N_PLAYERS \
                     else f", robs {PLY2STR[arg2].upper()}"
            text = f"moves the robber to H{arg1}{victim}"

        elif action == PASS:
            text = "ends their turn"

        # Every hand that moved, so a roll shows who collected what.
        deltas = {}
        for pid in range(N_PLAYERS):
            phrase = _delta_phrase(pre.hands[pid], after[pid])
            if phrase:
                deltas[PLY2STR[pid]] = phrase

        entry = MoveEntry(
            n=self.n,
            turn=pre.turn,
            player=PLY2STR[pre.actor],
            player_id=pre.actor,
            prompt=PROMPT2STR[pre.prompt] if pre.prompt < len(PROMPT2STR) else str(pre.prompt),
            action=RESPONSE2STR[action] if action < len(RESPONSE2STR) else str(action),
            category=_CATEGORY.get(action, CAT_TURN),
            text=text,
            dice=dice,
            deltas=deltas,
        )
        self.entries.append(entry)
        return entry

    def as_dicts(self, limit: int = 150) -> list[dict]:
        """Newest `limit` entries, oldest first."""
        items = list(self.entries)[-limit:]
        return [
            {
                "n": e.n, "turn": e.turn, "player": e.player, "player_id": e.player_id,
                "prompt": e.prompt, "action": e.action, "category": e.category,
                "text": e.text, "dice": list(e.dice) if e.dice else None,
                "deltas": e.deltas,
            }
            for e in items
        ]
