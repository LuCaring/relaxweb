from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from dungeon.contracts.json_validation import validation_errors
from dungeon.plugins import PluginError, Registry, default_registry


class ContentError(ValueError):
    pass


SCHEMA_DIR = Path(__file__).resolve().parents[2] / "contracts" / "dungeon" / "schemas"
KINDS = ("weapons", "enemies", "encounters", "routes", "effects", "progression", "economy", "loot")


def _pairs_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContentError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        if path.stat().st_size > 1_000_000:
            raise ContentError(f"file too large: {path}")
        with path.open("r", encoding="utf-8") as file:
            return json.load(file, object_pairs_hook=_pairs_unique,
                             parse_constant=lambda value: (_ for _ in ()).throw(ContentError(f"non-finite number: {value}")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentError(f"could not read JSON {path}: {exc}") from exc


def _validate(schema: Mapping[str, Any], value: Any, label: str) -> None:
    errors = validation_errors(schema, value)
    if errors:
        raise ContentError(f"{label}: {errors[0]}")


def _inside(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ContentError(f"path escapes content directory: {relative}")
    return path


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class Ruleset:
    ruleset_id: str
    ruleset_hash: str
    protocol_version: int
    plugin_api_version: int
    simulation_version: int
    save_version: int
    tick_rate: int
    _content: Mapping[str, Any]
    _plugin_versions: Mapping[str, str]

    def content(self, name: str) -> Any:
        """Return frozen internal content. Never forward this wholesale to clients."""
        if name not in KINDS:
            raise KeyError(name)
        return self._content[name]

    def mutable_content(self, name: str) -> Any:
        """Return a detached JSON copy for policy constructors and adapters."""
        return _thaw(self.content(name))

    def reference(self) -> dict[str, Any]:
        return {
            "ruleset_id": self.ruleset_id, "ruleset_hash": self.ruleset_hash,
            "protocol_version": self.protocol_version,
            "plugin_api_version": self.plugin_api_version,
            "simulation_version": self.simulation_version,
            "save_version": self.save_version, "tick_rate": self.tick_rate,
        }

    def public_catalog(self) -> dict[str, Any]:
        """Explicitly whitelist fields; hidden loot and effect params stay server-side."""
        return {
            "ruleset": dict(self.reference()),
            "weapons": [
                {"id": weapon["id"], "name": weapon["name"], "slot": weapon["slot"],
                 "visual_id": weapon["visual_id"], "stats": dict(weapon["stats"]),
                 "trade": dict(weapon["trade"])}
                for weapon in self.content("weapons")
            ],
            "encounters": [
                {"id": encounter["id"], "name": encounter["name"]}
                for encounter in self.content("encounters")
            ],
            "routes": [
                {"id": route["id"], "name": route["name"],
                 "entry_encounter_id": route["encounter_ids"][0],
                 "room_count": len(route["encounter_ids"])}
                for route in self.content("routes")
            ],
            "trade_policy": dict(self.content("economy")["trade_policy"]),
        }


def load_ruleset(release_path: str | Path, registry: Registry | None = None) -> Ruleset:
    """Validate a release and all referenced packs, then pin an immutable snapshot.

    Paths are resolved under the release directory and each pack directory. A
    symlink to outside those directories is rejected before opening any JSON.
    """
    registry = registry or default_registry()
    release_file = Path(release_path).resolve()
    release = _read_json(release_file)
    _validate(_read_json(SCHEMA_DIR / "release.schema.json"), release, "release")
    manifest_schema = _read_json(SCHEMA_DIR / "manifest.schema.json")
    content_schema = _read_json(SCHEMA_DIR / "content.schema.json")
    contents: dict[str, Any] = {kind: [] for kind in KINDS if kind != "economy"}
    seen_ids: set[str] = set()
    seen_packs: set[str] = set()
    manifests: list[dict[str, Any]] = []
    required_plugins: dict[str, str] = {}
    economy: dict[str, Any] | None = None
    for manifest_relative in release["packs"]:
        manifest_file = _inside(release_file.parent, manifest_relative)
        manifest = _read_json(manifest_file)
        _validate(manifest_schema, manifest, manifest_relative)
        if manifest["pack_id"] in seen_packs:
            raise ContentError(f"duplicate pack ID: {manifest['pack_id']}")
        seen_packs.add(manifest["pack_id"])
        manifests.append(manifest)
        pack_dir = manifest_file.parent
        if len(set(manifest["files"].values())) != len(manifest["files"]):
            raise ContentError("duplicate file path in pack manifest")
        seen_requirements: set[str] = set()
        for requirement in manifest["requires"]:
            plugin_id, version = requirement["plugin_id"], requirement["plugin_version"]
            if plugin_id in seen_requirements:
                raise ContentError(f"duplicate plugin requirement: {plugin_id}")
            seen_requirements.add(plugin_id)
            if plugin_id in required_plugins and required_plugins[plugin_id] != version:
                raise ContentError(f"conflicting plugin versions: {plugin_id}")
            try:
                registry.require(plugin_id, version)
            except PluginError as exc:
                raise ContentError(str(exc)) from exc
            required_plugins[plugin_id] = version
        for kind in manifest["files"]:
            path = _inside(pack_dir, manifest["files"][kind])
            value = _read_json(path)
            _validate({"$schema": content_schema["$schema"], "$defs": content_schema["$defs"],
                       "$ref": f"#/$defs/{kind}"}, value, str(path))
            if kind == "effects":
                declared = {item["plugin_id"]: item["plugin_version"] for item in manifest["requires"]}
                for effect in value:
                    try:
                        provider_id, provider_version = registry.provider(effect["kind"])
                    except PluginError as exc:
                        raise ContentError(str(exc)) from exc
                    if declared.get(provider_id) != provider_version:
                        raise ContentError(f"effect provider not declared by pack: {provider_id}@{provider_version}")
            if kind == "economy":
                if economy is not None:
                    raise ContentError("more than one economy policy")
                economy = value
                continue
            for entry in value:
                if entry["id"] in seen_ids:
                    raise ContentError(f"duplicate content ID: {entry['id']}")
                seen_ids.add(entry["id"])
            contents[kind].extend(value)
    if economy is None:
        raise ContentError("missing economy policy")
    for kind in ("weapons", "enemies", "encounters", "progression", "loot"):
        if not contents[kind]:
            raise ContentError(f"missing required content: {kind}")
    contents["economy"] = economy
    _check_semantics(contents, registry, required_plugins)
    # An older release has no routes file. Keep its canonical payload (and
    # pinned hash) byte-for-byte compatible with the original loader.
    canonical_contents = {kind: value for kind, value in contents.items()
                          if kind != "routes" or value}
    canonical = json.dumps({"release": release, "manifests": manifests,
                            "contents": canonical_contents, "plugins": required_plugins},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                           allow_nan=False).encode("utf-8")
    digest = "sha256-" + hashlib.sha256(canonical).hexdigest()
    frozen = _freeze(copy.deepcopy(contents))
    return Ruleset(release["ruleset_id"], digest, release["protocol_version"],
                   release["plugin_api_version"], release["simulation_version"],
                   release["save_version"], release["tick_rate"], frozen,
                   _freeze(required_plugins))


def _check_semantics(content: Mapping[str, Any], registry: Registry,
                     required_plugins: Mapping[str, str]) -> None:
    weapons = {entry["id"] for entry in content["weapons"]}
    enemies = {entry["id"]: entry for entry in content["enemies"]}
    encounters = {entry["id"]: entry for entry in content["encounters"]}
    pools = {entry["id"] for entry in content["loot"]}
    effects = {entry["id"] for entry in content["effects"]}
    progress: set[str] = set()
    for weapon in content["weapons"]:
        for effect_id in weapon["effects"]:
            if effect_id not in effects:
                raise ContentError(f"unknown effect reference: {effect_id}")
        if weapon["trade"]["allowed"] and weapon["trade"]["reason"] is not None:
            raise ContentError(f"trade reason on tradable item: {weapon['id']}")
        if not weapon["trade"]["allowed"] and not weapon["trade"]["reason"]:
            raise ContentError(f"missing trade reason: {weapon['id']}")
    for encounter in content["encounters"]:
        if len(encounter["enemies"]) > 64:
            raise ContentError(f"too many encounter enemies: {encounter['id']}")
        for enemy_id in encounter["enemies"]:
            if enemy_id not in enemies:
                raise ContentError(f"unknown enemy reference: {enemy_id}")
        if "spawn_groups" in encounter:
            groups = encounter["spawn_groups"]
            group_ids = [group["enemy_id"] for group in groups]
            if len(group_ids) != len(set(group_ids)):
                raise ContentError(f"duplicate spawn group enemy: {encounter['id']}")
            expanded = [group["enemy_id"] for group in groups for _ in range(group["count"])]
            if len(expanded) > 64 or expanded != encounter["enemies"]:
                raise ContentError(f"spawn groups do not match enemies: {encounter['id']}")
        if encounter["loot_pool_id"] not in pools:
            raise ContentError(f"unknown loot pool: {encounter['loot_pool_id']}")
        clear_progress = encounter.get("clear_progress_id")
        if clear_progress:
            if clear_progress in progress:
                raise ContentError(f"duplicate clear progress: {clear_progress}")
            progress.add(clear_progress)
        rewards = encounter.get("rewards")
        if rewards:
            reward_progress = rewards["permanent"]["progress_ids"]
            if reward_progress != ([clear_progress] if clear_progress else []):
                raise ContentError(f"reward progress does not match clear progress: {encounter['id']}")
    for route in content["routes"]:
        route_encounters = route["encounter_ids"]
        if len(route_encounters) != len(set(route_encounters)):
            raise ContentError(f"duplicate route encounter: {route['id']}")
        for encounter_id in route_encounters:
            if encounter_id not in encounters:
                raise ContentError(f"unknown route encounter: {encounter_id}")
            if "rewards" not in encounters[encounter_id]:
                raise ContentError(f"route encounter missing reward plan: {encounter_id}")
        for encounter_id in route_encounters[:-1]:
            if any(enemies[enemy_id]["boss"] for enemy_id in encounters[encounter_id]["enemies"]):
                raise ContentError(f"boss before final room: {route['id']}")
        final = encounters[route_encounters[-1]]
        if not any(enemies[enemy_id]["boss"] for enemy_id in final["enemies"]):
            raise ContentError(f"route has no final boss: {route['id']}")
    for pool in content["loot"]:
        for entry in pool["entries"]:
            if entry["item_template_id"] not in weapons:
                raise ContentError(f"unknown loot item: {entry['item_template_id']}")
    for effect in content["effects"]:
        try:
            mechanism = registry.mechanism(effect["kind"])
        except PluginError as exc:
            raise ContentError(str(exc)) from exc
        plugin_id, plugin_version = registry.provider(effect["kind"])
        if required_plugins.get(plugin_id) != plugin_version:
            raise ContentError(f"effect provider not declared: {plugin_id}@{plugin_version}")
        if effect["trigger"] != mechanism.trigger:
            raise ContentError(f"unsupported trigger: {effect['id']}")
        _validate(mechanism.params_schema, effect["params"], f"effect {effect['id']} params")
    for progression in content["progression"]:
        if progression["template_id"] not in weapons:
            raise ContentError(f"unknown upgrade template: {progression['template_id']}")
        levels: set[int] = set()
        for row in progression["rows"]:
            if row["to_level"] != row["from_level"] + 1 or row["from_level"] in levels:
                raise ContentError(f"invalid or duplicate upgrade level: {progression['id']}")
            levels.add(row["from_level"])
            if row["requires_progress"] not in progress:
                raise ContentError(f"unknown progress reference: {row['requires_progress']}")
            cost_ids = [cost["resource_id"] for cost in row["costs"]]
            if len(cost_ids) != len(set(cost_ids)):
                raise ContentError(f"duplicate upgrade resource: {progression['id']}")
    policy = content["economy"]["trade_policy"]
    if policy["maximum_price_minor"] < policy["minimum_price_minor"]:
        raise ContentError("maximum trade price below minimum")
