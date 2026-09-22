import asyncio
import logging

logger = logging.getLogger("live-chat")


class RoomHost:
    """管理房间生命周期，并把连接、结算和通知能力注入玩法引擎。"""

    def __init__(self, database, hub, accounts, settlement, ranking, rewards, wallet, disconnect_grace=30.0):
        self.database = database
        self.hub = hub
        self.accounts = accounts
        self.settlement = settlement
        self.ranking = ranking
        self.rewards = rewards
        self.wallet = wallet
        self.game_rooms = {}
        self.room_seq = 0
        self.leave_timers = {}
        self.cleanup_tasks = set()
        self.disconnect_grace = disconnect_grace

    def find_user_room(self, username):
        for room in self.game_rooms.values():
            if room.has_member(username) or room.has_spectator(username):
                return room
        return None

    def attach_host(self, room):
        """把宿主能力注入房间：成员广播、视图分发、列表变更通知与托管同步。"""
        pending_rewards = set()
        def member_sockets():
            for socket, client_state in list(self.hub.clients.items()):
                user = client_state.get("user")
                if user and (room.has_member(user["username"])
                             or room.has_spectator(user["username"])):
                    yield socket

        async def broadcast_payload(payload):
            await asyncio.gather(*(self.hub.send_json(socket, payload) for socket in member_sockets()))

        async def broadcast_views():
            await self.ranking.publish_ratings(room)
            for username in tuple(pending_rewards):
                pending_rewards.discard(username)
                await self.rewards.publish_daily_rewards(username)
            targets = []
            for socket, client_state in list(self.hub.clients.items()):
                user = client_state.get("user")
                if not user:
                    continue
                username = user["username"]
                if room.has_member(username):
                    targets.append((socket, room.view_for(username)))
                elif room.has_spectator(username):
                    targets.append((socket, room.spectator_view(username)))
            await asyncio.gather(*(self.hub.send_json(socket, view) for socket, view in targets))

        async def on_rooms_changed():
            await self.broadcast_room_list()

        def sync_escrow(username, amount):
            self.settlement.set_escrow(username, room.id, amount)

        async def on_dissolve_requested(reason):
            await self.dissolve_room(room, reason)

        async def on_rebuy_requested():
            await self.rebuy_members(room)

        room.broadcast_payload = broadcast_payload
        room.broadcast_views = broadcast_views
        room.on_rooms_changed = on_rooms_changed
        room.on_dissolve_requested = on_dissolve_requested
        room.on_rebuy_requested = on_rebuy_requested
        room.display_name = self.accounts.display_name
        room.player_avatar = lambda username: self.accounts.get_profile(username)["avatar"]
        room.set_escrow = sync_escrow
        room.player_rating = self.ranking.get_rating
        room.pending_rating_updates = set()

        def record_ratings(hand_id, starts, endings, stakes=None, statistics=None):
            results = self.settlement.record_hand_ratings(room, hand_id, starts, endings,
                                          stakes=stakes, statistics=statistics)
            room.pending_rating_updates.update(results)
            if stakes is not None and room.game_type == "holdem":
                pending_rewards.update(stakes)
            return results

        room.record_ratings = record_ratings

    async def rebuy_members(self, room):
        """结清上一轮，再让成员以标准买入额和全新筹码进入下一轮。"""
        for username in list(room.seating):
            member = room.members[username]
            old_ref = member.get("buyin_ref", f"room:{room.id}:{username}")
            new_ref = f"room:{room.id}:match:{room.match_no}:{username}"
            rebought, balance = self.settlement.settle_and_rebuy_room_member(
                room, username, member["stack"], member.get("paid", room.buy_in),
                old_ref, new_ref,
            )
            if not rebought:
                logger.info("%s 金币不足，未能重新买入 %s", username, room.id)
                await self.hub.send_to_user(username, {"type": "game_error",
                                              "message": f"金币不足 {room.buy_in:,.2f}，已离桌"})
                room.remove_member(username)
                await self.hub.send_to_user(
                    username, {"type": "room_closed", "reason": "已结算离桌"},
                )
                if balance is not None:
                    await self.wallet.push_balance(username, balance)
                continue
            member["stack"] = room.buy_in
            member["paid"] = room.buy_in
            member["buyin_ref"] = new_ref
            await self.wallet.push_balance(username, balance)   # 客户端金币牌要立刻反映扣款
        if room.owner not in room.members and room.seating:
            room.owner = room.seating[0]
        await room.broadcast_views()
        await self.broadcast_room_list()

    async def broadcast_room_list(self):
        await self.hub.broadcast(
            {"type": "room_list", "rooms": [room.summary() for room in self.game_rooms.values()]}
        )

    async def dissolve_room(self, room, reason):
        await room.finish_pending_settlement()
        room.close()
        self.game_rooms.pop(room.id, None)
        # 手牌进行中解散才是「流局」；打完后的正常解散按「结算」入账
        detail = (
            f"游戏厅流局：{room.name}" if room.in_hand()
            else f"游戏厅结算：{room.name}"
        )
        for username, refund in room.pending_refunds().items():
            self.settlement.settle_room_coins(room, username, refund, detail, room.member_paid(username))
        with self.database() as conn, conn:
            conn.execute("DELETE FROM game_escrows WHERE room_id = ?", (room.id,))
        logger.info("game room %s dissolved: %s", room.id, reason)
        await room.broadcast_payload({"type": "room_closed", "reason": reason})
        await self.broadcast_room_list()

    async def dissolve_empty_room(self, room):
        """成员走光后没有可看的对局：移除房间并让残留观战者返回大厅。"""
        room.close()
        self.game_rooms.pop(room.id, None)
        await room.broadcast_payload({"type": "room_closed", "reason": "对局已结束"})
        await self.broadcast_room_list()

    async def leave_room_internal(self, room, username):
        if room.has_spectator(username):
            room.remove_spectator(username)
            logger.info("%s stopped watching game room %s", username, room.id)
            await self.hub.send_to_user(username, {"type": "room_closed", "reason": "已退出观战"})
            if not room.members:
                await self.dissolve_empty_room(room)
            return
        await room.finish_pending_settlement()
        if room.in_hand() and room.has_member(username):
            room.settle_leaving_rating(username)
        member = room.remove_member(username)
        if not member:
            return
        mid_hand = room.note_leave(username)
        self.settlement.set_escrow(username, room.id, None)
        self.settlement.settle_room_coins(room, username, member["stack"],
                          f"游戏厅离桌：{room.name}", member.get("paid"),
                          member.get("buyin_ref"))
        await self.ranking.publish_ratings(room)
        logger.info("%s left game room %s", username, room.id)
        await self.hub.send_to_user(username, {"type": "room_closed", "reason": "已离桌"})
        if not room.members:
            await self.dissolve_empty_room(room)
            return
        if mid_hand:
            await room.progress_game()
        else:
            await room.broadcast_views()
        await self.broadcast_room_list()

    def cancel_leave_timer(self, room_id, username):
        handle = self.leave_timers.pop((room_id, username), None)
        if handle:
            handle.cancel()

    def fire_leave_timer(self, room_id, username):
        self.leave_timers.pop((room_id, username), None)
        task = asyncio.ensure_future(self.delayed_room_cleanup(room_id, username))
        self.cleanup_tasks.add(task)
        task.add_done_callback(self.cleanup_tasks.discard)

    async def delayed_room_cleanup(self, room_id, username):
        """断线宽限期内没有回到游戏厅（或直播间），再结算房间去留。"""
        await asyncio.sleep(self.disconnect_grace)
        room = self.game_rooms.get(room_id)
        if not room:
            return
        if not room.has_member(username) and not room.has_spectator(username):
            return
        for client_state in self.hub.clients.values():
            other = client_state.get("user")
            if other and other["username"] == username:
                return
        if room.has_spectator(username):
            # 观战者断线不牵动对局，直接移除记录即可
            room.remove_spectator(username)
            return
        if room.owner == username:
            await self.dissolve_room(room, "房主离开游戏厅较久")
        else:
            await self.leave_room_internal(room, username)

    async def cleanup_rooms_on_disconnect(self, state):
        user = state.get("user")
        if not user:
            return
        username = user["username"]
        for client_state in self.hub.clients.values():
            other = client_state.get("user")
            if other and other["username"] == username:
                return
        room = self.find_user_room(username)
        if not room:
            return
        # 页面间跳转（游戏厅 <-> 直播间）会短暂断线，延迟再结算，回到页面即取消
        self.cancel_leave_timer(room.id, username)
        self.leave_timers[(room.id, username)] = (
            asyncio.get_running_loop().call_later(
                self.disconnect_grace,
                self.fire_leave_timer,
                room.id,
                username,
            )
        )

    def on_user_authenticated(self, username):
        """登录/恢复会话时取消该用户的离桌倒计时。"""
        room = self.find_user_room(username)
        if room:
            self.cancel_leave_timer(room.id, username)

    async def aclose(self):
        for handle in self.leave_timers.values():
            handle.cancel()
        self.leave_timers.clear()
        tasks = tuple(self.cleanup_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cleanup_tasks.clear()
        for room in self.game_rooms.values():
            room.close()
        self.game_rooms.clear()
