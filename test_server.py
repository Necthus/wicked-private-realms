import concurrent.futures
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from server import ApiError, Store, make_server


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.store = Store(Path(self.tmp.name) / 'test.db', 'a' * 32, lambda: self.now)
        self.accounts = [self.store.enroll('Player ' + str(i), 'a' * 32) for i in range(5)]
        self.a, self.b = [v['account_id'] for v in self.accounts[:2]]
        self.realm = self.store.create_realm(self.a, 'Shared world', '29466')['realm_id']

    def tearDown(self):
        self.tmp.cleanup()

    def rejects(self, status, func, *args):
        with self.assertRaises(ApiError) as cm:
            func(*args)
        self.assertEqual(status, cm.exception.status)

    def test_enrollment_and_auth(self):
        self.assertEqual(self.a, self.store.authenticate(self.accounts[0]['token']))
        self.rejects(403, self.store.enroll, 'No', 'bad')
        self.rejects(403, self.store.enroll, 'No', '\u975eASCII\u53e3\u4ee4')
        self.rejects(401, self.store.authenticate, 'invalid')

    def test_all_enrolled_players_can_join_without_creator(self):
        result = self.store.join(self.b, self.realm, '29466')
        self.assertTrue(result['is_host'])
        self.assertFalse(result['game_transport_ready'])

    def test_build_mismatch(self):
        self.rejects(409, self.store.join, self.a, self.realm, 'different')

    def test_four_player_limit_and_rejoin(self):
        for a in self.accounts[:4]:
            self.store.join(a['account_id'], self.realm, '29466')
        self.store.join(self.a, self.realm, '29466')
        self.rejects(409, self.store.join, self.accounts[4]['account_id'], self.realm, '29466')

    def test_concurrent_host_election(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda a: self.store.join(a['account_id'], self.realm, '29466'), self.accounts[:4]))
        self.assertEqual(1, sum(r['is_host'] for r in results))
        self.assertEqual(1, len({r['host'] for r in results}))

    def test_expired_host_cannot_overwrite_new_host(self):
        old = self.store.join(self.a, self.realm, '29466')
        self.now += 46
        new = self.store.join(self.b, self.realm, '29466')
        self.assertGreater(new['epoch'], old['epoch'])
        self.rejects(409, self.store.save, self.a, self.realm, old['epoch'], 0, b'stale')
        self.store.save(self.b, self.realm, new['epoch'], 0, b'new')

    def test_snapshot_revisions_hash_and_restart(self):
        lease = self.store.join(self.a, self.realm, '29466')
        first = self.store.save(self.a, self.realm, lease['epoch'], 0, b'first')
        self.assertEqual(hashlib.sha256(b'first').hexdigest(), first['sha256'])
        self.store.save(self.a, self.realm, lease['epoch'], 1, b'second')
        other = Store(self.store.path, 'a' * 32, lambda: self.now)
        self.assertEqual(b'first', other.load(self.realm, 1)['data'])
        self.assertEqual(b'second', other.load(self.realm)['data'])

    def test_concurrent_save_only_one_wins(self):
        lease = self.store.join(self.a, self.realm, '29466')
        def save(_):
            try:
                self.store.save(self.a, self.realm, lease['epoch'], 0, b'data')
                return True
            except ApiError as exc:
                self.assertEqual(409, exc.status)
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(1, sum(pool.map(save, range(2))))

    def test_nonhost_save_denied(self):
        lease = self.store.join(self.a, self.realm, '29466')
        self.store.join(self.b, self.realm, '29466')
        self.rejects(409, self.store.save, self.b, self.realm, lease['epoch'], 0, b'no')

    def test_heartbeat_and_leave(self):
        lease = self.store.join(self.a, self.realm, '29466')
        self.now += 30
        updated = self.store.heartbeat(self.a, self.realm, lease['epoch'])
        self.assertGreater(updated['lease_expires'], lease['lease_expires'])
        self.store.leave(self.a, self.realm)
        self.assertTrue(self.store.join(self.b, self.realm, '29466')['is_host'])
        self.rejects(409, self.store.heartbeat, self.a, self.realm, lease['epoch'])

    def test_http_auth_binary_and_readiness(self):
        server = make_server(self.store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with urllib.request.urlopen(base + '/health') as response:
                self.assertFalse(json.load(response)['game_adapter_ready'])
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + '/v1/realms')
            self.assertEqual(401, cm.exception.code)
            auth = {'Authorization': 'Bearer ' + self.accounts[0]['token']}
            req = urllib.request.Request(base + '/v1/realms/' + self.realm + '/join',
                                         json.dumps({'build': '29466'}).encode(), auth)
            with urllib.request.urlopen(req) as response:
                lease = json.load(response)
            url = base + '/v1/realms/' + self.realm + '/snapshots'
            headers = dict(auth, **{'X-Host-Epoch': str(lease['epoch']), 'If-Match': '0'})
            with urllib.request.urlopen(urllib.request.Request(url, b'\x00\xffsave', headers, method='PUT')) as response:
                self.assertEqual(201, response.status)
            with urllib.request.urlopen(urllib.request.Request(url, headers=auth)) as response:
                self.assertEqual(b'\x00\xffsave', response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)
