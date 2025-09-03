from dataclasses import dataclass
import numpy as np
from typing import Optional

from catan.topology import BoardTopology

@dataclass
class BoardState:
    # Ownership
    road_owner: np.ndarray       # (N_ROAD,)  int8  -> {-1,0..3}
    settlement_owner: np.ndarray       # (N_SETT,)  int8  -> {-1,0..3}
    settlement_type:  np.ndarray       # (N_SETT,)  uint8 -> {EMPTY, SETTLEMENT, CITY}

    # Robber / decks / bank
    robber_hex: np.uint16
    dev_deck:   np.ndarray       # (N_DEV_TOTAL,) uint8 -> dev card ids (stack top = last index)
    bank_res:   np.ndarray       # (N_RES,) int16

@dataclass
class PlayerState:
    hand: np.ndarray             # (N_RES,) int8
    ports_mask: np.uint8         # bit0=3:1 any, bits1..5 = 2:1 per resource
    dev_cards: np.ndarray        # (N_DEV_TYPES,) int8  (counts)
    new_dev_cards: np.ndarray    # (N_DEV_TYPES,) int8  (counts)
    used_knights: np.uint8       # for largest army
    longest_road_len: np.uint8
    discards_required: np.uint8

    # Piece counts (for limits/scoring)
    roads_built: np.uint8
    settlements_built: np.uint8
    cities_built: np.uint8

@dataclass
class GameState:
    topology: BoardTopology
    board: BoardState
    players: list[PlayerState]

    current_player_idx: np.uint8
    current_player_turn_idx: np.uint8

    in_setup: bool
    setup_turn_idx: np.int16

    trade_offer_from: int              # -1 if none
    trade_offer_give: Optional[np.ndarray]  # (N_RES,) int8
    trade_offer_take: Optional[np.ndarray]  # (N_RES,) int8
    trade_accept_mask: Optional[np.ndarray] # (N_PLAYERS,) bool

    has_rolled: bool
    dev_card_used: bool
    winner: int  # -1 if none, else 0..3

    # Prompt / phase
    turn_index: np.int32
    prompt: np.uint8
    action_log: list[int]

    def clone_shallow(self) -> "GameState":
        return GameState(
            topology=self.topology,  # immutable, safe to share
            board=BoardState(
                road_owner=self.board.road_owner.copy(),
                settlement_owner=self.board.settlement_owner.copy(),
                settlement_type=self.board.settlement_type.copy(),
                robber_hex=self.board.robber_hex,
                dev_deck=self.board.dev_deck.copy(),
                bank_res=self.board.bank_res.copy(),
            ),
            players=[
                PlayerState(
                    hand=p.hand.copy(),
                    ports_mask=p.ports_mask,
                    dev_cards=p.dev_cards.copy(),
                    new_dev_cards=p.new_dev_cards.copy(),
                    used_knights=p.used_knights,
                    longest_road_len=p.longest_road_len,
                    discards_required=p.discards_required,
                    roads_built=p.roads_built,
                    settlements_built=p.settlements_built,
                    cities_built=p.cities_built,
                )
                for p in self.players
            ],
            current_player_idx=self.current_player_idx,
            current_player_turn_idx=self.current_player_turn_idx,
            in_setup=self.in_setup,
            setup_turn_idx=self.setup_turn_idx,
            trade_offer_from=self.trade_offer_from,
            trade_offer_give=None if self.trade_offer_give is None else self.trade_offer_give.copy(),
            trade_offer_take=None if self.trade_offer_take is None else self.trade_offer_take.copy(),
            trade_accept_mask=None if self.trade_accept_mask is None else self.trade_accept_mask.copy(),
            has_rolled=self.has_rolled,
            dev_card_used=self.dev_card_used,
            winner=self.winner,
            turn_index=self.turn_index,
            prompt=self.prompt,
            action_log=list(self.action_log),
        )
    
