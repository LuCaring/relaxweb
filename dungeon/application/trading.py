"""Authoritative directed trade: offer, reservation, expiry, atomic settlement.

All money moves through the shared wallet port in minor units inside one
BEGIN IMMEDIATE transaction; the fee is burned, never credited to an account.
"""

import hashlib
import json
import time
from uuid import uuid4

from dungeon.application.wallet import SharedWalletPort
from dungeon.domain.errors import DungeonError
from dungeon.legacy.receipts import REQUEST_ID_PATTERN
from dungeon.storage.assets import bump_asset_revision, release_item, reserve_item


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _natural(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DungeonError("invalid_request", f"{label}无效")


def _request_id(value):
    if not isinstance(value, str) or REQUEST_ID_PATTERN.fullmatch(value) is None:
        raise DungeonError("invalid_request", "请求编号无效")


class TradeService:
    def __init__(self, database, trade_policy, wallet=None, clock=None):
        self.database = database
        self.policy = trade_policy
        self.wallet = wallet or SharedWalletPort()
        self.clock = clock or time.time

    @staticmethod
    def _username(conn, user_id):
        _natural(user_id, "用户编号")
        row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise DungeonError("auth_required", "用户不存在")
        return row[0]

    @staticmethod
    def _item(conn, username, item_id):
        if not isinstance(item_id, str) or REQUEST_ID_PATTERN.fullmatch(item_id) is None:
            raise DungeonError("invalid_request", "装备编号无效")
        row = conn.execute("""SELECT template_id,template_version,display_name,visual_id,slot,
            quality,stats_json,beta_upgrade_level,version,location,locked
            FROM dungeon_items WHERE item_id=? AND owner=?""", (item_id, username)).fetchone()
        if row is None or row[9] == "sold":
            raise DungeonError("not_found", "装备不存在")
        try:
            stats = json.loads(row[6])
        except (TypeError, ValueError) as error:
            raise DungeonError("invalid_save", "装备属性存档异常") from error
        if not isinstance(stats, dict):
            raise DungeonError("invalid_save", "装备属性存档异常")
        return {"item_id": item_id, "template_id": row[0], "template_version": row[1],
                "display_name": row[2], "visual_id": row[3], "slot": row[4], "quality": row[5],
                "stats": stats, "upgrade_level": row[7], "version": row[8],
                "location": row[9], "locked": bool(row[10])}

    def _receipt(self, conn, user_id, request_id, digest):
        row = conn.execute("""SELECT request_hash,result_json FROM dungeon_beta_receipts
            WHERE user_id=? AND request_id=?""", (user_id, request_id)).fetchone()
        if row is None:
            return None
        if row[0] != digest:
            raise DungeonError("request_conflict", "请求编号已用于其他操作")
        return {**json.loads(row[1]), "replayed": True}

    def _save_receipt(self, conn, user_id, request_id, digest, result):
        conn.execute("""INSERT INTO dungeon_beta_receipts
            (user_id,request_id,request_hash,status,result_json,created_at)
            VALUES (?,?,?,'success',?,?)""",
                     (user_id, request_id, digest, _json(result), int(self.clock())))

    def _offer(self, conn, offer_id):
        if not isinstance(offer_id, str) or REQUEST_ID_PATTERN.fullmatch(offer_id) is None:
            raise DungeonError("invalid_request", "报价编号无效")
        row = conn.execute("""SELECT offer_id,seller_user_id,buyer_user_id,item_id,item_version,
            item_snapshot_json,price_minor,fee_minor,seller_net_minor,policy_version,status,
            offer_version,created_at,expires_at,finalized_at
            FROM dungeon_trade_offers WHERE offer_id=?""", (offer_id,)).fetchone()
        if row is None:
            raise DungeonError("not_found", "报价不存在")
        try:
            snapshot = json.loads(row[5])
        except (TypeError, ValueError) as error:
            raise DungeonError("invalid_save", "报价快照异常") from error
        return {"offer_id": row[0], "seller_user_id": row[1], "buyer_user_id": row[2],
                "item_id": row[3], "item_version": row[4], "item_snapshot": snapshot,
                "price_minor": row[6], "fee_minor": row[7], "seller_net_minor": row[8],
                "policy_version": row[9], "status": row[10], "offer_version": row[11],
                "created_at": row[12], "expires_at": row[13], "finalized_at": row[14]}

    @staticmethod
    def _require_reservation(conn, offer):
        row = conn.execute("""SELECT 1 FROM dungeon_asset_reservations
            WHERE asset_type='item' AND asset_id=? AND purpose='trade_offer'
            AND reservation_ref=?""", (offer["item_id"], offer["offer_id"])).fetchone()
        if row is None:
            raise DungeonError("invalid_save", "报价预留记录缺失")

    def _finalize_expired(self, conn, offer, now):
        """Commit an expiry transition; callers raise offer_expired after commit."""
        updated = conn.execute("""UPDATE dungeon_trade_offers
            SET status='expired',offer_version=offer_version+1,finalized_at=?
            WHERE offer_id=? AND status='open'""", (now, offer["offer_id"]))
        if updated.rowcount != 1:
            raise DungeonError("offer_closed", "报价已关闭")
        release_item(conn, offer["item_id"], "trade_offer", offer["offer_id"])
        return offer["offer_version"] + 1

    def _run(self, digest, user_id, request_id, action):
        """Receipt-gated write transaction. ``action(conn)`` returns the result.

        Returning ``None`` means the action committed an expiry transition;
        the caller then raises ``offer_expired`` after the commit.
        """
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._receipt(conn, user_id, request_id, digest)
            if replay is not None:
                return replay
            result = action(conn)
            if result is not None:
                self._save_receipt(conn, user_id, request_id, digest, result)
            return result

    # ------------------------------------------------------------------ offers

    def create_offer(self, user_id, request_id, item_id, *, expected_item_version,
                     buyer_id, price_minor, expected_policy_version, expected_fee_minor):
        _request_id(request_id)
        _natural(user_id, "用户编号")
        _natural(buyer_id, "买家编号")
        _natural(expected_item_version, "装备版本")
        _natural(expected_policy_version, "交易策略版本")
        _natural(expected_fee_minor, "预期手续费")
        payload = {"kind": "create_offer", "item_id": item_id,
                   "expected_item_version": expected_item_version, "buyer_id": buyer_id,
                   "price_minor": price_minor, "expected_policy_version": expected_policy_version,
                   "expected_fee_minor": expected_fee_minor}
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        now = int(self.clock())

        def action(conn):
            seller = self._username(conn, user_id)
            if buyer_id == user_id:
                raise DungeonError("invalid_request", "不能向自己发起交易")
            self._username(conn, buyer_id)
            self.policy.validate_price(price_minor)
            quote = self.policy.quote(price_minor, now)
            if expected_policy_version != self.policy.policy_version:
                raise DungeonError("quote_changed", "交易策略已变化，请重新确认")
            if expected_fee_minor != quote["fee_minor"]:
                raise DungeonError("quote_changed", "手续费已变化，请重新确认")
            item = self._item(conn, seller, item_id)
            if item["version"] != expected_item_version:
                raise DungeonError("version_conflict", "装备已变化，请刷新")
            snapshot = {key: item[key] for key in (
                "item_id", "template_id", "template_version", "display_name", "visual_id",
                "slot", "quality", "stats", "upgrade_level", "version")}
            offer_id = uuid4().hex
            reserve_item(conn, seller, item_id, "trade_offer", offer_id, now)
            conn.execute("""INSERT INTO dungeon_trade_offers
                (offer_id,seller_user_id,buyer_user_id,item_id,item_version,item_snapshot_json,
                 price_minor,fee_minor,seller_net_minor,policy_version,status,offer_version,
                 created_at,expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,'open',1,?,?)""",
                         (offer_id, user_id, buyer_id, item_id, item["version"], _json(snapshot),
                          quote["price_minor"], quote["fee_minor"], quote["seller_net_minor"],
                          quote["policy_version"], now, quote["expires_at"]))
            revision = bump_asset_revision(conn, user_id)
            return {"type": "dungeon_beta_result", "protocol_version": 1,
                    "request_id": request_id, "result_kind": "create_offer", "replayed": False,
                    "asset_revision": revision,
                    "result": {"offer_id": offer_id, "offer_version": 1, "status": "open",
                               "item_id": item_id, "item_version": item["version"],
                               "buyer_id": buyer_id, "price_minor": quote["price_minor"],
                               "fee_minor": quote["fee_minor"],
                               "seller_net_minor": quote["seller_net_minor"],
                               "policy_version": quote["policy_version"],
                               "expires_at": quote["expires_at"], "item_snapshot": snapshot}}

        return self._run(digest, user_id, request_id, action)

    # ----------------------------------------------------------------- closing

    def accept_offer(self, user_id, request_id, offer_id, *, expected_offer_version):
        _request_id(request_id)
        _natural(user_id, "用户编号")
        _natural(expected_offer_version, "报价版本")
        payload = {"kind": "accept_offer", "offer_id": offer_id,
                   "expected_offer_version": expected_offer_version}
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        now = int(self.clock())
        expired = None

        def action(conn):
            nonlocal expired
            buyer = self._username(conn, user_id)
            offer = self._offer(conn, offer_id)
            if offer["buyer_user_id"] != user_id:
                raise DungeonError("forbidden", "只有指定买家可以接受该报价")
            if offer["status"] == "open" and now > offer["expires_at"]:
                self._finalize_expired(conn, offer, now)
                expired = DungeonError("offer_expired", "报价已过期")
                return None
            if offer["status"] != "open":
                raise DungeonError("offer_expired" if offer["status"] == "expired" else "offer_closed",
                                   "报价已过期" if offer["status"] == "expired" else "报价已关闭")
            if offer["offer_version"] != expected_offer_version:
                raise DungeonError("version_conflict", "报价已变化，请刷新")
            # The offer froze price and fee at creation; the live policy cannot reprice it.
            seller = self._username(conn, offer["seller_user_id"])
            item = conn.execute("""SELECT version,location FROM dungeon_items
                WHERE item_id=?""", (offer["item_id"],)).fetchone()
            if item is None or item[1] == "sold":
                raise DungeonError("not_found", "装备不存在")
            if item[1] != "bag":
                raise DungeonError("invalid_save", "装备位置异常")
            if item[0] != offer["item_version"]:
                raise DungeonError("version_conflict", "装备已变化，报价失效")
            owned = conn.execute("""SELECT 1 FROM dungeon_items
                WHERE item_id=? AND owner=?""", (offer["item_id"], seller)).fetchone()
            if owned is None:
                raise DungeonError("not_found", "卖家已不再持有该装备")
            self._require_reservation(conn, offer)
            if self.wallet.balance(conn, buyer) < offer["price_minor"]:
                raise DungeonError("insufficient_funds", "金币不足")
            conn.execute("""INSERT OR IGNORE INTO dungeon_profiles
                (username,starter_granted,bag_capacity,version,created_at,updated_at)
                VALUES (?,0,60,1,?,?)""", (buyer, now, now))
            capacity = conn.execute("SELECT bag_capacity FROM dungeon_profiles WHERE username=?",
                                    (buyer,)).fetchone()[0]
            occupied = conn.execute("""SELECT COUNT(*) FROM dungeon_items
                WHERE owner=? AND location='bag'""", (buyer,)).fetchone()[0]
            location = "bag" if occupied < capacity else "pending"
            ref = f"dungeon:beta:trade:{offer['offer_id']}"
            self.wallet.change(conn, buyer, -offer["price_minor"], kind="dungeon_trade_buy",
                               ref=ref, detail="地下城购买装备")
            self.wallet.change(conn, seller, offer["seller_net_minor"], kind="dungeon_trade_sell",
                               ref=ref, detail="地下城出售装备")
            moved = conn.execute("""UPDATE dungeon_items SET owner=?,location=?,version=version+1
                WHERE item_id=? AND owner=? AND version=? AND location='bag'""",
                                 (buyer, location, offer["item_id"], seller, offer["item_version"]))
            if moved.rowcount != 1:
                raise DungeonError("version_conflict", "装备已变化，报价失效")
            # Reservation blocks equipping, so this only removes impossible dangling rows.
            conn.execute("DELETE FROM dungeon_loadout WHERE item_id=?", (offer["item_id"],))
            settled = conn.execute("""UPDATE dungeon_trade_offers
                SET status='accepted',offer_version=offer_version+1,finalized_at=?
                WHERE offer_id=? AND status='open' AND offer_version=?""",
                                   (now, offer["offer_id"], expected_offer_version))
            if settled.rowcount != 1:
                raise DungeonError("offer_closed", "报价已关闭")
            conn.execute("""INSERT INTO dungeon_trade_settlements
                (settlement_id,offer_id,buyer_user_id,seller_user_id,item_id,price_minor,
                 fee_minor,seller_net_minor,buyer_request_id,item_location,settled_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (uuid4().hex, offer["offer_id"], user_id, offer["seller_user_id"],
                          offer["item_id"], offer["price_minor"], offer["fee_minor"],
                          offer["seller_net_minor"], request_id, location, now))
            release_item(conn, offer["item_id"], "trade_offer", offer["offer_id"])
            bump_asset_revision(conn, offer["seller_user_id"])
            revision = bump_asset_revision(conn, user_id)
            balance = self.wallet.balance(conn, buyer)
            return {"type": "dungeon_beta_result", "protocol_version": 1,
                    "request_id": request_id, "result_kind": "accept_offer", "replayed": False,
                    "asset_revision": revision,
                    "result": {"offer_id": offer["offer_id"],
                               "offer_version": offer["offer_version"] + 1,
                               "status": "accepted", "item_id": offer["item_id"],
                               "seller_id": offer["seller_user_id"],
                               "item_version": offer["item_version"] + 1, "location": location,
                               "price_minor": offer["price_minor"],
                               "fee_minor": offer["fee_minor"],
                               "seller_net_minor": offer["seller_net_minor"],
                               "settlement_ref": ref,
                               "wallet_at_commit": {"coin_minor": balance}}}

        result = self._run(digest, user_id, request_id, action)
        if expired is not None:
            raise expired
        return result

    def cancel_offer(self, user_id, request_id, offer_id, *, expected_offer_version):
        _request_id(request_id)
        _natural(user_id, "用户编号")
        _natural(expected_offer_version, "报价版本")
        payload = {"kind": "cancel_offer", "offer_id": offer_id,
                   "expected_offer_version": expected_offer_version}
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        now = int(self.clock())
        expired = None

        def action(conn):
            nonlocal expired
            self._username(conn, user_id)
            offer = self._offer(conn, offer_id)
            if offer["seller_user_id"] != user_id:
                raise DungeonError("forbidden", "只有卖家可以撤销该报价")
            if offer["status"] == "open" and now > offer["expires_at"]:
                self._finalize_expired(conn, offer, now)
                expired = DungeonError("offer_expired", "报价已过期")
                return None
            if offer["status"] != "open":
                raise DungeonError("offer_expired" if offer["status"] == "expired" else "offer_closed",
                                   "报价已过期" if offer["status"] == "expired" else "报价已关闭")
            if offer["offer_version"] != expected_offer_version:
                raise DungeonError("version_conflict", "报价已变化，请刷新")
            cancelled = conn.execute("""UPDATE dungeon_trade_offers
                SET status='cancelled',offer_version=offer_version+1,finalized_at=?
                WHERE offer_id=? AND status='open' AND offer_version=?""",
                                     (now, offer["offer_id"], expected_offer_version))
            if cancelled.rowcount != 1:
                raise DungeonError("offer_closed", "报价已关闭")
            release_item(conn, offer["item_id"], "trade_offer", offer["offer_id"])
            revision = bump_asset_revision(conn, user_id)
            return {"type": "dungeon_beta_result", "protocol_version": 1,
                    "request_id": request_id, "result_kind": "cancel_offer", "replayed": False,
                    "asset_revision": revision,
                    "result": {"offer_id": offer["offer_id"],
                               "offer_version": offer["offer_version"] + 1,
                               "status": "cancelled", "item_id": offer["item_id"]}}

        result = self._run(digest, user_id, request_id, action)
        if expired is not None:
            raise expired
        return result

    # ------------------------------------------------------------------ reads

    def list_offers(self, user_id):
        """Expire due offers on read, then return the caller's offers only."""
        _natural(user_id, "用户编号")
        now = int(self.clock())
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._username(conn, user_id)
            for row in conn.execute("""SELECT offer_id FROM dungeon_trade_offers
                WHERE status='open' AND expires_at<=?""", (now,)).fetchall():
                offer = self._offer(conn, row[0])
                self._finalize_expired(conn, offer, now)
            offers = []
            for row in conn.execute("""SELECT offer_id FROM dungeon_trade_offers
                WHERE seller_user_id=? OR buyer_user_id=? ORDER BY created_at DESC, offer_id DESC""",
                                     (user_id, user_id)):
                offer = self._offer(conn, row[0])
                offers.append({
                    "role": "seller" if offer["seller_user_id"] == user_id else "buyer",
                    "counterparty_id": (offer["buyer_user_id"] if offer["seller_user_id"] == user_id
                                        else offer["seller_user_id"]),
                    **{key: offer[key] for key in (
                        "offer_id", "item_id", "item_version", "price_minor", "fee_minor",
                        "seller_net_minor", "policy_version", "status", "offer_version",
                        "created_at", "expires_at", "finalized_at")},
                    "item_snapshot": offer["item_snapshot"]})
            return offers

    def sweep_expired(self):
        """Maintenance path: finalize every due offer and release its reservation."""
        now = int(self.clock())
        swept = 0
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("""SELECT offer_id FROM dungeon_trade_offers
                WHERE status='open' AND expires_at<=?""", (now,)).fetchall():
                offer = self._offer(conn, row[0])
                self._finalize_expired(conn, offer, now)
                swept += 1
            return swept
