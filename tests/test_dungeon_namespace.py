#!/usr/bin/env python3
"""Keep old imports and patches working while new code uses dungeon.*."""

import ast
from importlib import import_module
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class DungeonNamespaceTests(unittest.TestCase):
    def test_legacy_imports_share_the_new_module_object(self):
        for old, new in (
            ("estate.dungeon.actions", "dungeon.legacy.actions"),
            ("estate.dungeon.catalog", "dungeon.legacy.catalog"),
            ("estate.dungeon.combat", "dungeon.legacy.combat"),
            ("estate.dungeon.effects", "dungeon.legacy.effects"),
            ("estate.dungeon.receipts", "dungeon.legacy.receipts"),
            ("estate.dungeon.runs", "dungeon.legacy.runs"),
            ("estate.dungeon.service", "dungeon.legacy.service"),
            ("estate.dungeon.schema", "dungeon.storage.legacy_schema"),
            ("server.estate.dungeon", "server.dungeon.legacy_protocol"),
        ):
            with self.subTest(old=old):
                self.assertIs(import_module(old), import_module(new))

        legacy_protocol = import_module("server.dungeon.legacy_protocol")
        with patch("server.estate.dungeon.progress_run") as replacement:
            self.assertIs(legacy_protocol.progress_run, replacement)

    def test_server_assembly_uses_new_namespace(self):
        app = import_module("server.app")
        schema = import_module("server.schema")
        self.assertIs(app.DungeonProtocol,
                      import_module("server.dungeon.legacy_protocol").DungeonProtocol)
        self.assertIs(schema.init_dungeon,
                      import_module("dungeon.storage.legacy_schema").init_dungeon)
        self.assertIs(schema.init_beta,
                      import_module("dungeon.storage.beta_schema").init_beta)


    def test_new_dungeon_namespace_does_not_import_estate_dungeon(self):
        sources = list((ROOT / "dungeon").rglob("*.py"))
        sources += list((ROOT / "server/dungeon").rglob("*.py"))
        sources += [ROOT / "server/app.py", ROOT / "server/schema.py",
                    ROOT / "scripts/simulate_dungeon.py"]
        for path in sources:
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                    if node.module == "estate":
                        names += ["estate." + alias.name for alias in node.names]
                else:
                    continue
                with self.subTest(path=str(path.relative_to(ROOT)), imports=names):
                    self.assertTrue(all(
                        name != "estate.dungeon" and not name.startswith("estate.dungeon.")
                        for name in names
                    ))


if __name__ == "__main__":
    unittest.main()
