import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dungeon.content import ContentError, load_ruleset
from dungeon.domain.growth import FixedLevelPolicy
from dungeon.plugins import PluginError, default_registry
from dungeon.plugins.registry import (
    CONTEXT_SCHEMA, EVENT_SCHEMA, OPERATION_SCHEMA, PARAM_SCHEMA, STATE_SCHEMA,
    EffectResult, Mechanism, Registry,
)
from dungeon.tools.__main__ import main as cli_main


RELEASE = ROOT / "content/dungeon/release.json"
P0_RELEASE = ROOT / "content/dungeon/release-p0.json"
FIXTURES = ROOT / "contracts/dungeon/fixtures"


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "dungeon"
        shutil.copytree(RELEASE.parent, self.root)
        self.release = self.root / "release.json"
        self.pack = self.root / "packs/beta-core"

    def change(self, relative, update):
        path = self.root / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        update(value)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def rejects(self, relative, update, fragment):
        self.change(relative, update)
        with self.assertRaisesRegex(ContentError, fragment):
            load_ruleset(self.release)

    def test_load_cli_hash_and_public_projection(self):
        rules = load_ruleset(RELEASE)
        again = load_ruleset(RELEASE)
        self.assertEqual(rules.ruleset_hash, again.ruleset_hash)
        self.assertEqual(rules.ruleset_hash,
                         "sha256-7b80484d5348e997d05f8afd96696e45c56089ff294e99f1ea0ef68cc1610b58")
        self.assertRegex(rules.ruleset_hash, r"^sha256-[a-f0-9]{64}$")
        self.assertEqual(cli_main(["validate-content", str(RELEASE)]), 0)
        public = json.dumps(rules.public_catalog(), ensure_ascii=False)
        self.assertNotIn("beta.loot.boss", public)
        self.assertNotIn("loot_pool_id", public)
        self.assertNotIn("minimum_spent", public)
        self.assertIn("遗迹长剑", public)
        with self.assertRaises(TypeError):
            rules.content("weapons")[0]["name"] = "tampered"
        detached = rules.mutable_content("weapons")
        detached[0]["name"] = "tampered"
        self.assertEqual(rules.content("weapons")[0]["name"], "遗迹长剑")

    def test_real_pack_quotes_growth_with_atk_stat(self):
        rules = load_ruleset(RELEASE)
        fixture = json.loads((FIXTURES / "upgrade_quote.json").read_text(encoding="utf-8"))
        policy = FixedLevelPolicy(rules.ruleset_id, rules.mutable_content("progression"))
        quote = policy.quote(fixture["item"], set(fixture["progress"]), fixture["target_level"])
        self.assertEqual(quote["costs"], fixture["expected_costs"])
        self.assertEqual(quote["stats"], fixture["expected_stats"])

    def test_second_incremental_pack_loads_and_changes_hash(self):
        before = load_ruleset(self.release).ruleset_hash
        extra = self.root / "packs/addon"
        extra.mkdir()
        (extra / "manifest.json").write_text(json.dumps({
            "pack_id": "beta.addon", "pack_version": "0.1.0", "schema_version": 1,
            "requires": [], "files": {"weapons": "weapons.json"}
        }), encoding="utf-8")
        weapon = copy.deepcopy(json.loads((self.pack / "weapons.json").read_text(encoding="utf-8"))[0])
        weapon["id"] = "beta.sword.addon"
        weapon["effects"] = []
        (extra / "weapons.json").write_text(json.dumps([weapon]), encoding="utf-8")
        self.change("release.json", lambda value: value["packs"].append("packs/addon/manifest.json"))
        rules = load_ruleset(self.release)
        self.assertEqual(len(rules.content("weapons")), 2)
        self.assertNotEqual(rules.ruleset_hash, before)

    def test_rejects_versions_schema_and_bad_money(self):
        self.rejects("release.json", lambda value: value.update(protocol_version=2), "1 was expected")

    def test_rejects_float_version(self):
        self.rejects("release.json", lambda value: value.update(protocol_version=1.0), "integer")

    def test_rejects_incompatible_plugin_version(self):
        self.rejects("packs/beta-core/manifest.json",
                     lambda value: value["requires"][0].update(plugin_version="0.2.0"),
                     "unavailable plugin")

    def test_rejects_duplicate_plugin_requirement(self):
        self.rejects("packs/beta-core/manifest.json",
                     lambda value: value["requires"].append(copy.deepcopy(value["requires"][0])),
                     "duplicate plugin requirement")

    def test_rejects_unknown_field(self):
        self.rejects("packs/beta-core/weapons.json", lambda value: value[0].update(script="exec"),
                     "Additional properties")

    def test_rejects_float_and_boolean_money(self):
        for amount in (200.0, True, -1):
            with self.subTest(amount=amount):
                path = self.pack / "progression.json"
                original = path.read_text(encoding="utf-8")
                self.rejects("packs/beta-core/progression.json",
                             lambda value: value[0]["rows"][0]["costs"][0].update(amount=amount),
                             "amount|integer|minimum")
                path.write_text(original, encoding="utf-8")

    def test_rejects_missing_reference_duplicate_id_empty_pool(self):
        self.rejects("packs/beta-core/encounters.json",
                     lambda value: value[0]["enemies"].append("beta.enemy.missing"), "unknown enemy")

    def test_rejects_duplicate_content_id(self):
        self.rejects("packs/beta-core/weapons.json", lambda value: value.append(copy.deepcopy(value[0])),
                     "duplicate content ID")

    def test_rejects_empty_loot_pool(self):
        self.rejects("packs/beta-core/loot.json", lambda value: value[0].update(entries=[]),
                     "non-empty|should be non-empty")

    def test_rejects_plugin_not_declared_and_unknown_kind(self):
        self.rejects("packs/beta-core/manifest.json", lambda value: value.update(requires=[]),
                     "provider not declared")

    def test_rejects_path_escape_symlink(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("[]", encoding="utf-8")
        target = self.pack / "weapons.json"
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaisesRegex(ContentError, "path escapes"):
            load_ruleset(self.release)

    def test_rejects_literal_path_traversal(self):
        self.rejects("packs/beta-core/manifest.json",
                     lambda value: value["files"].update(weapons="../other.json"),
                     "does not match")

    def test_rejects_duplicate_json_keys(self):
        self.release.write_text('{"ruleset_id":"one","ruleset_id":"two"}', encoding="utf-8")
        with self.assertRaisesRegex(ContentError, "duplicate JSON key"):
            load_ruleset(self.release)


class P0ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "dungeon"
        shutil.copytree(P0_RELEASE.parent, self.root)
        self.release = self.root / "release-p0.json"
        self.pack = self.root / "packs/beta-p0-flamefield"

    def change(self, filename, update):
        path = self.pack / filename
        value = json.loads(path.read_text(encoding="utf-8"))
        update(value)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def rejects(self, filename, update, fragment):
        self.change(filename, update)
        with self.assertRaisesRegex(ContentError, fragment):
            load_ruleset(self.release)

    def test_route_spawns_rewards_and_public_catalog(self):
        rules = load_ruleset(P0_RELEASE)
        route = rules.content("routes")[0]
        self.assertEqual(route["encounter_ids"], tuple(
            "beta.p0.encounter.rm%02d" % number for number in range(1, 5)))
        rooms = {room["id"]: room for room in rules.content("encounters")}
        self.assertEqual([sum(group["count"] for group in rooms[room_id]["spawn_groups"])
                          for room_id in route["encounter_ids"]], [18, 18, 13, 1])
        rewards = [rooms[room_id]["rewards"] for room_id in route["encounter_ids"]]
        self.assertEqual([reward["permanent"]["coins_minor"] for reward in rewards],
                         [1500, 2000, 3000, 5000])
        self.assertEqual([reward["permanent"]["loot_rolls"] for reward in rewards],
                         [1, 0, 1, 1])
        self.assertEqual(rewards[2]["run"]["potions"][0]["heal_max_hp_bp"], 3000)
        public = json.dumps(rules.public_catalog(), ensure_ascii=False)
        self.assertIn("beta.p0.route.flamefield", public)
        self.assertIn("entry_encounter_id", public)
        for secret in ("loot_pool_id", "loot_rolls", "coins_minor", "progress_ids", "spawn_groups"):
            self.assertNotIn(secret, public)
        self.assertEqual(load_ruleset(P0_RELEASE).ruleset_hash, rules.ruleset_hash)
        self.assertNotEqual(rules.ruleset_hash, load_ruleset(RELEASE).ruleset_hash)

    def test_rejects_route_unknown_duplicate_and_wrong_boss_position(self):
        self.rejects("routes.json",
                     lambda routes: routes[0]["encounter_ids"].append("beta.p0.encounter.missing"),
                     "unknown route encounter")

    def test_rejects_duplicate_route_room(self):
        self.rejects("routes.json",
                     lambda routes: routes[0]["encounter_ids"].append("beta.p0.encounter.rm01"),
                     "unique|duplicate")

    def test_rejects_boss_before_final_room(self):
        self.rejects("routes.json", lambda routes: routes[0]["encounter_ids"].reverse(),
                     "boss before final room")

    def test_rejects_route_without_final_boss(self):
        self.rejects("routes.json", lambda routes: routes[0]["encounter_ids"].pop(),
                     "route has no final boss")

    def test_rejects_route_room_without_reward_plan(self):
        self.rejects("encounters.json", lambda rooms: rooms[0].pop("rewards"),
                     "route encounter missing reward plan")

    def test_rejects_route_referencing_legacy_room_without_reward_plan(self):
        self.rejects("routes.json",
                     lambda routes: routes[0]["encounter_ids"].__setitem__(
                         0, "beta.encounter.entry"),
                     "route encounter missing reward plan: beta.encounter.entry")

    def test_rejects_spawn_reference_and_expansion(self):
        self.rejects("encounters.json",
                     lambda rooms: rooms[0]["spawn_groups"][0].update(enemy_id="beta.p0.enemy.missing"),
                     "spawn groups do not match enemies")

    def test_rejects_spawn_count_limit(self):
        self.rejects("encounters.json",
                     lambda rooms: rooms[0]["spawn_groups"][0].update(count=65),
                     "64")

    def test_rejects_duplicate_spawn_group(self):
        self.rejects("encounters.json",
                     lambda rooms: rooms[0]["spawn_groups"].append(
                         copy.deepcopy(rooms[0]["spawn_groups"][0])),
                     "duplicate spawn group enemy")

    def test_rejects_reward_progress_forgery(self):
        self.rejects("encounters.json",
                     lambda rooms: rooms[0]["rewards"]["permanent"].update(
                         progress_ids=["beta.p0.clear.rm04"]),
                     "reward progress does not match clear progress")

    def test_rejects_reward_duplicate_and_bounded_rolls(self):
        self.rejects("encounters.json",
                     lambda rooms: rooms[0]["rewards"]["permanent"].update(
                         progress_ids=["beta.p0.clear.rm01", "beta.p0.clear.rm01"]),
                     "unique")

    def test_rejects_fractional_money(self):
        self.rejects("encounters.json",
                     lambda rooms: rooms[0]["rewards"]["permanent"].update(coins_minor=15.5),
                     "integer")

    def test_rejects_reward_quantity_limits(self):
        for field, value, fragment in (("loot_rolls", 11, "10"),
                                       ("coins_minor", 1000000001, "1000000000")):
            with self.subTest(field=field):
                path = self.pack / "encounters.json"
                original = path.read_text(encoding="utf-8")
                self.rejects("encounters.json",
                             lambda rooms: rooms[0]["rewards"]["permanent"].update(**{field: value}),
                             fragment)
                path.write_text(original, encoding="utf-8")

    def test_rejects_route_and_room_quantity_limits(self):
        self.rejects("routes.json",
                     lambda routes: routes[0]["encounter_ids"].extend(
                         "beta.p0.encounter.extra%02d" % index for index in range(33)),
                     "too long|32")

    def test_rejects_more_than_64_enemies(self):
        self.rejects("encounters.json",
                     lambda rooms: rooms[0]["enemies"].extend(
                         ["beta.p0.enemy.softbug"] * 47),
                     "too long|64")


class RegistryTests(unittest.TestCase):
    def test_fixture_invocation_cooldown_and_derived_events(self):
        fixture = json.loads((FIXTURES / "shield_on_spend.json").read_text(encoding="utf-8"))
        params = load_ruleset(RELEASE).content("effects")[0]["params"]
        registry = default_registry()
        result = registry.invoke(fixture["kind"], fixture["event"], fixture["context"], params,
                                 fixture["effect_state"])
        self.assertEqual(dict(result.next_effect_state), fixture["expected"]["next_effect_state"])
        self.assertEqual([dict(op) for op in result.operations], fixture["expected"]["operations"])
        no_proc = registry.invoke(fixture["kind"], fixture["event"], fixture["context"], params,
                                  result.next_effect_state)
        self.assertEqual(no_proc.operations, ())
        derived = dict(fixture["event"], proc_depth=1)
        self.assertEqual(registry.invoke(fixture["kind"], derived, fixture["context"], params,
                                         fixture["effect_state"]).operations, ())

    def test_one_proc_per_action_even_without_cooldown(self):
        fixture = json.loads((FIXTURES / "shield_on_spend.json").read_text(encoding="utf-8"))
        params = load_ruleset(RELEASE).mutable_content("effects")[0]["params"]
        params["cooldown_ms"] = 0
        registry = default_registry()
        first = registry.invoke(fixture["kind"], fixture["event"], fixture["context"],
                                params, fixture["effect_state"])
        self.assertEqual(len(first.operations), 1)
        repeated = registry.invoke(fixture["kind"], fixture["event"], fixture["context"],
                                   params, first.next_effect_state)
        self.assertEqual(repeated.operations, ())
        next_action = dict(fixture["event"], action_id="swing_2")
        self.assertEqual(len(registry.invoke(fixture["kind"], next_action, fixture["context"],
                                             params, first.next_effect_state).operations), 1)

    def test_strict_integer(self):
        fixture = json.loads((FIXTURES / "shield_on_spend.json").read_text(encoding="utf-8"))
        params = load_ruleset(RELEASE).content("effects")[0]["params"]
        for bad in (3.0, True):
            with self.subTest(value=bad), self.assertRaises(PluginError):
                default_registry().invoke(fixture["kind"], dict(fixture["event"], actual_spent=bad),
                                          fixture["context"], params, fixture["effect_state"])

    def test_rejects_budget_unknown_operation_and_wrong_actor(self):
        fixture = json.loads((FIXTURES / "shield_on_spend.json").read_text(encoding="utf-8"))
        params = load_ruleset(RELEASE).mutable_content("effects")[0]["params"]
        valid = fixture["expected"]["operations"][0]
        bad_results = (
            ("budget", (valid, valid)),
            ("operation", (dict(valid, operation="wallet.credit"),)),
            ("different actor", (dict(valid, actor_id="other"),)),
        )
        for label, operations in bad_results:
            with self.subTest(label=label):
                registry = Registry()
                registry.register("builtin.test", "0.1.0", Mechanism(
                    "kind.test", "sword.momentum_spent",
                    lambda event, context, params, state, ops=operations: EffectResult(dict(state), ops),
                    PARAM_SCHEMA, STATE_SCHEMA, EVENT_SCHEMA, CONTEXT_SCHEMA, OPERATION_SCHEMA, 1))
                with self.assertRaises(PluginError):
                    registry.invoke("kind.test", fixture["event"], fixture["context"],
                                    params, fixture["effect_state"])

    def test_registry_schema_snapshot_and_multiple_mechanisms(self):
        schema = copy.deepcopy(PARAM_SCHEMA)
        registry = Registry()
        mechanism = Mechanism("kind.one", "sword.momentum_spent",
                              lambda event, context, params, state: EffectResult(dict(state), ()),
                              schema, STATE_SCHEMA, EVENT_SCHEMA, CONTEXT_SCHEMA, OPERATION_SCHEMA, 1)
        registry.register("builtin.test", "0.1.0", mechanism)
        registry.register("builtin.test", "0.1.0", Mechanism(
            "kind.two", "sword.momentum_spent", mechanism.handler,
            PARAM_SCHEMA, STATE_SCHEMA, EVENT_SCHEMA, CONTEXT_SCHEMA, OPERATION_SCHEMA, 1))
        schema["properties"]["minimum_spent"]["minimum"] = -100
        self.assertEqual(registry.mechanism("kind.one").params_schema["properties"]["minimum_spent"]["minimum"], 1)
        with self.assertRaises(PluginError):
            registry.register("builtin.test", "0.2.0", Mechanism(
                "kind.three", "sword.momentum_spent", mechanism.handler,
                PARAM_SCHEMA, STATE_SCHEMA, EVENT_SCHEMA, CONTEXT_SCHEMA, OPERATION_SCHEMA, 1))


if __name__ == "__main__":
    unittest.main()
