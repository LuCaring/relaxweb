"""Pure fixed-level upgrade policy backed by versioned content rows."""

from copy import deepcopy

from dungeon.domain.errors import DungeonError


class FixedLevelPolicy:
    def __init__(self, ruleset_id, progression_rows):
        if not isinstance(ruleset_id, str) or not ruleset_id:
            raise ValueError("ruleset_id required")
        self.ruleset_id = ruleset_id
        self._rows = {}
        for track in progression_rows:
            if track.get("strategy") != "fixed_level_table":
                continue
            template = track["template_id"]
            for row in track["rows"]:
                key = (template, row["from_level"], row["to_level"])
                if key in self._rows or isinstance(key[1], bool) or isinstance(key[2], bool) or key[2] != key[1] + 1:
                    raise ValueError("invalid or duplicate upgrade row")
                costs = row["costs"]
                resources = [entry["resource_id"] for entry in costs]
                if len(resources) != len(set(resources)) or not costs:
                    raise ValueError("duplicate or empty costs")
                for entry in costs:
                    amount = entry["amount"]
                    if (not (entry["resource_id"] == "wallet:coins" or entry["resource_id"].startswith("material:"))
                            or isinstance(amount, bool) or not isinstance(amount, int) or amount < 0):
                        raise ValueError("invalid upgrade cost")
                for change in row["changes"]:
                    value = change.get("value")
                    if (change.get("operation") != "item.stat_flat" or not isinstance(change.get("stat"), str)
                            or isinstance(value, bool) or not isinstance(value, int)):
                        raise ValueError("unsupported upgrade operation")
                self._rows[key] = deepcopy(row)

    def quote(self, item, progress_set, target_level):
        if isinstance(target_level, bool) or not isinstance(target_level, int):
            raise DungeonError("invalid_request", "升级等级无效")
        key = (item["template_id"], item["upgrade_level"], target_level)
        row = self._rows.get(key)
        if row is None:
            raise DungeonError("not_found", "升级路线不存在")
        requirement = row.get("requires_progress")
        if requirement and requirement not in progress_set:
            raise DungeonError("forbidden", "挑战进度尚未解锁升级")
        stats = deepcopy(item["stats"])
        for change in row["changes"]:
            stat = change["stat"]
            previous = stats.get(stat, 0)
            if isinstance(previous, bool) or not isinstance(previous, int):
                raise DungeonError("invalid_save", "装备属性无法升级")
            stats[stat] = previous + change["value"]
        return {"ruleset_id": self.ruleset_id, "item_id": item["item_id"],
                "item_version": item["version"], "target_level": target_level,
                "costs": deepcopy(row["costs"]), "stats": stats}
