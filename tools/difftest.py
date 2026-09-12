"""Step-for-step differential test between two working trees.

Use this when changing the engine for performance: it proves the new tree makes
exactly the same game out of the same inputs, rather than merely a plausible one.

Both trees are imported into separate module namespaces and driven by the *same*
seeded move picker. After every single action the full observable GameState is
compared field by field; the first divergence is printed and that game stops.

    python tools/difftest.py <baseline_tree> <candidate_tree> [n_games]

To get a baseline tree to compare against:

    git worktree add ../catan-baseline main
    python tools/difftest.py ../catan-baseline . 200
"""
import importlib
import random
import sys

MODULES = (
    "catan", "catan.ids", "catan.actions", "catan.topology", "catan.state",
    "catan.interface", "catan.engine", "catan.init",
    "game", "game.engine", "players", "players.player",
)

TOP_LEVEL = ("catan", "game", "players")


def load(root):
    """Import the engine from `root` and hand back its private module namespace."""
    for name in list(sys.modules):
        if name.split(".")[0] in TOP_LEVEL:
            del sys.modules[name]
    sys.path.insert(0, root)
    try:
        engine = importlib.import_module("game.engine")
        rules = importlib.import_module("catan.engine")
        ids = importlib.import_module("catan.ids")
        ns = {n: sys.modules[n] for n in MODULES if n in sys.modules}
        return engine, rules, ids, ns
    finally:
        sys.path.remove(root)


def activate(ns):
    sys.modules.update(ns)


def snapshot(gs):
    """Everything an observer could tell apart, as plain Python."""
    b = gs.board
    return (
        b.road_owner.tolist(), b.settlement_owner.tolist(), b.settlement_type.tolist(),
        int(b.robber_hex), b.dev_deck.tolist(), b.bank_res.tolist(),
        [(p.hand.tolist(), int(p.ports_mask), p.dev_cards.tolist(), p.new_dev_cards.tolist(),
          int(p.used_knights), int(p.longest_road_len), int(p.discards_required),
          int(p.roads_built), int(p.settlements_built), int(p.cities_built))
         for p in gs.players],
        int(gs.current_player_idx), int(gs.current_player_turn_idx), bool(gs.in_setup),
        int(gs.setup_turn_idx), int(gs.trade_offer_from),
        None if gs.trade_offer_give is None else gs.trade_offer_give.tolist(),
        None if gs.trade_offer_take is None else gs.trade_offer_take.tolist(),
        None if gs.trade_accept_mask is None else gs.trade_accept_mask.tolist(),
        int(gs.longest_road_owner), int(gs.largest_army_owner),
        bool(gs.has_rolled), bool(gs.dev_card_used), int(gs.winner),
        int(gs.turn_index), int(gs.prompt), list(gs.action_log),
    )


FIELDS = (
    "road_owner", "settlement_owner", "settlement_type", "robber_hex", "dev_deck",
    "bank_res", "players", "current_player_idx", "current_player_turn_idx", "in_setup",
    "setup_turn_idx", "trade_offer_from", "trade_offer_give", "trade_offer_take",
    "trade_accept_mask", "longest_road_owner", "largest_army_owner", "has_rolled",
    "dev_card_used", "winner", "turn_index", "prompt", "action_log",
)


def main(base_root, cand_root, n_games, max_steps=10_000):
    base = load(base_root)
    cand = load(cand_root)
    none_player = base[2].NONE_PLAYER
    failures = 0

    for game in range(n_games):
        seed = 5000 + game
        picker = random.Random(seed)

        activate(base[3]); eb = base[0].Engine(seed=seed); gb = eb.gs
        activate(cand[3]); ec = cand[0].Engine(seed=seed); gc = ec.gs

        activate(base[3])
        if snapshot(gb) != snapshot(gc):
            print(f"game {game}: initial state differs")
            failures += 1
            continue

        steps = 0
        while int(gb.winner) == none_player and steps < max_steps:
            activate(base[3]); mb = [int(x) for x in base[1].playable_moves(gb)]
            activate(cand[3]); mc = [int(x) for x in cand[1].playable_moves(gc)]
            if mb != mc:
                print(f"game {game} step {steps}: move list differs "
                      f"(prompt={int(gb.prompt)})")
                print(f"  baseline  n={len(mb)} {mb[:12]}")
                print(f"  candidate n={len(mc)} {mc[:12]}")
                failures += 1
                break

            action = mb[picker.randrange(len(mb))]
            activate(base[3]); base[1].apply_action_inplace(gb, action, eb.rng)
            activate(cand[3]); cand[1].apply_action_inplace(gc, action, ec.rng)

            sb, sc = snapshot(gb), snapshot(gc)
            if sb != sc:
                print(f"game {game} step {steps}: state differs after action {action}")
                for name, x, y in zip(FIELDS, sb, sc):
                    if x != y:
                        print(f"  {name}:\n    baseline  {x!r}\n    candidate {y!r}")
                failures += 1
                break
            steps += 1

    ok = failures == 0
    print(f"\n{n_games} games differential-tested: "
          f"{'ALL IDENTICAL' if ok else f'{failures} FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2],
                  int(sys.argv[3]) if len(sys.argv) > 3 else 50))
