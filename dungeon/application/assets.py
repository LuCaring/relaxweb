"""Authoritative Beta asset operations. Not registered as a public protocol yet."""

import hashlib
import json
import time

from dungeon.application.wallet import SharedWalletPort
from dungeon.application.wallet import MAX_MINOR
from dungeon.storage.assets import bump_asset_revision, ensure_available
from dungeon.domain.errors import DungeonError
from dungeon.legacy.receipts import REQUEST_ID_PATTERN
from dungeon.legacy.effects import MAX_STAT, STAT_FIELDS


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _natural(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DungeonError("invalid_request", f"{label}无效")


def validate_upgrade_plan(item, quote, target_level):
    """Core validation is independent of the injected content policy."""
    if not isinstance(quote, dict) or quote.get("item_id") != item["item_id"] or quote.get("item_version") != item["version"]:
        raise DungeonError("invalid_config", "升级计划与装备不匹配")
    if quote.get("target_level") != target_level or target_level != item["upgrade_level"] + 1:
        raise DungeonError("invalid_config", "升级等级计划无效")
    if not isinstance(quote.get("ruleset_id"), str) or not quote["ruleset_id"]:
        raise DungeonError("invalid_config", "规则版本无效")
    costs = quote.get("costs")
    if not isinstance(costs, list) or not costs:
        raise DungeonError("invalid_config", "升级成本无效")
    resources = set()
    total = 0
    for entry in costs:
        if not isinstance(entry, dict) or set(entry) != {"resource_id", "amount"}:
            raise DungeonError("invalid_config", "升级成本无效")
        resource, amount = entry["resource_id"], entry["amount"]
        if (not isinstance(resource, str) or resource in resources or
                not (resource == "wallet:coins" or
                     (resource.startswith("material:") and len(resource) > len("material:"))) or
                isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= MAX_MINOR):
            raise DungeonError("invalid_config", "升级成本无效")
        resources.add(resource)
        total += amount
    if total < 1 or total > MAX_MINOR:
        raise DungeonError("invalid_config", "升级成本无效")
    stats = quote.get("stats")
    previous = item["stats"]
    if not isinstance(stats, dict) or stats == previous or set(stats) != set(previous):
        raise DungeonError("invalid_config", "升级属性无效")
    for stat, value in stats.items():
        if stat not in STAT_FIELDS or isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_STAT:
            raise DungeonError("invalid_config", "升级属性无效")


class AssetService:
    def __init__(self, database, growth_policy, wallet=None, clock=None):
        self.database = database
        self.policy = growth_policy
        self.wallet = wallet or SharedWalletPort()
        self.clock = clock or time.time

    @staticmethod
    def _user(conn, user_id):
        _natural(user_id, "用户编号")
        row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise DungeonError("auth_required", "用户不存在")
        return row[0]

    @staticmethod
    def _item(conn, username, item_id):
        if not isinstance(item_id, str) or REQUEST_ID_PATTERN.fullmatch(item_id) is None:
            raise DungeonError("invalid_request", "装备编号无效")
        row = conn.execute("""SELECT template_id,version,beta_upgrade_level,stats_json,location
            FROM dungeon_items WHERE item_id=? AND owner=?""", (item_id, username)).fetchone()
        if row is None or row[4] == "sold":
            raise DungeonError("not_found", "装备不存在")
        if row[4] != "bag":
            raise DungeonError("forbidden", "装备尚未领取")
        try:
            stats = json.loads(row[3])
        except (TypeError, ValueError) as error:
            raise DungeonError("invalid_save", "装备属性存档异常") from error
        if not isinstance(stats, dict):
            raise DungeonError("invalid_save", "装备属性存档异常")
        return {"item_id": item_id, "template_id": row[0], "version": row[1],
                "upgrade_level": row[2], "stats": stats}

    @staticmethod
    def _progress(conn, user_id):
        return {row[0] for row in conn.execute(
            "SELECT progress_id FROM dungeon_beta_progress WHERE user_id=?", (user_id,))}

    def quote_upgrade(self, user_id, item_id, target_level):
        with self.database() as conn:
            username = self._user(conn, user_id)
            item = self._item(conn, username, item_id)
            ensure_available(conn, username, item_id)
            quote = self.policy.quote(item, self._progress(conn, user_id), target_level)
            validate_upgrade_plan(item, quote, target_level)
            return quote

    def upgrade(self, user_id, request_id, item_id, target_level, *, expected_item_version,
                expected_ruleset_id, expected_costs):
        if not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
            raise DungeonError("invalid_request", "请求编号无效")
        _natural(user_id, "用户编号")
        _natural(target_level, "升级等级")
        _natural(expected_item_version, "装备版本")
        if not isinstance(expected_ruleset_id, str) or not expected_ruleset_id:
            raise DungeonError("invalid_request", "规则版本无效")
        if not isinstance(expected_costs, list) or any(
                not isinstance(x, dict) or set(x) != {"resource_id", "amount"}
                or not isinstance(x["resource_id"], str) or isinstance(x["amount"], bool)
                or not isinstance(x["amount"], int) or x["amount"] < 0 for x in expected_costs):
            raise DungeonError("invalid_request", "预期成本无效")
        payload = {"kind": "upgrade", "item_id": item_id, "target_level": target_level,
                   "expected_item_version": expected_item_version,
                   "expected_ruleset_id": expected_ruleset_id, "expected_costs": expected_costs}
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            username = self._user(conn, user_id)
            old = conn.execute("""SELECT request_hash,status,result_json FROM dungeon_beta_receipts
                WHERE user_id=? AND request_id=?""", (user_id, request_id)).fetchone()
            if old:
                if old[0] != digest:
                    raise DungeonError("request_conflict", "请求编号已用于其他操作")
                result = json.loads(old[2])
                return {**result, "replayed": True}
            item = self._item(conn, username, item_id)
            if item["version"] != expected_item_version:
                raise DungeonError("version_conflict", "装备已变化，请刷新")
            ensure_available(conn, username, item_id)
            quote = self.policy.quote(item, self._progress(conn, user_id), target_level)
            validate_upgrade_plan(item, quote, target_level)
            if quote["ruleset_id"] != expected_ruleset_id or quote["costs"] != expected_costs:
                raise DungeonError("quote_changed", "升级报价已变化，请重新确认")
            material_costs = [(entry["resource_id"][len("material:"):], entry["amount"])
                              for entry in quote["costs"] if entry["resource_id"].startswith("material:")]
            coin_cost = sum(entry["amount"] for entry in quote["costs"]
                            if entry["resource_id"] == "wallet:coins")
            for material_id, amount in material_costs:
                if not material_id:
                    raise DungeonError("invalid_config", "材料编号无效")
                held = conn.execute("""SELECT amount FROM dungeon_material_balances
                    WHERE user_id=? AND material_id=?""", (user_id, material_id)).fetchone()
                if (held[0] if held else 0) < amount:
                    raise DungeonError("insufficient_materials", "材料不足")
            if self.wallet.balance(conn, username) < coin_cost:
                raise DungeonError("insufficient_funds", "金币不足")
            for material_id, amount in material_costs:
                conn.execute("""UPDATE dungeon_material_balances SET amount=amount-?
                    WHERE user_id=? AND material_id=?""", (amount, user_id, material_id))
            balance = self.wallet.change(conn, username, -coin_cost, kind="dungeon_beta_upgrade",
                                         ref=f"dungeon:beta:upgrade:{user_id}:{request_id}", detail="地下城装备升级") if coin_cost else self.wallet.balance(conn, username)
            updated = conn.execute("""UPDATE dungeon_items SET stats_json=?,beta_upgrade_level=?,version=version+1
                WHERE item_id=? AND owner=? AND version=? AND location='bag'""",
                (_json(quote["stats"]), target_level, item_id, username, expected_item_version))
            if updated.rowcount != 1:
                raise DungeonError("version_conflict", "装备已变化，请刷新")
            revision = bump_asset_revision(conn, user_id)
            conn.execute("""UPDATE dungeon_profiles SET version=version+1,updated_at=?
                WHERE username=?""", (int(self.clock()), username))
            result = {"type": "dungeon_beta_result", "protocol_version": 1,
                      "request_id": request_id, "result_kind": "upgrade", "replayed": False,
                      "asset_revision": revision, "result": {"item_id": item_id,
                      "item_version": expected_item_version + 1, "upgrade_level": target_level,
                      "stats": quote["stats"], "spent": quote["costs"],
                      "wallet_at_commit": {"coin_minor": balance}}}
            conn.execute("""INSERT INTO dungeon_beta_receipts
                (user_id,request_id,request_hash,status,result_json,created_at)
                VALUES (?,?,?,'success',?,?)""", (user_id, request_id, digest, _json(result), int(self.clock())))
            return result
