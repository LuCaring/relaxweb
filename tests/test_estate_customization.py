"""Deployment-defined collection rewards preserve server authority and saved ownership."""
from copy import deepcopy
import json
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULTS
from estate import EstateError, estate_state, init_estate, set_skin, buy_skin
from estate.catalog import SKINS, FISHING_TREASURES, collectible_item, public_catalog
from estate.customization import COLLECTION_REWARD_ID, collection_reward
from estate.store import change_inventory


class CustomRewardTests(unittest.TestCase):
    def setUp(self):
        self.settings = deepcopy(DEFAULTS["estate"]["collection_reward"])
        self.normal = {key: value for key, value in SKINS.items() if value["unlock"] != "collection"}
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY, coins REAL)")
        self.conn.execute("INSERT INTO users VALUES ('alice', 10000)")
        init_estate(self.conn)

    def configure(self, **settings):
        self.settings.update(settings)
        catalog = dict(self.normal)
        reward = collection_reward(self.settings, catalog, FISHING_TREASURES)
        if reward is not None:
            catalog[COLLECTION_REWARD_ID] = reward
        self.enterContext(patch.dict(SKINS, catalog, clear=True))

    def state(self):
        return estate_state(self.conn, "alice", 2_000_000_000)

    def test_custom_name_asset_and_requirements_drive_unlock_and_persist(self):
        self.configure(name="星空守望者", description="属于这个站点的奖赏。", asset_id="steve",
                       required_skins=["steve"], required_collectibles=["antique_watch"])
        state = self.state()
        reward = state["catalog"]["skins"][COLLECTION_REWARD_ID]
        self.assertEqual((reward["name"], reward["asset_id"]), ("星空守望者", "steve"))
        self.assertEqual(state["skins"]["missing_skins"], ["steve"])
        self.assertEqual(state["skins"]["missing_collectibles"], ["antique_watch"])
        self.conn.execute("INSERT INTO estate_owned_skins VALUES ('alice','steve')")
        self.assertNotIn(COLLECTION_REWARD_ID, self.state()["skins"]["owned"])
        change_inventory(self.conn, "alice", collectible_item("antique_watch"), 1)
        self.assertIn(COLLECTION_REWARD_ID, self.state()["skins"]["owned"])
        set_skin(self.conn, "alice", "custom-reward-equip", COLLECTION_REWARD_ID, 2_000_000_000)
        change_inventory(self.conn, "alice", collectible_item("antique_watch"), -1)
        self.assertEqual(self.state()["profile"]["skin_id"], COLLECTION_REWARD_ID)
        self.assertEqual(self.state()["coins"], 10000)

    def test_empty_requirements_unlock_immediately_but_never_sell(self):
        self.configure(required_skins=[], required_collectibles=[])
        self.assertIn(COLLECTION_REWARD_ID, self.state()["skins"]["owned"])
        with self.assertRaises(EstateError) as caught:
            buy_skin(self.conn, "alice", "reward-not-for-sale", COLLECTION_REWARD_ID,
                     2_000_000_000, lambda *args: self.fail("reward must never debit"))
        self.assertEqual(caught.exception.code, "skin_not_for_sale")

    def test_disabled_reward_retains_ownership_and_restores_when_enabled(self):
        self.configure(required_skins=[], required_collectibles=[])
        self.state()
        set_skin(self.conn, "alice", "reward-before-disable", COLLECTION_REWARD_ID, 2_000_000_000)
        with patch.dict(SKINS, self.normal, clear=True):
            state = self.state()
            self.assertNotIn(COLLECTION_REWARD_ID, state["catalog"]["skins"])
            self.assertEqual(state["profile"]["skin_id"], "berry")
            self.assertTrue(self.conn.execute("SELECT 1 FROM estate_owned_skins WHERE skin_id=?",
                                             (COLLECTION_REWARD_ID,)).fetchone())
        self.assertIn(COLLECTION_REWARD_ID, self.state()["skins"]["owned"])

    def test_catalog_lists_are_copied_and_owned_rewards_survive_requirement_changes(self):
        self.configure(required_skins=[], required_collectibles=[])
        self.state()
        SKINS[COLLECTION_REWARD_ID]["required_skins"] = ["steve"]
        catalog = public_catalog()
        catalog["skins"][COLLECTION_REWARD_ID]["required_skins"].clear()
        self.assertEqual(SKINS[COLLECTION_REWARD_ID]["required_skins"], ["steve"])
        self.assertIn(COLLECTION_REWARD_ID, self.state()["skins"]["owned"])

    def test_invalid_customization_is_rejected(self):
        for key, value in (("enabled", "false"), ("name", ""), ("description", None),
                           ("asset_id", "../external"), ("asset_id", "https://example.test/a"),
                           ("required_skins", [COLLECTION_REWARD_ID]),
                           ("required_skins", ["steve", "steve"]),
                           ("required_collectibles", ["unknown"]),
                           ("required_collectibles", [{}])):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                collection_reward({**self.settings, key: value}, self.normal, FISHING_TREASURES)
        self.assertIsNone(collection_reward({"enabled": False}, self.normal, FISHING_TREASURES))

    def test_actual_config_file_controls_catalog_and_does_not_expose_private_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "settings.json"
            config.write_text(json.dumps({"estate": {"collection_reward": {
                "name": "本站奖赏", "asset_id": "dva", "required_skins": []}},
                "stream": {"publish_password": "test-private-setting"}}))
            result = subprocess.check_output([sys.executable, "-c",
                "import json; from estate.catalog import public_catalog; print(json.dumps(public_catalog()))"],
                cwd=ROOT, env={**os.environ, "LIVE_CONFIG_FILE": str(config)}, text=True)
            reward = json.loads(result)["skins"][COLLECTION_REWARD_ID]
            self.assertEqual((reward["name"], reward["asset_id"], reward["required_skins"]),
                             ("本站奖赏", "dva", []))
            self.assertEqual(set(reward["required_collectibles"]), set(FISHING_TREASURES))
            self.assertNotIn("test-private-setting", result)


if __name__ == "__main__":
    unittest.main()
