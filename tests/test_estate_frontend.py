#!/usr/bin/env python3
"""小胖庄园前端装配与双端输入的静态契约。"""
from pathlib import Path
import json
import struct
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class EstateFrontendTests(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_solo_hall_route_is_metadata_driven(self):
        config = self.read("assets/js/game-config.js")
        hall = self.read("assets/js/hall.js")
        core = self.read("assets/js/core.js")
        self.assertIn('id: "estate"', config)
        self.assertIn('mode: "solo"', config)
        self.assertIn('view: "estate"', config)
        self.assertIn("ROOM_GAME_TYPES", config)
        self.assertIn('game.mode === "solo" ? game.view : "rooms"', hall)
        self.assertIn('renderView(state.hallPage)', core)
        self.assertNotIn('game.id === "estate"', hall)

    def test_entry_reaches_all_estate_modules_and_style(self):
        main = self.read("assets/js/main.js")
        page = self.read("game.html")
        self.assertIn('import "./estate/view.js"', main)
        self.assertIn('assets/css/estate.css?v=', page)
        for name in ("state", "protocol", "input", "art", "assets", "sprites", "map", "ui", "fishing", "mining", "view"):
            self.assertTrue((ROOT / f"assets/js/estate/{name}.js").is_file())

    def test_user_pixel_assets_are_complete_and_game_ready(self):
        from estate.catalog import CROPS, FISH, FISHING_TREASURES, MINERALS

        root = ROOT / "assets/estate/xiaopang"
        mapping = json.loads((root / "mapping.json").read_text(encoding="utf-8"))
        self.assertEqual({entry["id"] for entry in mapping["crops"]}, set(CROPS))
        self.assertEqual({entry["id"] for entry in mapping["fish"]}, set(FISH))
        self.assertEqual({entry["id"] for entry in mapping["collectibles"]}, set(FISHING_TREASURES))
        self.assertEqual({entry["id"] for entry in mapping["minerals"]}, set(MINERALS))

        def png_size(path):
            with path.open("rb") as image:
                self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")
                length = struct.unpack(">I", image.read(4))[0]
                self.assertEqual(image.read(4), b"IHDR")
                width, height = struct.unpack(">II", image.read(8))
                self.assertEqual(length, 13)
                return width, height

        for crop_id in CROPS:
            for stage in ("01_sprout", "02_seedling", "03_growing", "04_mature"):
                self.assertEqual(png_size(root / "crops" / crop_id / f"{stage}.png"), (16, 16))
        for fish_id in FISH:
            self.assertEqual(png_size(root / "fish" / f"{fish_id}.png"), (64, 48))
        for collectible_id in FISHING_TREASURES:
            self.assertEqual(png_size(root / "collectibles" / f"{collectible_id}.png"), (64, 48))
        for mineral_id in MINERALS:
            self.assertEqual(png_size(root / "minerals" / f"{mineral_id}.png"), (16, 16))
        for level in (1, 2, 3):
            self.assertEqual(png_size(root / "tools" / f"rod_{level}_icon.png"), (32, 32))
            self.assertEqual(png_size(root / "tools" / f"rod_{level}_held.png"), (16, 32))
        self.assertEqual(png_size(root / "player" / "character-sheet.png"), (889, 1769))

        attribution = self.read("assets/estate/ATTRIBUTION.md")
        self.assertIn("小胖庄园原创像素素材", attribution)
        assets = self.read("assets/js/estate/assets.js")
        self.assertIn('const ROOT = "assets/estate/xiaopang"', assets)
        for function in ("cropAsset", "catchAsset", "mineralAsset", "toolAsset", "inventoryAsset", "drawPlayerAsset"):
            self.assertIn(f"function {function}", assets)
        for direction in ('down:', 'up:', 'right:'):
            self.assertIn(direction, assets)

    def test_cc0_pixel_atlases_are_local_and_documented(self):
        attribution = self.read("assets/estate/ATTRIBUTION.md")
        sprites = self.read("assets/js/estate/sprites.js")
        for name in ("tiny-farm", "tiny-town"):
            self.assertTrue((ROOT / f"assets/estate/kenney/{name}.png").is_file())
            self.assertIn(f"kenney/{name}.png", attribution)
            self.assertIn(f"assets/estate/kenney/{name}.png", sprites)
        self.assertIn("CC0", attribution)
        self.assertTrue((ROOT / "assets/estate/kenney/License-tiny-farm.txt").is_file())
        self.assertTrue((ROOT / "assets/estate/kenney/License-tiny-town.txt").is_file())

    def test_desktop_and_mobile_controls_are_present(self):
        source = self.read("assets/js/estate/input.js")
        world = self.read("assets/js/estate/map.js")
        style = self.read("assets/css/estate.css")
        for key in ("ArrowRight", "ArrowLeft", "KeyW", "KeyA", "KeyE", "Space", "ShiftLeft"):
            self.assertIn(key, source)
        self.assertIn("sprinting", source)
        self.assertIn("input.sprinting", world)
        self.assertIn("190", world)
        for event in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
            self.assertIn(event, source)
        self.assertIn("touch-action: none", style)
        self.assertIn("(pointer: coarse)", style)

    def test_map_and_economy_actions_are_wired(self):
        world = self.read("assets/js/estate/map.js")
        art = self.read("assets/js/estate/art.js")
        ui = self.read("assets/js/estate/ui.js")
        self.assertIn("requestAnimationFrame", world)
        self.assertIn("collides", world)
        self.assertIn("drawPathSurface", world)
        self.assertNotIn('drawSprite(ctx, "town", 12', world)
        self.assertNotIn('drawSprite(ctx, "town", 24', world)
        self.assertIn('direction: "down"', world)
        self.assertIn("drawPlayerAsset", world)
        self.assertIn("drawPlotFrame", world)
        self.assertNotIn('stableSprite(x, [3, 4, 5, 8, 9, 10])', world)
        for direction in ('"up"', '"down"', '"left"', '"right"'):
            self.assertIn(direction, art)
        for action in ("estate_buy", "estate_plant", "estate_harvest", "estate_sell", "estate_sell_all"):
            self.assertIn(action, ui)
        for action in ("estate_buy_tool", "estate_start_fishing", "estate_start_mining"):
            self.assertIn(action, ui)
        self.assertIn("estate_finish_fishing", self.read("assets/js/estate/fishing.js"))
        fishing = self.read("assets/js/estate/fishing.js")
        # 分帧步长改由服务端下发的 fishing_rules 决定，不再写死 `trace.length / 10`
        self.assertIn("rules.steps_per_frame", fishing)
        self.assertNotIn("Math.floor(elapsed)", fishing)
        self.assertIn("fishing-catch-card", fishing)
        self.assertIn("继续钓鱼", fishing)
        self.assertIn("返回小胖钓场", fishing)
        self.assertIn("estate_mine_cell", self.read("assets/js/estate/mining.js"))
        mining = self.read("assets/js/estate/mining.js")
        self.assertIn('bomb: "💣"', mining)
        self.assertIn("mine-explosion", mining)

    def test_client_does_not_reimplement_server_rules(self):
        """玩法规则只能在服务端定义一处；客户端消费 catalog 下发的值。"""
        fishing = self.read("assets/js/estate/fishing.js")
        mining = self.read("assets/js/estate/mining.js")
        ui = self.read("assets/js/estate/ui.js")
        rules = self.read("assets/js/estate/rules.js")

        # 钓鱼物理系数只允许出现在 rules.js 里从规则对象读取，不得内联
        for literal in (".026", ".045", ".0035", "1.12", ".82", ".68"):
            self.assertNotIn(literal, fishing, f"fishing.js 内联了物理常量 {literal}")
        self.assertIn("tensionStep", fishing)
        self.assertIn("rodFactor", fishing)
        self.assertNotIn("{ 1: 1, 2: .82", fishing)

        # 维修费与预留格由 rules.js 计算，ui.js 不再自己套公式
        self.assertIn("repairCost", ui)
        self.assertIn("reservedSlots", ui)
        self.assertNotIn("repair_price *", ui)
        self.assertNotIn("strikes + 2", ui)

        # 矿壁格数来自本次矿局下发的边长，不再写死 25
        self.assertIn("run.size * run.size", mining)
        self.assertNotIn("index < 25", mining)

        # 规则层只按传入的规则推进，且不 import 任何 estate 模块
        self.assertNotIn("from \"./", rules)
        for field in ("hold_tension_gain", "release_tension_drop", "snapped_at"):
            self.assertIn(f"rules.{field}", rules)

    def test_ui_has_a_single_panel_dispatch(self):
        """面板分派只应存在一条链，避免 render 与 interact 各写一份。"""
        ui = self.read("assets/js/estate/ui.js")
        self.assertIn("const PANELS = {", ui)
        self.assertEqual(ui.count("renderShop()"), 2)  # 定义 + 分派表
        self.assertEqual(ui.count("renderMining()"), 2)
        self.assertNotIn('active?.kind === "shop"', ui)
        self.assertNotIn("disabled ||=", ui)

    def test_all_interactive_places_use_xiaopang_branding(self):
        combined = "\n".join([
            self.read("assets/js/estate/map.js"),
            self.read("assets/js/estate/ui.js"),
            self.read("assets/js/estate/fishing.js"),
        ])
        for name in ("小胖种子铺", "小胖谷仓", "小胖湖钓场", "小胖矿洞", "小胖农田"):
            self.assertIn(name, combined)
        for old_name in ("露露种子铺", "丰收谷仓", "星石矿洞", "月牙湖钓场", "谷仓库存"):
            self.assertNotIn(old_name, combined)

    def test_javascript_parses(self):
        for path in (ROOT / "assets/js/estate").glob("*.js"):
            result = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
