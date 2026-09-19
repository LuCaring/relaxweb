"""四人游戏离桌回归：临时 SQLite + 本机 WebSocket，不接触实际账号。"""
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
            if msg['type'].endswith('_error'):
                raise AssertionError(msg)
            if msg['type'] == kind and predicate(msg):
                return msg
    return await asyncio.wait_for(read(), 5)


async def send(ws, **payload):
    await ws.send(json.dumps(payload))


async def main():
    names = ['player' + str(i) for i in range(4)]
    with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DB_FILE', str(Path(tmp) / 'leave.db')):
        server.init_db()
        with server.database() as conn, conn:
            for name in names:
                conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) VALUES (?, '', '', 0, 1000)", (name,))
        tokens = {name: server.create_session(name) for name in names}
        async with websockets.serve(server.handler, '127.0.0.1', 0) as host:
            uri = 'ws://127.0.0.1:' + str(host.sockets[0].getsockname()[1])
            sockets = []
            try:
                for name in names + [names[1]]:
                    ws = await websockets.connect(uri)
                    sockets.append(ws)
                    await send(ws, type='resume', token=tokens[name])
                    await receive(ws, 'resume_success')
                for game, scenario in [(game, scenario) for game in ('mahjong', 'guandan')
                                       for scenario in ('playing', 'paused', 'claim', 'settled')]:
                    await asyncio.sleep(3.1)
                    await send(sockets[0], type='create_room', game=game, name='离桌回归', buy_in=100, blind=1)
                    room_id = (await receive(sockets[0], 'game_joined'))['room']['room_id']
                    for ws in sockets[1:4]:
                        await send(ws, type='join_room', room_id=room_id)
                        await receive(ws, 'game_joined')
                    await send(sockets[0], type='start_game')
                    await receive(sockets[0], 'game_update', lambda m: m.get('to_act'))
                    room = server.game_rooms[room_id]
                    if scenario == 'paused':
                        room.pause()
                    elif scenario == 'claim':
                        room.game['to_act'] = names[1]
                        if game == 'mahjong':
                            room.game['phase'] = 'claim'
                            room.game['claim'] = {'mode':'discard', 'by':names[0], 'tile':4,
                                'options':{names[1]:{'peng':True}}, 'claims':{}, 'passed':set()}
                    elif scenario == 'settled':
                        if game == 'mahjong':
                            await room._end_hand_aborted()
                        else:
                            await room.end_hand(True)
                    await send(sockets[1], type='leave_room')
                    await asyncio.gather(receive(sockets[1], 'room_closed'), receive(sockets[4], 'room_closed'))
                    views = await asyncio.gather(*(receive(sockets[i], 'game_update', lambda m: bool(m.get('result')) and len(m['players']) == 3) for i in (0, 2, 3)))
                    for view in views:
                        assert view['result']['aborted'], view
                        assert view['settlement']['can_next'] is False
                        assert view['to_act'] is None
                        assert not view['paused']
                        assert len(view['players']) == 3
                    await send(sockets[0], type='settle_vote', choice='dissolve')
                    await send(sockets[2], type='settle_vote', choice='dissolve')
                    await asyncio.gather(*(receive(sockets[i], 'room_closed') for i in (0, 2, 3)))
                    assert room_id not in server.game_rooms
                    with server.database() as conn:
                        assert dict(conn.execute('SELECT username,coins FROM users')) == {name:1000 for name in names}
                        assert conn.execute('SELECT count(*) FROM game_escrows').fetchone()[0] == 0
                    print('PASS', game, scenario, 'non-owner leave, remaining players settle and dissolve, coins refunded exactly once')
                    await asyncio.sleep(.55)
            finally:
                for room in list(server.game_rooms.values()):
                    room.close()
                await asyncio.gather(*(ws.close() for ws in sockets))
                for timer in server.room_leave_timers.values():
                    timer.cancel()
                server.room_leave_timers.clear()


if __name__ == '__main__':
    asyncio.run(main())
