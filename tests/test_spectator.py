"""游戏厅观战协议回归：临时 SQLite + 本机 WebSocket，不接触实际账号。

覆盖：开局前/后进入、观战视图（第一视角、无操作字段）、切换观看目标、
观战聊天标注、观战动作无效、随时退出、被看玩家离桌回退与解散广播。
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets
import chat_server as server


async def receive(ws, kind, predicate=lambda msg: True):
    async def read():
        while True:
            msg = json.loads(await ws.recv())
            if msg['type'] == kind and predicate(msg):
                return msg
            if msg['type'].endswith('_error') and msg['type'] != kind:
                raise AssertionError(f'unexpected error: {msg}')
    return await asyncio.wait_for(read(), 5)


async def send(ws, **payload):
    await ws.send(json.dumps(payload))


def coins():
    with server.database() as conn:
        return dict(conn.execute('SELECT username, coins FROM users'))


async def main():
    names = ['player' + str(i) for i in range(4)]
    spec = 'watcher'
    joiner = 'latecomer'
    with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DB_FILE', str(Path(tmp) / 'spectator.db')):
        server.init_db()
        with server.database() as conn, conn:
            for name in names + [spec, joiner]:
                conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) VALUES (?, '', '', 0, 1000)", (name,))
        tokens = {name: server.create_session(name) for name in names + [spec, joiner]}
        async with websockets.serve(server.handler, '127.0.0.1', 0) as host:
            uri = 'ws://127.0.0.1:' + str(host.sockets[0].getsockname()[1])
            sockets = []
            try:
                for name in names + [spec, joiner]:
                    ws = await websockets.connect(uri)
                    sockets.append(ws)
                    await send(ws, type='resume', token=tokens[name])
                    await receive(ws, 'resume_success')
                player, spect, late = sockets[:4], sockets[4], sockets[5]

                await asyncio.sleep(3.1)
                await send(player[0], type='create_room', game='mahjong', name='观战回归', buy_in=100, blind=1)
                room_id = (await receive(player[0], 'game_joined'))['room']['room_id']
                for ws in player[1:]:
                    await send(ws, type='join_room', room_id=room_id)
                    await receive(ws, 'game_joined')

                # 开局前不能观战
                await send(spect, type='join_room', room_id=room_id, spectate=True)
                error = await receive(spect, 'game_error')
                assert '尚未' in error['message'], error
                await asyncio.sleep(1.1)

                await send(player[0], type='start_game')
                own_views = {name: await receive(ws, 'game_update', lambda m: m.get('to_act'))
                             for name, ws in zip(names, player)}

                # 开局后观战进入：不扣币、默认看首位成员、拿到其第一视角
                await send(spect, type='join_room', room_id=room_id, spectate=True)
                joined = (await receive(spect, 'game_joined'))['room']
                assert joined['spectator'] is True and joined['watching'] == names[0], joined
                assert joined['your_hand'] == own_views[names[0]]['your_hand'], joined
                assert 'your_options' not in joined, joined
                assert coins()[spec] == 1000, 'spectator must not pay a buy-in'

                # 开局后普通加入被拒绝；满员房间仍可以观战进入
                await send(late, type='join_room', room_id=room_id)
                error = await receive(late, 'game_error')
                assert '观战' in error['message'], error
                await asyncio.sleep(1.1)
                await send(late, type='join_room', room_id=room_id, spectate=True)
                late_view = (await receive(late, 'game_joined'))['room']
                assert late_view['spectator'] is True and late_view['watching'] == names[0], late_view
                assert coins()[joiner] == 1000, 'spectator must not pay a buy-in'

                # 观战者的对局动作与续手确认一律无效
                snapshot = json.dumps(server.game_rooms[room_id].game, default=list, sort_keys=True)
                await send(spect, type='poker_action', action='discard', index=0)
                await send(spect, type='hand_continue')
                await send(spect, type='settle_vote', choice='dissolve')
                # Same-socket get_room is a processing barrier for the prior actions.
                await send(spect, type='get_room')
                await receive(spect, 'game_update', lambda m: m.get('spectator'))
                assert json.dumps(server.game_rooms[room_id].game, default=list, sort_keys=True) == snapshot
                assert not server.game_rooms[room_id].votes

                # 切换观看目标
                await asyncio.sleep(.3)
                await send(spect, type='watch_player', username=names[2])
                switched = await receive(spect, 'game_update', lambda m: m.get('spectator'))
                assert switched['watching'] == names[2], switched
                assert switched['your_hand'] == own_views[names[2]]['your_hand'], switched
                assert 'your_options' not in switched, switched
                await asyncio.sleep(.3)
                await send(spect, type='watch_player', username='nobody')
                error = await receive(spect, 'game_error')
                assert '不在本房间' in error['message'], error

                # 观战聊天：全房可见并带 spectator 标志；玩家消息对观战者可见
                await send(spect, type='room_chat', text='大家好，我来学习')
                marker = lambda m: m.get('text') == '大家好，我来学习'
                for ws in [spect, *player]:
                    message = await receive(ws, 'room_chat', marker)
                    assert message['spectator'] is True, message
                await send(player[1], type='room_chat', text='欢迎')
                message = await receive(spect, 'room_chat', lambda m: m.get('text') == '欢迎')
                assert message['spectator'] is False, message

                # 随时退出：对局不受影响，重进后 get_room 恢复观战视图
                await send(spect, type='leave_room')
                closed = await receive(spect, 'room_closed')
                assert closed['reason'] == '已退出观战', closed
                assert room_id in server.game_rooms
                await asyncio.sleep(1.1)
                await send(spect, type='join_room', room_id=room_id, spectate=True)
                await receive(spect, 'game_joined')
                await send(spect, type='get_room')
                view = await receive(spect, 'game_update', lambda m: m.get('spectator'))
                assert view['watching'] in names, view

                # 被看玩家离桌：观看目标自动回退到仍在座的成员
                await send(spect, type='watch_player', username=names[2])
                await receive(spect, 'game_update', lambda m: m.get('watching') == names[2])
                await send(player[2], type='leave_room')
                await receive(player[2], 'room_closed')
                fallback = await receive(spect, 'game_update',
                                         lambda m: m.get('spectator') and m.get('watching') != names[2])
                assert fallback['watching'] == names[0], fallback

                # 房主离场解散：房内成员与观战者都收到关闭通知，且全程未扣一分钱
                await send(player[0], type='leave_room')
                await asyncio.gather(*(receive(ws, 'room_closed') for ws in [spect, late, player[1], player[3]]))
                assert room_id not in server.game_rooms
                assert coins() == {name: 1000 for name in names + [spec, joiner]}
                with server.database() as conn:
                    assert conn.execute('SELECT count(*) FROM game_escrows').fetchone()[0] == 0
                print('PASS spectate join/view/switch/chat/leave/fallback/dissolve without paying')
            finally:
                for room in list(server.game_rooms.values()):
                    room.close()
                await asyncio.gather(*(ws.close() for ws in sockets))
                for timer in server.room_leave_timers.values():
                    timer.cancel()
                server.room_leave_timers.clear()


if __name__ == '__main__':
    asyncio.run(main())
