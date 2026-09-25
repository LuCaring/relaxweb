"""Authenticated ``dungeon_beta_*`` WebSocket adapter over the Beta services.

Permanent operations only: state, catalog, upgrade quotes/commits, directed offers.
Run-time messages (input/frame/sync) await host lifecycle/transport integration. Every handler
resolves the stable user id from the authenticated session per call and never
trusts ownership fields sent by the client.
"""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

from dungeon.application.assets import AssetService
from dungeon.application.state import StateService
from dungeon.application.trading import TradeService
from dungeon.content import ContentError, load_ruleset
from dungeon.contracts.json_validation import validation_errors
from dungeon.domain.errors import DungeonError
from dungeon.domain.growth import FixedLevelPolicy
from dungeon.domain.trading import TradePolicy
from dungeon.legacy.receipts import REQUEST_ID_PATTERN

logger = logging.getLogger("live-chat")

RELEASE_PATH = Path(__file__).resolve().parents[2] / "content" / "dungeon" / "release.json"
MESSAGE_SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "dungeon" /
                             "schemas" / "beta_messages.schema.json").read_text())
REQUEST_DEFINITIONS = {
    "dungeon_beta_get_state": "get_state_request",
    "dungeon_beta_get_catalog": "get_catalog_request",
    "dungeon_beta_quote_upgrade": "quote_upgrade_request",
    "dungeon_beta_upgrade": "upgrade_request",
    "dungeon_beta_create_offer": "create_offer_request",
    "dungeon_beta_list_offers": "list_offers_request",
    "dungeon_beta_accept_offer": "accept_offer_request",
    "dungeon_beta_cancel_offer": "cancel_offer_request",
    "dungeon_beta_get_receipt": "get_receipt_request",
}
RETRYABLE_CODES = frozenset({"storage_busy", "storage_failed"})


class DungeonBetaProtocol:
    def __init__(self, *, database, hub, release_path=None, clock=None):
        self.database = database
        self.hub = hub
        self.clock = clock or time.time
        self.ruleset = None
        self.assets = None
        self.trades = None
        self.state = StateService(database)
        path = Path(release_path) if release_path is not None else RELEASE_PATH
        try:
            self.ruleset = load_ruleset(path)
        except (ContentError, OSError, ValueError) as error:
            # A broken release must not take the legacy protocol down with it;
            # every beta handler reports ruleset_unavailable instead.
            logger.error("dungeon beta ruleset unavailable at %s: %s", path, error)
        if self.ruleset is not None:
            growth = FixedLevelPolicy(self.ruleset.ruleset_id,
                                      self.ruleset.mutable_content("progression"))
            self.assets = AssetService(database, growth, clock=lambda: self.clock())
            policy = TradePolicy.from_economy(self.ruleset.content("economy"))
            self.trades = TradeService(database, policy, clock=lambda: self.clock())
        self._slots = asyncio.Semaphore(4)

    def handlers(self):
        return {"dungeon_beta_get_state": self.handle_get_state,
                "dungeon_beta_get_catalog": self.handle_get_catalog,
                "dungeon_beta_quote_upgrade": self.handle_quote_upgrade,
                "dungeon_beta_upgrade": self.handle_upgrade,
                "dungeon_beta_create_offer": self.handle_create_offer,
                "dungeon_beta_list_offers": self.handle_list_offers,
                "dungeon_beta_accept_offer": self.handle_accept_offer,
                "dungeon_beta_cancel_offer": self.handle_cancel_offer,
                "dungeon_beta_get_receipt": self.handle_get_receipt}

    # ---------------------------------------------------------------- plumbing

    async def _blocking(self, function, *args, **kwargs):
        async with self._slots:
            return await asyncio.to_thread(function, *args, **kwargs)

    async def _fail(self, websocket, request_id, error):
        if not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
            request_id = None
        if isinstance(error, DungeonError):
            code, message = error.code, str(error)
        elif isinstance(error, sqlite3.OperationalError):
            code, message = "storage_busy", "存储繁忙，请稍后重试"
        else:
            code, message = "storage_failed", "存储暂时不可用，请稍后重试"
        await self.hub.send_json(websocket, {
            "type": "dungeon_beta_error", "protocol_version": 1, "request_id": request_id,
            "code": code, "message": message, "retryable": code in RETRYABLE_CODES,
            "details": {}})

    def _user_id(self, username):
        with self.database() as conn:
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            raise DungeonError("auth_required", "用户不存在")
        return row[0]

    def _account_snapshot(self, user_id):
        """Point read for notifications: username, coins and camp revision."""
        with self.database() as conn:
            row = conn.execute("SELECT username,round(coins,2) FROM users WHERE id=?",
                               (user_id,)).fetchone()
            if row is None:
                raise DungeonError("auth_required", "用户不存在")
            revision = conn.execute("""SELECT revision FROM dungeon_beta_asset_revisions
                WHERE user_id=?""", (user_id,)).fetchone()
        return row[0], row[1], revision[0] if revision else None

    async def _refresh(self, user_id):
        """Refresh cached coins on the loop, read the account off the loop."""
        username, coins, revision = await self._blocking(self._account_snapshot, user_id)
        for client_state in list(self.hub.clients.values()):
            account = client_state.get("user")
            if account and account.get("username") == username:
                account["coins"] = coins
        return username, revision

    async def _invalidate(self, username, revision):
        """Hint every connection of one account that its camp state changed."""
        await self.hub.send_to_user(username, {"type": "dungeon_beta_invalidate",
                                               "protocol_version": 1, "asset_revision": revision})

    async def _guard(self, websocket, state, data):
        """Return (request_id, user_id, username) or None after replying."""
        request_id = data.get("request_id") if isinstance(data, dict) else None
        user = state.get("user")
        if not user:
            await self._fail(websocket, request_id, DungeonError("auth_required", "请先登录"))
            return None
        if self.ruleset is None:
            await self._fail(websocket, request_id,
                             DungeonError("ruleset_unavailable", "地下城规则集不可用"))
            return None
        definition = REQUEST_DEFINITIONS.get(data.get("type"))
        if definition is None or validation_errors(
                {"$ref": "#/$defs/" + definition, "$defs": MESSAGE_SCHEMA["$defs"]}, data):
            await self._fail(websocket, request_id,
                             DungeonError("invalid_request", "地下城请求格式无效"))
            return None
        try:
            user_id = await self._blocking(self._user_id, user["username"])
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return None
        return request_id, user_id, user["username"]

    @staticmethod
    def _read_result(request_id, kind, result):
        return {"type": "dungeon_beta_result", "protocol_version": 1,
                "request_id": request_id, "result_kind": kind, "result": result}

    # --------------------------------------------------------------- catalog

    async def handle_get_state(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, _ = guard
        try:
            snapshot = await self._blocking(self.state.get_state, user_id)
            snapshot["ruleset"] = self.ruleset.reference()
            await self.hub.send_json(websocket, self._read_result(request_id, "get_state", snapshot))
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)

    async def handle_get_catalog(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, _, _ = guard
        await self.hub.send_json(websocket, self._read_result(
            request_id, "get_catalog", self.ruleset.public_catalog()))

    # --------------------------------------------------------------- upgrades

    async def handle_quote_upgrade(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, _ = guard
        try:
            quote = await self._blocking(self.assets.quote_upgrade, user_id,
                                         data.get("item_id"), data.get("target_level"))
            await self.hub.send_json(websocket, self._read_result(
                request_id, "quote_upgrade", quote))
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)

    async def handle_upgrade(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        try:
            result = await self._blocking(
                self.assets.upgrade, user_id, data.get("request_id"), data.get("item_id"),
                data.get("target_level"), expected_item_version=data.get("expected_item_version"),
                expected_ruleset_id=data.get("expected_ruleset_id"),
                expected_costs=data.get("expected_costs"))
            await self.hub.send_json(websocket, result)
            if not result.get("replayed"):
                await self._refresh(user_id)  # coin caches; the revision comes from the result
                await self._invalidate(username, result.get("asset_revision"))
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)

    # ----------------------------------------------------------------- offers

    async def handle_create_offer(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        try:
            result = await self._blocking(
                self.trades.create_offer, user_id, data.get("request_id"), data.get("item_id"),
                expected_item_version=data.get("expected_item_version"),
                buyer_id=data.get("buyer_id"), price_minor=data.get("price_minor"),
                expected_policy_version=data.get("expected_trade_policy_version"),
                expected_fee_minor=data.get("expected_fee_minor"))
            await self.hub.send_json(websocket, result)
            if not result.get("replayed"):
                await self._invalidate(username, result.get("asset_revision"))
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)

    async def handle_list_offers(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, _ = guard
        try:
            offers = await self._blocking(self.trades.list_offers, user_id)
            await self.hub.send_json(websocket, self._read_result(
                request_id, "list_offers", {"offers": offers}))
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)

    async def handle_accept_offer(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, _ = guard
        try:
            result = await self._blocking(
                self.trades.accept_offer, user_id, data.get("request_id"), data.get("offer_id"),
                expected_offer_version=data.get("expected_offer_version"))
            await self.hub.send_json(websocket, result)
            if not result.get("replayed"):
                buyer_username, _ = await self._refresh(user_id)
                seller_username, seller_revision = await self._refresh(
                    result["result"]["seller_id"])
                await self._invalidate(buyer_username, result.get("asset_revision"))
                await self._invalidate(seller_username, seller_revision)
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)

    async def handle_cancel_offer(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        try:
            result = await self._blocking(
                self.trades.cancel_offer, user_id, data.get("request_id"), data.get("offer_id"),
                expected_offer_version=data.get("expected_offer_version"))
            await self.hub.send_json(websocket, result)
            if not result.get("replayed"):
                await self._invalidate(username, result.get("asset_revision"))
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)

    # --------------------------------------------------------------- receipts

    async def handle_get_receipt(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, _ = guard
        lookup = data.get("lookup_request_id")
        try:
            def read():
                with self.database() as conn:
                    row = conn.execute("""SELECT result_json FROM dungeon_beta_receipts
                        WHERE user_id=? AND request_id=?""", (user_id, lookup)).fetchone()
                return None if row is None else json.loads(row[0])
            receipt = await self._blocking(read)
            # Not found is not a terminal failure: the original write may still
            # be in flight or failed without occupying the request id.
            await self.hub.send_json(websocket, self._read_result(
                request_id, "get_receipt", {"found": receipt is not None, "receipt": receipt}))
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
