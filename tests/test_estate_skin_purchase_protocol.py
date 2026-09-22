"""Real WebSocket purchase authorization, concurrency, ledger, unlock and reconnect."""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch
from test_estate_protocol import server, send, receive
import server.database as storage
from server.schema import init_db
from estate.catalog import SKINS, FISHING_TREASURES, collectible_item
import websockets

async def main():
    normal = {key: skin for key, skin in SKINS.items() if skin['unlock'] == 'purchase'}
    total_price = sum(skin['price'] for skin in normal.values())
    steve_price = normal['steve']['price']
    with tempfile.TemporaryDirectory() as tmp, patch.object(storage, 'DB_FILE', str(Path(tmp)/'skins.db')):
        init_db(server.database)
        with server.database() as conn, conn:
            conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) VALUES ('alice','','',0,?)", (total_price,))
        token = server.accounts.create_session('alice')
        async with websockets.serve(server.handler, '127.0.0.1', 0) as host:
            uri = f'ws://127.0.0.1:{host.sockets[0].getsockname()[1]}'
            async with websockets.connect(uri) as a, websockets.connect(uri) as b:
                await send(a, type='estate_buy_skin', request_id='unauthorized-buy', skin_id='steve')
                assert (await receive(a, 'estate_error'))['code'] == 'auth_required'
                for ws in (a,b):
                    await send(ws, type='resume', token=token)
                    await receive(ws, 'resume_success')
                await send(a, type='estate_set_skin', request_id='locked-skin-test', skin_id='steve')
                assert (await receive(a, 'estate_error'))['code'] == 'skin_locked'
                # Different concurrent request IDs for the same skin still charge once.
                await asyncio.gather(*(send(ws, type='estate_buy_skin', request_id=f'purchase-steve-{i}', skin_id='steve', price=1) for i,ws in enumerate((a,b))))
                charged = []
                for _ in range(2):
                    sa,sb = await asyncio.gather(receive(a,'estate_state'), receive(b,'estate_state'))
                    assert sa == sb
                    charged.append(sa['result']['charged'])
                    assert sa['coins'] == total_price - steve_price
                assert sorted(charged) == [0,steve_price]
                await send(a, type='estate_buy_skin', request_id='special-buy-test', skin_id='collection_reward')
                assert (await receive(a,'estate_error'))['code'] == 'skin_not_for_sale'
                for skin, metadata in SKINS.items():
                    if metadata['unlock'] != 'purchase' or skin == 'steve': continue
                    await send(a,type='estate_buy_skin',request_id='purchase-'+skin,skin_id=skin)
                    await asyncio.gather(receive(a,'estate_state'),receive(b,'estate_state'))
                await send(a,type='estate_set_skin',request_id='still-locked-test',skin_id='collection_reward')
                assert (await receive(a,'estate_error'))['code'] == 'skin_locked'
                with server.database() as conn, conn:
                    conn.executemany('INSERT INTO estate_inventory VALUES (?,?,1)', [('alice',collectible_item(k)) for k in FISHING_TREASURES])
                await send(a,type='estate_set_skin',request_id='special-equip-test',skin_id='collection_reward')
                sa,sb = await asyncio.gather(receive(a,'estate_state'),receive(b,'estate_state'))
                assert sa == sb and sa['coins'] == 0 and sa['profile']['skin_id'] == 'collection_reward'
                with server.database() as conn:
                    assert conn.execute("SELECT COUNT(*), SUM(amount) FROM coin_transactions WHERE kind='estate_purchase'").fetchone() == (len(normal),-total_price)
            init_db(server.database)
            async with websockets.connect(uri) as c:
                await send(c,type='resume',token=token)
                await receive(c,'resume_success')
                await send(c,type='get_estate')
                saved = await receive(c,'estate_state')
                assert saved['profile']['skin_id'] == 'collection_reward' and set(saved['skins']['owned']) == set(SKINS)
                await send(c,type='estate_buy_skin',request_id='purchase-steve-0',skin_id='steve')
                replay = await receive(c,'estate_state')
                assert replay['result']['replayed'] and replay['coins'] == 0 and replay['profile']['skin_id'] == 'collection_reward'
    print('PASS skin purchase WebSocket: auth, concurrent duplicate purchase, authoritative price, ledger, collection gates, persistence and replay')

if __name__ == '__main__': asyncio.run(main())
