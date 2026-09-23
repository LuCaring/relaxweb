"""六款游戏共用观战协议与房间系统消息回归。"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games.base import create_room
from server.app import create_app
from server.schema import init_db
import server.database as storage


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))

    def last(self, kind):
        return next(message for message in reversed(self.messages) if message['type'] == kind)


async def main():
    with tempfile.TemporaryDirectory() as tmp, patch.object(storage, 'DB_FILE', str(Path(tmp) / 'spectator-games.db')):
        app = create_app(disconnect_grace=0)
        init_db(app.database)
        with app.database() as conn, conn:
            for name in ('p0', 'p1', 'p2', 'p3', 'watcher'):
                conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) VALUES (?, '', '', 0, 1000)", (name,))
            conn.execute("UPDATE users SET nickname = '观战员' WHERE username = 'watcher'")
        sockets = {name: Socket() for name in ('p0', 'p1', 'p2', 'p3', 'watcher')}
        for name, socket in sockets.items():
            app.hub.clients[socket] = {
                'user': {'username': name, 'nickname': name, 'role': 'user'},
                'send_lock': asyncio.Lock(),
            }
        try:
            for room_id, game in enumerate(('holdem', 'uno', 'guandan', 'mahjong', 'ludo', 'liarsbar'), 1):
                room = create_room(game, room_id=room_id, name=game, owner='p0', buy_in=100, blind=1)
                app.rooms.attach_host(room)
                for name in ('p0', 'p1', 'p2', 'p3')[:4 if game in ('guandan', 'mahjong') else 2]:
                    room.add_member(name, 100)
                app.rooms.game_rooms[room.id] = room
                try:
                    await room.start()
                    assert room.status == 'playing', game
                    for socket in sockets.values():
                        socket.messages.clear()
                    spectator = sockets['watcher']
                    app.hub.clients[spectator].pop('last_room_op', None)
                    await app.room_protocol.handle_join_room(spectator, app.hub.clients[spectator],
                                                             {'room_id': room.id, 'spectate': True})
                    joined = spectator.last('game_joined')['room']
                    assert joined['spectator'] and joined['watching'] == 'p0', game
                    assert 'your_options' not in joined, game
                    assert joined['players'] == room.view_for('p0')['players'], game
                    for key in ('your_hand', 'your_hole', 'your_flowers', 'my_team'):
                        assert joined.get(key) == room.view_for('p0').get(key), (game, key)
                    assert spectator.last('room_chat_history')['messages'] == [], game
                    notice = '观战员进入房间开始观战'
                    assert spectator.last('room_chat')['text'] == notice, game
                    assert sockets['p0'].last('room_chat')['text'] == notice, game
                    assert room.chat[-1]['system'] is True, game

                    app.hub.clients[spectator].pop('last_watch_switch', None)
                    await app.room_protocol.handle_watch_player(spectator, app.hub.clients[spectator],
                                                                {'username': 'p1'})
                    switched = spectator.last('game_update')
                    assert switched['watching'] == 'p1' and 'your_options' not in switched, game
                    for key in ('your_hand', 'your_hole', 'your_flowers', 'my_team'):
                        assert switched.get(key) == room.view_for('p1').get(key), (game, key)
                    assert len(room.chat) == 1, game

                    await app.room_protocol.handle_leave_room(spectator, app.hub.clients[spectator], {})
                    assert not room.has_spectator('watcher'), game
                    assert spectator.last('room_closed')['reason'] == '已退出观战', game
                    assert sockets['p0'].last('room_chat')['text'] == '观战员离开房间结束观战', game
                    assert [message['text'] for message in room.chat] == [
                        '观战员进入房间开始观战', '观战员离开房间结束观战'], game
                    if game == 'ludo':
                        app.hub.clients[spectator].pop('last_room_op', None)
                        await app.room_protocol.handle_join_room(spectator, app.hub.clients[spectator],
                                                                 {'room_id': room.id, 'spectate': True})
                        assert room.has_spectator('watcher')
                        state = app.hub.clients.pop(spectator)
                        try:
                            await app.rooms.delayed_room_cleanup(room.id, 'watcher')
                            assert not room.has_spectator('watcher')
                            assert sockets['p0'].last('room_chat')['text'] == '观战员离开房间结束观战'
                        finally:
                            app.hub.clients[spectator] = state
                    print(f'PASS {game} spectator join/view/switch/leave notices')
                finally:
                    room.close()
                    app.rooms.game_rooms.pop(room.id, None)
        finally:
            await app.aclose()


if __name__ == '__main__':
    asyncio.run(main())
