from typing import Final
import numpy as np


## --- DEFINITIONS ---

## Resources
WOOD: Final[int]  = 0
BRICK: Final[int] = 1
SHEEP: Final[int] = 2
WHEAT: Final[int] = 3
ORE: Final[int]   = 4
N_RES: Final[int] = 5

RES2STR: Final[list[str]] = ["wood", "brick", "sheep", "wheat", "ore"]
STR2RES: Final[dict[str, int]] = {s: i for i, s in enumerate(RES2STR)}

ZERO_RES   = np.zeros(N_RES, dtype=np.int8)
ONE_HOT_RES    = np.eye(N_RES, dtype=np.int8)  # ONE_HOT[WOOD] -> [1,0,0,0,0]

# Tiles
DESERT = 5
N_TILES = 6

# Players
RED, BLUE, WHITE, ORANGE = 0, 1, 2, 3
N_PLAYERS = 4
NONE_PLAYER = 15

PLY2STR: Final[list[str]] = ["red", "blue", "white", "orange"]

# Settlements
EMPTY, SETTLEMENT, CITY = 0, 1, 2

# Developement Cards
KNIGHT, YEAR_OF_PLENTY, MONOPOLY, ROAD_BUILDER, VICTORY_POINT = 0, 1, 2, 3, 4
DEV2STR: Final[list[str]] = ["knight", "year_of_plenty", "monopoly", "road_building", "victory_point"]
N_DEV_CARDS: Final[int] = 5


## --- Ingame Definitions
OBJ_ROAD, OBJ_SETTLEMENT, OBJ_CITY, OBJ_DEV = range(4)
OBJ2STR: Final[list[str]] = ["road", "settlement", "city", "dev_card"]
N_OBJ: Final[int] = 4

COSTS = np.array([
    # WOOD BRICK SHEEP WHEAT ORE
    [  1,    1,    0,    0,    0 ],  # road
    [  1,    1,    1,    1,    0 ],  # settlement
    [  0,    0,    0,    2,    3 ],  # city
    [  0,    0,    1,    1,    1 ],  # development card
], dtype=np.int8)

VICTORY_POINTS_REQUIRED = 10
ROADS_ALLOWED = 15
SETTLEMENTS_ALLOWED = 5
CITIES_ALLOWED = 4
BANK_STOCK = 19

LONGEST_ROAD_MIN = 5
LARGEST_ARMY_MIN = 3