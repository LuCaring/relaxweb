#!/usr/bin/env python3
"""小胖庄园纯规则层的跨语言等价性与行为测试。

用 Python 驱动 node 运行 `assets/js/estate/rules.js`，与服务端实现比对。
关键是钓鱼张力：客户端要为玩家画进度条，必须与服务端判定逐帧一致，
否则会出现「进度满了却判逃脱」。

证明链：Node 的 tensionStep ≡ Python 逐步参考实现 ≡ 服务端 simulate_fishing。
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate import catalog as rule_source
from estate.activities import simulate_fishing

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "assets" / "js" / "estate" / "rules.js"

HARNESS = r"""
import fs from "node:fs";
const rules = await import(process.env.ESTATE_RULES_URL);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};

if (input.fishing) {
  out.fishing = input.fishing.map((item) => {
    const cfg = item.catalog.fishing_rules;
    const factor = rules.rodFactor(item.catalog, item.rodLevel);
    let tension = cfg.tension_start;
    let progress = cfg.progress_start;
    const steps = [];
    for (let index = 0; index < item.trace.length; index += 1) {
      const frame = Math.floor(index / cfg.steps_per_frame);
      const force = item.pattern[Math.min(item.pattern.length - 1, frame)];
      const next = rules.tensionStep(cfg,
        { tension, progress, held: item.trace[index], force, factor });
      tension = next.tension;
      progress = next.progress;
      steps.push([tension, progress, next.outcome]);
      if (next.outcome) break;
    }
    return { factor, steps };
  });
}

if (input.repair) {
  out.repair = input.repair.map((item) => rules.repairCost(item.rule, item.durability));
}

if (input.duration) {
  out.duration = input.duration.map((value) => rules.formatDuration(value));
}

if (input.loot) {
  out.loot = input.loot.map((item) =>
    rules.lootSummary(item.loot, item.icons, item.empty, item.spaced === true));
}

if (input.entry) {
  out.entry = input.entry.map((item) => {
    const value = rules.catalogEntry(item.table, item.key);
    return value === undefined ? null : value;
  });
}

console.log(JSON.stringify(out));
"""


def run_rules(payload):
    env = dict(os.environ)
    env["ESTATE_RULES_URL"] = RULES_PATH.as_uri()
    # 不能让它按系统 locale 解码：中文 Windows 是 cp936，遇到 emoji 会解码失败
    # 并把 stdout 变成 None。规则输出含 emoji，必须显式按 UTF-8 解码。
    result = subprocess.run(
        ["node", "--input-type=module", "-e", HARNESS],
        input=json.dumps(payload).encode("utf-8"), capture_output=True,
        cwd=str(ROOT), env=env, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"node 失败: {result.stderr.decode('utf-8', 'replace').strip()[:800]}")
    if result.stdout is None:
        raise AssertionError("node 没有输出")
    return json.loads(result.stdout.decode("utf-8"))


# --------------------------------------------------------------------------
# 逐步参考实现：直接引用服务端常量，避免测试里再抄一份数字
# --------------------------------------------------------------------------


def reference_steps(trace, pattern, rod_level):
    rules = {
        "tension_start": rule_source.TENSION_START,
        "progress_start": rule_source.PROGRESS_START,
        "hold_tension_gain": rule_source.HOLD_TENSION_GAIN,
        "hold_tension_force_base": rule_source.HOLD_TENSION_FORCE_BASE,
        "hold_progress_gain": rule_source.HOLD_PROGRESS_GAIN,
        "hold_progress_base": rule_source.HOLD_PROGRESS_BASE,
        "hold_progress_force_scale": rule_source.HOLD_PROGRESS_FORCE_SCALE,
        "release_tension_drop": rule_source.RELEASE_TENSION_DROP,
        "release_progress_drop": rule_source.RELEASE_PROGRESS_DROP,
        "release_progress_force_base": rule_source.RELEASE_PROGRESS_FORCE_BASE,
        "snapped_at": rule_source.TENSION_SNAPPED_AT,
        "caught_at": rule_source.PROGRESS_CAUGHT_AT,
        "steps_per_frame": rule_source.FISHING_STEPS_PER_FRAME,
    }
    factor = rule_source.TOOLS["rod"][rod_level]["tension_factor"]
    tension, progress = rules["tension_start"], rules["progress_start"]
    peak = tension
    steps = []
    for index, held in enumerate(trace):
        frame = index // rules["steps_per_frame"]
        force = pattern[min(len(pattern) - 1, frame)]
        if held:
            tension += rules["hold_tension_gain"] * (rules["hold_tension_force_base"] + force) * factor
            progress += rules["hold_progress_gain"] * (rules["hold_progress_base"] - force * rules["hold_progress_force_scale"])
        else:
            tension -= rules["release_tension_drop"]
            progress -= rules["release_progress_drop"] * (rules["release_progress_force_base"] + force)
        tension = max(0, tension)
        progress = max(0, progress)
        peak = max(peak, tension)
        outcome = ("snapped" if tension >= rules["snapped_at"]
                   else "caught" if progress >= rules["caught_at"] else None)
        steps.append([tension, progress, outcome])
        if outcome:
            break
    return factor, steps, peak


def make_pattern(seed, length=rule_source.FISHING_STEPS):
    """确定性伪随机强度序列，范围与服务端采样一致。"""
    values = []
    state = seed
    for _ in range(length):
        state = (state * 1103515245 + 12345) % 2147483648
        values.append(round(rule_source.PATTERN_MIN
                            + (state / 2147483648) * rule_source.PATTERN_SPAN, 3))
    return values


def make_traces(length=120):
    return {
        "all_held": [True] * length,
        "all_released": [False] * length,
        "alternating": [index % 2 == 0 for index in range(length)],
        "bursty": [(index // 7) % 3 != 0 for index in range(length)],
        "late_hold": [index > length - 40 for index in range(length)],
    }


def client_catalog():
    """把服务端 catalog 转成客户端拿到的形状（JSON 往返后键变为字符串）。"""
    from estate.catalog import public_catalog
    return json.loads(json.dumps(public_catalog()))


class EstateRulesTests(unittest.TestCase):
    def test_node_can_import_rules_module(self):
        self.assertTrue(RULES_PATH.is_file())

    def test_tension_stepping_matches_server_step_by_step(self):
        catalog = client_catalog()
        cases, expected = [], []
        for rod_level in (1, 2, 3):
            for index, (name, trace) in enumerate(make_traces().items()):
                pattern = make_pattern(1000 + rod_level * 10 + index)
                cases.append({"catalog": catalog, "rodLevel": rod_level,
                              "trace": trace, "pattern": pattern})
                expected.append(reference_steps(trace, pattern, rod_level))
        result = run_rules({"fishing": cases})["fishing"]
        self.assertEqual(len(result), len(expected))
        for case_index, (got, want) in enumerate(zip(result, expected)):
            factor, want_steps, _peak = want
            self.assertAlmostEqual(got["factor"], factor, places=12,
                                   msg=f"case {case_index} 鱼竿系数不一致")
            self.assertEqual(len(got["steps"]), len(want_steps),
                             msg=f"case {case_index} 结算帧数不一致")
            for step_index, (got_step, want_step) in enumerate(zip(got["steps"], want_steps)):
                for field, (a, b) in enumerate(zip(got_step, want_step)):
                    if field == 2:
                        self.assertEqual(a, b, msg=f"case {case_index} 第 {step_index} 帧结局不一致")
                    else:
                        self.assertAlmostEqual(
                            a, b, places=12,
                            msg=f"case {case_index} 第 {step_index} 帧第 {field} 项不一致")

    def test_reference_mirror_agrees_with_server_simulation(self):
        """参考实现必须与服务端真实函数给出相同结局，否则上一条不成立。"""
        checked = 0
        for rod_level in (1, 2, 3):
            for index, (_name, trace) in enumerate(make_traces().items()):
                pattern = make_pattern(1000 + rod_level * 10 + index)
                _factor, steps, peak = reference_steps(trace, pattern, rod_level)
                served = simulate_fishing(trace, pattern, rod_level)
                self.assertEqual(steps[-1][2] or "escaped", served["outcome"],
                                 msg=f"rod {rod_level} {_name} 结局不一致")
                self.assertAlmostEqual(peak, served["peak_tension"], places=12,
                                       msg=f"rod {rod_level} {_name} 峰值张力不一致")
                checked += 1
        self.assertEqual(checked, 15)

    def test_rod_factor_reads_catalog_and_falls_back(self):
        catalog = client_catalog()
        cases = [{"table": None, "key": level} for level in (1, 2, 3)]
        result = run_rules({"fishing": [
            {"catalog": catalog, "rodLevel": level, "trace": [False] * 20,
             "pattern": [0.1]} for level in (1, 2, 3, 0, 99)
        ]})["fishing"]
        for level, got in zip((1, 2, 3, 0, 99), result):
            expected = rule_source.TOOLS["rod"].get(level, {}).get("tension_factor", 1)
            self.assertAlmostEqual(got["factor"], expected, places=12,
                                   msg=f"鱼竿等级 {level} 系数不一致")
        self.assertEqual(cases[0]["key"], 1)

    def test_repair_cost_matches_server_for_every_tool_and_durability(self):
        catalog = client_catalog()
        cases, expected = [], []
        for tool_type, levels in rule_source.TOOLS.items():
            for level, rule in levels.items():
                for durability in range(0, rule["max_durability"] + 1):
                    cases.append({"rule": catalog["tools"][tool_type][str(level)],
                                  "durability": durability})
                    missing = rule["max_durability"] - durability
                    expected.append(max(1.0, round(
                        rule["repair_price"] * missing / rule["max_durability"], 2)))
        result = run_rules({"repair": cases})["repair"]
        self.assertEqual(len(result), len(cases))
        for index, (got, want) in enumerate(zip(result, expected)):
            self.assertAlmostEqual(got, want, places=6,
                                   msg=f"第 {index} 组修理费不一致: {got} != {want}")

    def test_format_duration(self):
        values = [0, 1, 59, 60, 61, 3599, 3600, 3661, 7325, -5, 0.4]
        result = run_rules({"duration": values})["duration"]
        expected = []
        for seconds in values:
            value = max(0, int(seconds) if float(seconds).is_integer() else int(-(-seconds // 1)))
            hours, minutes, secs = value // 3600, value % 3600 // 60, value % 60
            expected.append(f"{hours}时 {minutes}分" if hours else f"{minutes}:{secs:02d}")
        self.assertEqual(result, expected)

    def test_loot_summary_keeps_both_layouts(self):
        icons = {"stone": "🪨", "iron": "⬢"}
        cases = [
            {"loot": {}, "icons": icons, "empty": "背包还是空的"},
            {"loot": {"stone": 2}, "icons": icons, "empty": "空"},
            {"loot": {"stone": 2, "iron": 1}, "icons": icons, "empty": "空"},
            {"loot": {"unknown": 3}, "icons": icons, "empty": "空"},
            {"loot": {"stone": 2, "iron": 1}, "icons": icons, "empty": "空", "spaced": True},
            {"loot": {}, "icons": icons, "empty": "没有挖到矿物", "spaced": True},
        ]
        result = run_rules({"loot": cases})["loot"]
        self.assertEqual(result, [
            "背包还是空的",
            "🪨×2",
            "🪨×2  ⬢×1",
            "◆×3",
            "🪨 × 2　⬢ × 1",
            "没有挖到矿物",
        ])

    def test_catalog_entry_handles_string_and_numeric_keys(self):
        table = {"1": {"name": "a"}, "2": {"name": "b"}}
        numeric = {1: {"name": "a"}, 2: {"name": "b"}}
        result = run_rules({"entry": [
            {"table": table, "key": 1}, {"table": table, "key": "2"},
            {"table": numeric, "key": 1}, {"table": numeric, "key": "1"},
            {"table": table, "key": 99}, {"table": None, "key": 1},
        ]})["entry"]
        self.assertEqual(result, [{"name": "a"}, {"name": "b"},
                                  {"name": "a"}, {"name": "a"}, None, None])

    def test_rules_module_holds_no_gameplay_literals(self):
        """规则必须来自服务端下发，模块里不能另存一份物理常量。"""
        source = RULES_PATH.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith(("*", "//", "/*"))
        )
        for literal in (".18", ".026", ".045", ".0035", ".68", "1.12",
                        ".013", ".08", ".78", ".82"):
            self.assertNotIn(literal, code, f"rules.js 仍含玩法字面量 {literal}")
        # 系数必须逐个从传入的规则对象读取，而不是模块自带
        for field in ("hold_tension_gain", "hold_tension_force_base",
                      "hold_progress_gain", "hold_progress_base",
                      "hold_progress_force_scale", "release_tension_drop",
                      "release_progress_drop", "release_progress_force_base",
                      "snapped_at", "caught_at"):
            self.assertIn(f"rules.{field}", code, f"rules.js 未从规则对象读取 {field}")
        self.assertIn("catalog.mining_rules.extra_cells", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
