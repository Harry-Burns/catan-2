"""The official rules of Catan, as constants.

Everything here is transcribed from the 2020 base-game rulebook:
https://www.catan.com/sites/default/files/2021-06/catan_base_rules_2020_200707.pdf

This module deliberately does NOT import from `catan.ids`. The point of the
verifier is to check the engine against an independent statement of the rules,
so if it read the engine's own constants it would only prove the engine agrees
with itself. Where a value also exists in `catan.ids`, the verifier compares the
two and reports a mismatch.

Section references in comments are to that rulebook.
"""

# --- Resource indices -------------------------------------------------------
# The engine's ordering; the verifier checks this against catan.ids at startup.
WOOD, BRICK, SHEEP, WHEAT, ORE = 0, 1, 2, 3, 4
N_RESOURCES = 5
RESOURCE_NAMES = ("wood", "brick", "sheep", "wheat", "ore")

# --- Components (rulebook p.2, "Game Components") ---------------------------
N_PLAYERS = 4
N_HEXES = 19
N_INTERSECTIONS = 54          # standard 3-4 player board
N_PATHS = 72                  # standard 3-4 player board

# "95 resource cards" -- 19 per resource.
RESOURCE_CARDS_PER_TYPE = 19
TOTAL_RESOURCE_CARDS = 95

# 19 terrain hexes: 4 forest, 4 fields, 4 pasture, 3 hills, 3 mountains, 1 desert
TERRAIN_COUNTS = {
    WOOD: 4,     # forest -> lumber
    WHEAT: 4,    # fields -> grain
    SHEEP: 4,    # pasture -> wool
    BRICK: 3,    # hills -> brick
    ORE: 3,      # mountains -> ore
}
N_DESERT_HEXES = 1

# "The 18 number tokens are marked with the numerals 2 through 12. There is only
#  one 2 and one 12. There is no 7."  (Almanac, "Number Tokens")
NUMBER_TOKEN_COUNTS = {2: 1, 3: 2, 4: 2, 5: 2, 6: 2, 8: 2, 9: 2, 10: 2, 11: 2, 12: 1}
N_NUMBER_TOKENS = 18
NO_TOKEN = 7                  # 7 never appears on a hex

# "9 harbor pieces": 4 generic 3:1 and 5 specific 2:1, one per resource.
N_HARBOURS = 9
N_GENERIC_HARBOURS = 4
N_SPECIFIC_HARBOURS = 5

# "25 development cards (14 knight cards, 6 progress cards, 5 victory point
#  cards)"; the 6 progress cards are 2 each of road building, year of plenty
#  and monopoly (Almanac, "Progress Cards").
DEV_KNIGHT = 14
DEV_ROAD_BUILDING = 2
DEV_YEAR_OF_PLENTY = 2
DEV_MONOPOLY = 2
DEV_VICTORY_POINT = 5
TOTAL_DEV_CARDS = 25

# --- Per-player pieces (p.3, "Setting up the Game") -------------------------
MAX_ROADS = 15
MAX_SETTLEMENTS = 5
MAX_CITIES = 4

# --- Building costs (p.4-5, "Build") ----------------------------------------
# (wood, brick, sheep, wheat, ore)
COST_ROAD = (1, 1, 0, 0, 0)          # "Requires: Brick & Lumber"
COST_SETTLEMENT = (1, 1, 1, 1, 0)    # "Requires: Brick, Lumber, Wool, & Grain"
COST_CITY = (0, 0, 0, 2, 3)          # "Requires: 3 Ore & 2 Grain"
COST_DEV_CARD = (0, 0, 1, 1, 1)      # "Requires: Ore, Wool, & Grain"

# --- Victory (p.5, "Ending the Game") ---------------------------------------
VICTORY_POINTS_TO_WIN = 10
VP_SETTLEMENT = 1
VP_CITY = 2
VP_LONGEST_ROAD = 2
VP_LARGEST_ARMY = 2
VP_DEV_CARD = 1

# --- Special cards ----------------------------------------------------------
# "the first player to build a continuous road ... of at least 5 road segments"
LONGEST_ROAD_MIN = 5
# "The first player to have 3 knight cards in front of themself"
LARGEST_ARMY_MIN = 3

# --- The robber (p.5, "Rolling a 7 and Activating the Robber") --------------
# "every player who has more than 7 resource cards must select half
#  (rounded down) of their resource cards and return them to the bank"
DISCARD_THRESHOLD = 7

def discard_count(hand_size: int) -> int:
    """How many cards a player must discard on a 7."""
    return hand_size // 2 if hand_size > DISCARD_THRESHOLD else 0

# --- Maritime trade (p.4, "Maritime Trade") ---------------------------------
BANK_TRADE_RATE = 4          # always available, no harbour needed
GENERIC_HARBOUR_RATE = 3
SPECIFIC_HARBOUR_RATE = 2

# --- Setup (Almanac, "Set-up Phase") ----------------------------------------
SETUP_ROUNDS = 2
SETUP_PLACEMENTS = N_PLAYERS * SETUP_ROUNDS
# "Each player receives their starting resources immediately after building
#  their second settlement."
SECOND_SETTLEMENT_PRODUCES = True

# --- Dice -------------------------------------------------------------------
DICE_MIN, DICE_MAX = 2, 12
DICE_COUNT, DIE_FACES = 2, 6

# Ways to roll each total with 2d6 -- used to sanity-check the dice stream.
DICE_WAYS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
DICE_OUTCOMES = 36


# --- Rules the engine is known to model differently -------------------------
# These are reported as DEVIATION (once per run), not as internal corruption.
# Each entry: code -> (what the rulebook says, where).
KNOWN_RULE_TEXT = {
    "BANK_SHORTAGE": (
        "If there are not enough of a given resource in the supply to fulfill "
        "everyone's production, then no one receives any of that resource "
        "during that turn (unless it only affects 1 player).",
        "rulebook p.4, 'Resource Production'",
    ),
    "SETUP_PRODUCTION": (
        "Each player receives their starting resources immediately after "
        "building their second settlement. For each terrain hex adjacent to "
        "this second settlement, take a corresponding resource card.",
        "Almanac, 'Set-up Phase', Round Two",
    ),
    "LONGEST_ROAD_TIE": (
        "If you no longer have the longest road, but two or more players tie "
        "for the new longest road, set the Longest Road card aside. Do the "
        "same if no one has a 5+ segment road.",
        "Almanac, 'Longest Road', Special Case",
    ),
    "WIN_ON_OWN_TURN": (
        "If you have 10 or more victory points during your turn, the game ends "
        "and you are the winner.",
        "rulebook p.5, 'Ending the Game'",
    ),
    "TRADE_WITH_CURRENT_PLAYER": (
        "Players may only trade with the player whose turn it is. The other "
        "players may not trade among themselves.",
        "rulebook p.4, 'Domestic Trade'",
    ),
}
