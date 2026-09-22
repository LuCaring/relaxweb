"""Purchase boundaries and collection unlock rules, using isolated databases."""
import sqlite3
import unittest
from estate import init_estate, estate_state, set_skin, buy_skin, EstateError
from estate.catalog import SKINS, FISHING_TREASURES, collectible_item
from estate.store import change_inventory

NOW = 2000000000
NORMAL = [key for key, value in SKINS.items() if value['unlock'] == 'purchase']

def coins(conn, username, delta, kind, detail='', ref=''):
    result = conn.execute('UPDATE users SET coins=coins+? WHERE username=? AND coins+?>=0', (delta, username, delta))
    if not result.rowcount:
        raise ValueError('金币不足')
    conn.execute('INSERT INTO ledger VALUES (?,?,?)', (username, delta, ref))

class SkinPurchaseTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.execute('CREATE TABLE users(username TEXT PRIMARY KEY COLLATE NOCASE, coins REAL)')
        self.c.executemany('INSERT INTO users VALUES (?,?)', [('alice', 200000), ('bob', 0)])
        self.c.execute('CREATE TABLE ledger(username TEXT, amount REAL, ref TEXT)')
        init_estate(self.c)
        self.c.commit()
    def tearDown(self): self.c.close()
    def state(self, user='alice'):
        with self.c: return estate_state(self.c, user, NOW)
    def buy(self, skin, request=None, user='alice'):
        with self.c: return buy_skin(self.c, user, request or 'purchase-'+skin, skin, NOW, coins)
    def switch(self, skin, request='switch-test', user='alice'):
        with self.c: return set_skin(self.c, user, request, skin, NOW)
    def collect(self, keys=None):
        with self.c:
            for key in FISHING_TREASURES if keys is None else keys:
                change_inventory(self.c, 'alice', collectible_item(key), 1)
    def test_default_prices_and_zero_balance_rejection(self):
        self.assertEqual(set(SKINS), {'berry', 'steve', 'dva', 'little_gwen', 'jamie', 'xiaofei', 'weichong', 'ryu', 'malphite', 'nailong', 'collection_reward'})
        self.assertEqual(self.state()['skins']['owned'], ['berry'])
        self.assertEqual(self.state()['profile']['skin_id'], 'berry')
        for skin in NORMAL:
            self.assertEqual(SKINS[skin]['price'], 5000)
            with self.assertRaises(EstateError): self.buy(skin, user='bob')
            with self.assertRaises(EstateError): self.switch(skin, user='bob')
        with self.assertRaises(EstateError): self.switch('collection_reward', user='bob')
        self.assertEqual(self.state('bob')['coins'], 0)
    def test_exact_balance_and_just_short(self):
        for skin in NORMAL:
            with self.subTest(skin=skin):
                price = SKINS[skin]['price']
                with self.c: self.c.execute("UPDATE users SET coins=? WHERE username='alice'", (price - 1,))
                with self.assertRaises(EstateError): self.buy(skin)
                self.assertNotIn(skin, self.state()['skins']['owned'])
                with self.c: self.c.execute("UPDATE users SET coins=? WHERE username='alice'", (price,))
                self.buy(skin)
                self.assertEqual(self.state()['coins'], 0)
                self.assertEqual(self.state()['profile']['skin_id'], 'berry')
    def test_replay_repurchase_and_switch_do_not_double_charge(self):
        self.buy('steve')
        self.switch('berry')
        self.assertTrue(self.buy('steve')['replayed'])
        self.assertEqual(self.state()['profile']['skin_id'], 'berry')
        self.assertEqual(self.buy('steve', 'buy-again')['charged'], 0)
        self.switch('steve', 'switch-again')
        self.assertEqual(self.state()['coins'], 200000 - SKINS['steve']['price'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ledger').fetchone()[0], 1)
        self.assertNotIn('steve', self.state('bob')['skins']['owned'])
        init_estate(self.c)
        self.assertIn('steve', self.state()['skins']['owned'])
    def test_every_missing_skin_blocks_special(self):
        self.collect()
        for missing in NORMAL:
            with self.c:
                self.c.execute('DELETE FROM estate_owned_skins')
                self.c.executemany('INSERT INTO estate_owned_skins VALUES (?,?)', [('alice', k) for k in NORMAL if k != missing])
            with self.assertRaises(EstateError): self.switch('collection_reward')
    def test_every_missing_collectible_blocks_special(self):
        for skin in NORMAL: self.buy(skin)
        for missing in FISHING_TREASURES:
            with self.c:
                self.c.execute('DELETE FROM estate_inventory')
                self.c.execute('DELETE FROM estate_collections')
            self.collect([k for k in FISHING_TREASURES if k != missing])
            with self.assertRaises(EstateError): self.switch('collection_reward')
    def test_unlock_after_last_collection_is_permanent_and_free(self):
        for skin in NORMAL: self.buy(skin)
        self.collect()
        self.assertIn('collection_reward', self.state()['skins']['owned'])
        before = self.state()['coins']
        self.switch('collection_reward')
        with self.c:
            for key in FISHING_TREASURES: change_inventory(self.c, 'alice', collectible_item(key), -1)
        self.assertEqual(self.state()['coins'], before)
        self.assertEqual(self.state()['profile']['skin_id'], 'collection_reward')
    def test_unlock_after_last_purchase_and_old_inventory_backfill(self):
        with self.c:
            self.c.executemany('INSERT INTO estate_inventory VALUES (?,?,1)', [('alice', collectible_item(k)) for k in FISHING_TREASURES])
        for skin in NORMAL: self.buy(skin)
        self.assertIn('collection_reward', self.state()['skins']['owned'])
        self.assertEqual(self.state()['coins'], 200000 - sum(SKINS[skin]['price'] for skin in NORMAL))
    def test_special_not_for_sale_and_request_conflict(self):
        for skin in ['berry', 'collection_reward', 'nature', 'rose_mage', None, [], 'missing']:
            with self.assertRaises(EstateError): self.buy(skin, 'invalid-buy')
        self.buy('steve', 'same-request')
        with self.assertRaises(EstateError): self.buy('dva', 'same-request')
        self.assertEqual(self.state()['coins'], 200000 - SKINS['steve']['price'])
    def test_old_free_skin_resets_without_losing_progress(self):
        self.state()
        with self.c: self.c.execute("UPDATE estate_profiles SET skin_id='collection_reward',xp=42 WHERE username='alice'")
        state = self.state()
        self.assertEqual(state['profile']['skin_id'], 'berry')
        self.assertEqual(state['profile']['xp'], 42)
    def test_ledger_failure_rolls_back_purchase(self):
        self.state()
        def fail(*args, **kwargs):
            coins(*args, **kwargs)
            raise RuntimeError('ledger failure')
        with self.assertRaises(RuntimeError), self.c:
            buy_skin(self.c, 'alice', 'failed-purchase', 'steve', NOW, fail)
        self.assertEqual(self.state()['coins'], 200000)
        self.assertNotIn('steve', self.state()['skins']['owned'])

if __name__ == '__main__': unittest.main()
