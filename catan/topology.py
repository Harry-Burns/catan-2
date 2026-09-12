from dataclasses import dataclass
import numpy as np

from functools import lru_cache

from catan.ids import DESERT

@dataclass(frozen=True)
class BoardTopology:
    ## - Hexes
    hex_resource: np.ndarray     # (N_HEX,)  int8  -> {0..4}=res, 5=desert
    hex_number:   np.ndarray     # (N_HEX,)  int8  -> {2..12} or 0 for none
    hex_settlement_ix:  np.ndarray     # (N_HEX,6) int16 -> settlement ids around each hex
    hex_road_ix:  np.ndarray     # (N_HEX,6) int16 -> road ids around each hex

    ## - Graphs
    # Settlements
    settlement_adj_settlement: np.ndarray    # (N_SETT,3) int16 -> neighboring settlement ids (-1 if none)
    settlement_adj_roads: np.ndarray   # (N_SETT,3) int16 -> incident road ids

    # Roads
    road_adj_settlement: np.ndarray    # (N_ROAD,2) int16 -> endpoint settlement ids
    road_adj_roads: np.ndarray   # (N_ROAD,4) int16 -> neighboring road ids (-1 if <4)
    road_adj_hex:   np.ndarray   # (N_ROAD,2) int16 -> adjacent hex ids (-1 if none)

    ## - Ports (bitmasks for players live in dynamic state; below are geometry hooks)
    port_settlement_ix: np.ndarray     # (N_PORT,2) int16 -> settlements touching each port
    port_res_id:  np.ndarray     # (N_PORT,)  int8  -> 0..4 for 2:1, or -1 for 3:1-any

    def __post_init__(self):
        """Python-native mirrors of the (frozen) lookup tables.

        Every hot path indexes these 19/54/72-entry tables one element at a time;
        at that size numpy scalar boxing costs far more than it saves.
        """
        o = object.__setattr__

        def pos(row):
            """Row as plain ints, with the -1 padding dropped."""
            return tuple(int(v) for v in row if v >= 0)

        o(self, "py_hex_settlement", tuple(pos(r) for r in self.hex_settlement_ix))
        o(self, "py_sett_adj_sett", tuple(pos(r) for r in self.settlement_adj_settlement))
        o(self, "py_sett_adj_roads", tuple(pos(r) for r in self.settlement_adj_roads))
        o(self, "py_road_adj_sett", tuple(tuple(int(v) for v in r) for r in self.road_adj_settlement))
        o(self, "py_hex_resource", tuple(int(v) for v in self.hex_resource))
        o(self, "py_hex_number", tuple(int(v) for v in self.hex_number))

        # dice total -> ((resource, (settlement ids...)), ...) for every producing hex
        rolls = {}
        for hid, num in enumerate(self.py_hex_number):
            res = self.py_hex_resource[hid]
            if num == 0 or res == DESERT:
                continue
            rolls.setdefault(num, []).append((hid, res, self.py_hex_settlement[hid]))
        o(self, "py_roll_hexes", {k: tuple(v) for k, v in rolls.items()})

        # settlement id -> port bit (0 = 3:1 any, res+1 = 2:1), or None
        port_bit = {}
        for pid_, sids in enumerate(self.port_settlement_ix):
            res = int(self.port_res_id[pid_])
            bit = 0 if res == -1 else res + 1
            for sid in sids:
                if int(sid) >= 0:
                    port_bit.setdefault(int(sid), bit)
        o(self, "py_port_bit", port_bit)


## --- Constants

ROWS = 5
COLS = 9

HEX_EXCLUDED = [(0,0), (0,8), (4,0), (4,8)]
EDGE_OFFSETS = np.array([(-1,1),(0,2),(1,1),(1,-1),(0,-2),(-1,-1)], dtype=np.int8) # All 6 Edges. For each edge, +3 to get the opposite
VERTICIE_GROUPS = np.array([[(-1,-1), (-1,1)],[(-1,1), (0,2)],[(0,2), (1,1)],[(1,1), (1,-1)],[(1,-1), (0,-2)],[(0,-2), (-1,-1)]], dtype=np.int8) # All 6 Verticies

PORT_ROADS = np.array([5, 6, 19, 27, 46, 52, 60, 64, 67])
#PORT_LOCATIONS = np.array([(0,5), (1,0), (6, 0), (11, 1), (15,2), (17,2), (16,3), (12,4), (3,4)], dtype=np.int8) # [(hex_id, road_index)]

def _is_hex(x,y):
    if not(0 <= x < ROWS and 0 <= y < COLS):
        return False
    if (x + y) % 2 != 0:
        return False
    if (x,y) in HEX_EXCLUDED:
        return False
    return True

def _relative_settlement_index(offset:tuple[int,int], shared:tuple[int,int]) -> int:
    v1x,v1y = offset; v2x,v2y = shared
    target = {(-v1x, -v1y), (-v1x+v2x, -v1y+v2y)}
    for idx,grp in enumerate(VERTICIE_GROUPS):
        if target == {tuple(grp[0]), tuple(grp[1])}: return idx
    raise RuntimeError("relative settlement index not found")

@lru_cache(maxsize=1)
def generate_topology() -> BoardTopology:
    # 1) Grid & hex IDs
    grid = np.full((ROWS, COLS), -1, dtype=np.int16)
    hex_coords = []
    hid = 0
    for x in range(ROWS):
        for y in range(COLS):
            if _is_hex(x,y):
                grid[x,y] = hid
                hex_coords.append((x,y))
                hid += 1
    N_HEX = hid

    hex_road_ix = np.full((N_HEX,6), -1, dtype=np.int16)
    hex_settlement_ix = np.full((N_HEX,6), -1, dtype=np.int16)

    # 2) Roads (unique per shared edge)
    next_road = 0
    for h,(x,y) in enumerate(hex_coords):
        for e,(dx,dy) in enumerate(EDGE_OFFSETS):
            if hex_road_ix[h,e] != -1: continue
            nx,ny = x+dx, y+dy
            opp = (e+3)%6
            if _is_hex(nx,ny):
                h2 = grid[nx,ny]
                if hex_road_ix[h2,opp] != -1:
                    hex_road_ix[h,e] = hex_road_ix[h2,opp]
                    continue
            rid = next_road; next_road += 1
            hex_road_ix[h,e] = rid
            if _is_hex(nx,ny):
                hex_road_ix[grid[nx,ny], opp] = rid
    N_ROAD = next_road

    # 3) Settlements (unique per shared vertex)
    next_sett = 0
    for h,(x,y) in enumerate(hex_coords):
        for v in range(6):
            if hex_settlement_ix[h,v] != -1: continue
            placed = False
            pair = VERTICIE_GROUPS[v]
            pair_rev = pair[::-1]
            for off, shared in (pair, pair_rev):
                nx,ny = x+int(off[0]), y+int(off[1])
                if _is_hex(nx,ny):
                    h2 = grid[nx,ny]
                    rel = _relative_settlement_index(tuple(off), tuple(shared))
                    if hex_settlement_ix[h2, rel] != -1:
                        hex_settlement_ix[h,v] = hex_settlement_ix[h2, rel]
                        placed = True
                        break
            if placed: continue
            sid = next_sett; next_sett += 1
            hex_settlement_ix[h,v] = sid
    N_SETT = next_sett

    # 4) Settlement adjacencies
    touch_hex = [[] for _ in range(N_SETT)]
    for h in range(N_HEX):
        for v in range(6):
            sid = hex_settlement_ix[h,v]
            if sid != -1: touch_hex[sid].append(h)

    settlement_adj_settlement = np.full((N_SETT,3), -1, dtype=np.int16)
    settlement_adj_roads      = np.full((N_SETT,3), -1, dtype=np.int16)
    for sid in range(N_SETT):
        nset, nrds = set(), set()
        for h in touch_hex[sid]:
            v = int(np.where(hex_settlement_ix[h] == sid)[0][0])
            nset.add(int(hex_settlement_ix[h,(v+1)%6]))
            nset.add(int(hex_settlement_ix[h,(v-1)%6]))
            nrds.add(int(hex_road_ix[h,v]))
            nrds.add(int(hex_road_ix[h,(v-1)%6]))
        nset.discard(-1); nset.discard(sid); nrds.discard(-1)
        nlist = list(nset)[:3]; rlist = list(nrds)[:3]
        settlement_adj_settlement[sid,:len(nlist)] = nlist
        settlement_adj_roads[sid,:len(rlist)] = rlist

    # 5) Road endpoints / neighbor roads / touching hexes
    road_adj_settlement = np.full((N_ROAD,2), -1, dtype=np.int16)
    road_adj_roads      = np.full((N_ROAD,4), -1, dtype=np.int16)
    road_adj_hex        = np.full((N_ROAD,2), -1, dtype=np.int16)

    for h in range(N_HEX):
        for e in range(6):
            rid = hex_road_ix[h,e]
            if rid == -1: continue
            if road_adj_hex[rid,0] == -1: road_adj_hex[rid,0] = h
            elif road_adj_hex[rid,1] == -1 and road_adj_hex[rid,0] != h: road_adj_hex[rid,1] = h

    for rid in range(N_ROAD):
        h = int(road_adj_hex[rid,0])
        e = int(np.where(hex_road_ix[h] == rid)[0][0])
        sA = int(hex_settlement_ix[h,e])
        sB = int(hex_settlement_ix[h,(e+1)%6])
        road_adj_settlement[rid] = (sA, sB)
        neigh = set()
        if sA != -1: neigh.update(settlement_adj_roads[sA])
        if sB != -1: neigh.update(settlement_adj_roads[sB])
        neigh.discard(-1); 
        if rid in neigh: neigh.remove(rid)
        lst = list(neigh)[:4]
        road_adj_roads[rid,:len(lst)] = lst

    # 6) Ports → which settlements they touch
    n_port = PORT_ROADS.shape[0]
    port_settlement_ix = np.full((n_port, 2), -1, dtype=np.int16)

    for pid, r in enumerate(PORT_ROADS.astype(np.int16)):
        sA, sB = map(int, road_adj_settlement[int(r)])
        port_settlement_ix[pid] = (sA, sB)

    # (optional sanity)
    assert np.all(port_settlement_ix >= 0), "Some ports didn't resolve to settlements"

    # 7) Assemble topology (randomized fields are placeholders; fill during init_game)
    topo = BoardTopology(
        hex_resource=np.zeros(N_HEX, dtype=np.int8),               # fill in initialize_game()
        hex_number=np.zeros(N_HEX, dtype=np.int8),                 # fill in initialize_game()
        hex_settlement_ix=hex_settlement_ix,
        hex_road_ix=hex_road_ix,
        settlement_adj_settlement=settlement_adj_settlement,
        settlement_adj_roads=settlement_adj_roads,
        road_adj_settlement=road_adj_settlement,
        road_adj_roads=road_adj_roads,
        road_adj_hex=road_adj_hex,
        port_settlement_ix=port_settlement_ix,
        port_res_id=np.full(n_port, -1, dtype=np.int8),            # fill in initialize_game()
    )
    return topo