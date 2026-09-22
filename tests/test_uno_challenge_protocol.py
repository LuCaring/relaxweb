"""独立临时数据库及随机端口上的 UNO 真实 WebSocket 回归。"""
import asyncio
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
import time
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.accounts import hash_password
from server.app import create_app
from server.database import database
from server.schema import init_db


async def exercise(uri):
    clients = {}
    async def send(name, payload):
        await clients[name]['ws'].send(json.dumps(payload))
    async def wait_for(predicate, timeout=8):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(.01)
    async def read(name):
        async for raw in clients[name]['ws']:
            msg = json.loads(raw)
            if msg['type'] == 'login_success':
                clients[name]['login'] = True
            elif msg['type'] in ('game_update', 'game_joined'):
                clients[name]['view'] = msg.get('room', msg)
                clients[name]['revision'] += 1
    try:
        for name in ('玩家1', '玩家2', '玩家3'):
            ws = await websockets.connect(uri)
            clients[name] = {'ws': ws, 'view': {}, 'revision': 0}
            clients[name]['reader'] = asyncio.create_task(read(name))
            await send(name, {'type': 'login', 'username': name, 'password': 'test123456'})
            await wait_for(lambda: clients[name].get('login'))
        owner = '玩家1'
        await send(owner, {'type': 'create_room', 'game': 'uno', 'name': '漏喊质疑验收', 'buy_in': 100, 'blind': 1})
        await wait_for(lambda: clients[owner]['view'].get('room_id'))
        room_id = clients[owner]['view']['room_id']
        for name in ('玩家2', '玩家3'):
            await send(name, {'type': 'join_room', 'room_id': room_id})
            await wait_for(lambda: clients[name]['view'].get('room_id') == room_id)
        await send(owner, {'type': 'start_game'})
        await wait_for(lambda: clients[owner]['view'].get('to_act'))
        for _ in range(2000):
            view = clients[owner]['view']
            pending = [p['username'] for p in view['players'] if p.get('uno')]
            if pending:
                target = pending[0]
                break
            actor = view['to_act']
            await wait_for(lambda: clients[actor]['view'].get('action_event') == view.get('action_event') and clients[actor]['view'].get('to_act') == actor)
            own = clients[actor]['view']
            options = own.get('your_options', {})
            cards = own['your_hand']
            pick = next((i for i, card in enumerate(cards) if
                         (not options.get('pass') or i == own.get('your_drawn')) and
                         (card['c'] == 'w' or card['c'] == own['active']['c'] or card['v'] == own['active']['v'])), None)
            payload = {'type': 'poker_action', 'action': 'play', 'card': pick, 'color': 'r'} if pick is not None else {'type': 'poker_action', 'action': 'pass' if options.get('pass') else 'draw'}
            revision = clients[owner]['revision']
            await send(actor, payload)
            await wait_for(lambda: clients[owner]['revision'] > revision)
        else:
            raise AssertionError('No one reached one card')
        challenger = next(name for name in clients if name != target)
        start = time.monotonic()
        revision = clients[owner]['revision']
        await send(challenger, {'type': 'poker_action', 'action': 'challenge_uno', 'target': target})
        await wait_for(lambda: clients[owner]['revision'] > revision)
        assert next(p['cards'] for p in clients[owner]['view']['players'] if p['username'] == target) == 1
        await wait_for(lambda: target in clients[challenger]['view'].get('your_options', {}).get('challenge', []), timeout=4)
        elapsed = time.monotonic() - start
        assert 1.8 < elapsed < 3.5, elapsed
        for _ in range(2):
            await send(challenger, {'type': 'poker_action', 'action': 'challenge_uno', 'target': target})
        await wait_for(lambda: next(p['cards'] for p in clients[owner]['view']['players'] if p['username'] == target) == 3)
        await asyncio.sleep(.1)
        assert next(p['cards'] for p in clients[owner]['view']['players'] if p['username'] == target) == 3
        print(f'PASS real WebSocket: early rejection, automatic challenge availability after {elapsed:.2f}s, exactly two penalty cards despite duplicate requests')
    finally:
        if '玩家1' in clients:
            await send('玩家1', {'type': 'leave_room'})
            await asyncio.sleep(.1)
        for client in clients.values():
            await client['ws'].close()
            await client['reader']


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(partial(database, str(Path(tmp) / 'uno.db')))
        init_db(app.database)
        password, salt = hash_password('test123456')
        with app.database() as conn, conn:
            conn.executemany(
                'INSERT INTO users(username,password_hash,salt,created_at,coins) VALUES (?,?,?,0,1000)',
                [(name, password, salt) for name in ('玩家1', '玩家2', '玩家3')],
            )
        try:
            async with websockets.serve(app.handler, '127.0.0.1', 0) as host:
                await exercise(f'ws://127.0.0.1:{host.sockets[0].getsockname()[1]}/?client=game')
        finally:
            await app.aclose()


if __name__ == '__main__':
    asyncio.run(main())
