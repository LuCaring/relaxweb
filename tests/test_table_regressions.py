"""麻将、掼蛋状态与牌型回归：python3 tests/test_table_regressions.py。"""
import asyncio
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from games.base import create_room
from games.guandan import beats, find_moves, resolve_combo
from games.mahjong import best_score


def card(rank, suit=0):
    return {"r": rank, "s": suit}


async def room_for(game, rules=None):
    room = create_room(game, room_id=1, name="回归测试", owner="a", buy_in=200, blind=1, rules=rules)
    for name in "abcd":
        room.add_member(name, 200)

    async def noop(*args, **kwargs):
        pass

    room.broadcast_views = room.broadcast_payload = room.on_rooms_changed = noop
    await room.start()
    room.cancel_timers()
    return room


class GuandanRules(unittest.TestCase):
    def test_level_does_not_raise_sequences(self):
        for copies, kind in [(1, "straight"), (2, "pairs_seq"), (3, "triple_seq")]:
            length = 5 if copies == 1 else 3 if copies == 2 else 2
            low = [card(r, s % 4) for r in range(3, 3 + length) for s in range(copies)]
            high = [card(r, (s + r) % 4) for r in range(4, 4 + length) for s in range(copies)]
            if copies == 1:
                low[0]["s"] = 1
            a = resolve_combo(low, None, {2, 2 + length})
            b = resolve_combo(high, None, {2, 2 + length})
            self.assertEqual(a["type"], kind)
            self.assertEqual(b["type"], kind)
            self.assertTrue(beats(b, a))
        low = resolve_combo([card(r) for r in range(3, 8)], None, {7})
        high = resolve_combo([card(r) for r in range(4, 9)], None, {7})
        self.assertTrue(beats(high, low))

    def test_bombs_available_with_wild_elsewhere_in_hand(self):
        hand = [card(9, s) for s in range(4)] + [card(9), card(8, 1)]
        moves = find_moves(hand, 8, {8}, None)
        self.assertEqual({m["len"] for m in moves if m["type"] == "bomb"}, {4, 5})
        kings = [card(16, 4)] * 2 + [card(17, 4)] * 2
        self.assertTrue(any(m["type"] == "king_bomb" for m in find_moves(hand + kings, 8, {8}, None)))
        self.assertIsNone(resolve_combo([card(9, s) for s in range(3)] + [card(8, 1)], 8, {8}))

    def test_wild_can_be_its_natural_rank_in_a_bomb(self):
        combo = resolve_combo([card(8, s) for s in range(4)], 8, {8})
        self.assertEqual(combo["type"], "bomb")
        self.assertEqual(len(combo["cards"]), 4)

    def test_hints_can_be_played_against_same_standing(self):
        hand = [card(3), card(3, 1), card(4), card(4, 1), card(8, 1), card(8, 1)]
        standing = resolve_combo([card(3, s) for s in range(3)] + [card(4, s) for s in range(3)], None, {8})
        for move in find_moves(hand, 8, {8}, standing):
            resolved = resolve_combo(move["cards"], 8, {8}, standing)
            self.assertIsNotNone(resolved)
            self.assertTrue(beats(resolved, standing))


class TableState(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()

    async def test_guandan_view_contains_comparison_fields(self):
        room = await room_for("guandan")
        room.game["hands"]["a"] = [card(7), card(8)]
        await room.perform_action("a", "play", {"cards": [0]})
        standing = room.view_for("b")["standing"]
        self.assertEqual((standing["tier"], standing["main"], standing["len"]), (0, [1, 7], 1))

    def test_guandan_combos_are_ordered_by_structure(self):
        cases = [
            ([card(9, 0), card(7, 0), card(9, 1), card(7, 1), card(9, 2)],
             [9, 9, 9, 7, 7]),
            ([card(3, 0), card(4, 0), card(5, 0), card(3, 1), card(4, 1), card(5, 1)],
             [3, 3, 4, 4, 5, 5]),
            ([card(3, 0), card(4, 0), card(3, 1), card(4, 1), card(3, 2), card(4, 2)],
             [3, 3, 3, 4, 4, 4]),
            ([card(7, 2), card(3, 0), card(6, 0), card(4, 1), card(5, 3)],
             [3, 4, 5, 6, 7]),
        ]
        for cards, expected in cases:
            actual = resolve_combo(cards, None, {2})
            self.assertEqual([item["r"] for item in actual["cards"]], expected)

        wild = card(8, 1)
        actual = resolve_combo(
            [card(3, 0), card(7, 0), card(3, 1), card(7, 2), wild], 8, {8})
        self.assertEqual([item["r"] for item in actual["cards"]], [8, 7, 7, 3, 3])

        actual = resolve_combo(
            [card(3, 0), card(3, 1), card(3, 2), card(14, 0), card(2, 1)],
            2, {2})
        self.assertEqual([item["r"] for item in actual["cards"]], [3, 3, 3, 2, 14])

    async def test_casual_game_views_include_player_avatars(self):
        for game in ("guandan", "mahjong"):
            room = await room_for(game)
            room.player_avatar = lambda username: f"https://example.test/{username}.png"
            players = room.view_for("a")["players"]
            self.assertEqual(
                [player["avatar"] for player in players],
                [f"https://example.test/{name}.png" for name in room.seating],
            )

    async def test_king_bomb_settles_even_as_last_four_cards(self):
        for previous_finish in ([], ["c"], ["b"]):
            room = await room_for("guandan")
            room.game["hands"]["a"] = [card(16, 4)] * 2 + [card(17, 4)] * 2
            room.game["finish"] = previous_finish
            await room.perform_action("a", "play", {"cards": [0, 1, 2, 3]})
            self.assertFalse(room.in_hand())
            self.assertEqual(room.game["result"]["finish"], ["a", "c", "b", "d"])
            self.assertEqual(room.game["result"]["gain"], 3)

    async def test_bad_indices_do_not_mutate_hands(self):
        for game in ("guandan", "mahjong"):
            room = await room_for(game)
            before = copy.deepcopy(room.game["hands"])
            for index in (True, 0.5, "0", None, -1, 200):
                payload = {"cards": [index]} if game == "guandan" else {"index": index}
                await room.perform_action("a", "play" if game == "guandan" else "discard", payload)
            if game == "guandan":
                for indices in (1, "0", {}, [0, 0]):
                    await room.perform_action("a", "play", {"cards": indices})
            self.assertEqual(room.game["hands"], before)

    async def test_mahjong_claim_submission_and_pause(self):
        room = await room_for("mahjong")
        g = room.game
        g["phase"] = "claim"
        g["claim"] = {"mode": "discard", "tile": 4, "by": "a", "options": {"b": {"peng": True}, "c": {"hu": True}}, "passed": set(), "claims": {}}
        await room.perform_action("b", "claim", {"kind": "peng"})
        self.assertTrue(room.options_for("b")["submitted"])
        self.assertIsNone(room.options_for("b")["claim"])
        room.paused = True
        self.assertIsNone(room.options_for("c")["claim"])
        await room.perform_action("c", "claim", {"kind": "hu"})
        self.assertNotIn("c", g["claim"]["claims"])

    async def test_kong_clears_claim_and_claimed_discard_marker(self):
        room = await room_for("mahjong")
        g = room.game
        g["hands"]["b"] = [4, 4, 4, 1, 2, 3, 9, 10, 11, 18, 19, 20, 27]
        g["discards"]["a"] = [4, 4]
        g["last_discard"] = {"by": "a", "tile": 4}
        g["claim"] = {"tile": 4}
        await room._apply_gang("b", "a", 4)
        self.assertIsNone(g["claim"])
        self.assertIsNone(g["last_discard"])
        self.assertEqual(g["discards"]["a"], [4])
        view = room.view_for("b")
        self.assertEqual(view["your_draw_index"], len(view["your_hand"]) - 1)
        self.assertIsNone(room.view_for("a")["your_draw_index"])

    async def test_no_kong_without_replacement_tile(self):
        room = await room_for("mahjong")
        g = room.game
        g["wall"] = []
        g["hands"]["a"] = [4] * 4 + [3]
        g["hands"]["b"] = [4] * 3
        g["melds"]["a"] = [{"type": "peng", "tiles": [3] * 3, "from": "d"}]
        before = copy.deepcopy(g)
        await room.perform_action("a", "angang", {"index": 0})
        await room.perform_action("a", "bugang", {"index": 4})
        self.assertEqual(g, before)
        self.assertFalse(room.claim_options("a", 4).get("b", {}).get("gang"))

    async def test_last_visible_tile_context(self):
        room = await room_for("mahjong")
        g = room.game
        g["discards"] = {"a": [4, 4], "b": [4], "c": [], "d": []}
        self.assertTrue(room.make_ctx("c", 4, zimo=True)["juezhang"])
        self.assertFalse(room.make_ctx("c", 4, zimo=False)["juezhang"])
        g["discards"]["d"].append(4)
        self.assertTrue(room.make_ctx("c", 4, zimo=False)["juezhang"])

    async def test_robbing_kong_uses_bonus_for_eligibility_and_moves_tile(self):
        # 只考察抢杠加番，不让开局随机花牌把基础分推过 8 分门槛。
        room = await room_for("mahjong", {"flowers": False})
        g = room.game
        self.assertEqual(g["flowers"]["b"], [])
        g["hands"]["a"] = [4]
        g["melds"]["a"] = [{"type": "peng", "tiles": [4] * 3, "from": "d"}]
        g["hands"]["b"] = [0, 1, 2, 12, 13, 14, 23, 24, 25, 13, 14, 15, 4]
        g["melds"]["b"] = []
        g["discards"] = {name: [] for name in "abcd"}
        ctx = room.make_ctx("b", 4, zimo=False)
        self.assertLess(best_score(g["hands"]["b"] + [4], [], ctx)[1], 8)
        await room.perform_action("a", "bugang", {"index": 0})
        self.assertTrue(g["claim"]["options"]["b"]["hu"])
        for name in tuple(g["claim"]["options"]):
            if name != "b":
                await room.perform_action(name, "pass")
        await room.perform_action("b", "claim", {"kind": "hu"})
        self.assertEqual(g["hands"]["a"], [])
        self.assertEqual(g["melds"]["a"][0]["type"], "peng")
        self.assertEqual(g["result"]["winner"], "b")
        self.assertEqual(len(g["result"]["hands"]["b"]), 14)


if __name__ == '__main__':
    unittest.main()
