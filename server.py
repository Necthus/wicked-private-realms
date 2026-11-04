"""Private realm control/storage service. NOT a Photon/Quantum game server.

Run behind an SSH tunnel or TLS reverse proxy. All /v1 routes except enrollment
require an account token. An enrollment code grants access to the shared group.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_SNAPSHOT = 64 * 1024 * 1024
LEASE_SECONDS = 45


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class Store:
    def __init__(self, path, enrollment_code, clock=time.time):
        if len(enrollment_code) < 32:
            raise ValueError('Enrollment code must have at least 32 characters')
        self.path, self.code, self.clock = str(path), enrollment_code, clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS realms (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    creator TEXT NOT NULL REFERENCES accounts(id),
                    build TEXT NOT NULL, created REAL NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    epoch INTEGER NOT NULL DEFAULT 0,
                    host TEXT, host_expires REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS presence (
                    realm TEXT NOT NULL REFERENCES realms(id),
                    account TEXT NOT NULL REFERENCES accounts(id),
                    expires REAL NOT NULL, PRIMARY KEY(realm, account));
                CREATE TABLE IF NOT EXISTS snapshots (
                    realm TEXT NOT NULL REFERENCES realms(id),
                    revision INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    created REAL NOT NULL, writer TEXT NOT NULL,
                    data BLOB NOT NULL, PRIMARY KEY(realm, revision));
            ''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enroll(self, name, code):
        if not isinstance(code, str) or not hmac.compare_digest(code.encode('utf-8'), self.code.encode('utf-8')):
            raise ApiError(403, 'Invalid enrollment code')
        name = self.name(name)
        account, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute('INSERT INTO accounts VALUES(?,?,?,?)',
                       (account, name, digest(token), self.clock()))
        return {'account_id': account, 'name': name, 'token': token}

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise ApiError(400, 'Name must contain 1-80 characters')
        return value.strip()

    def authenticate(self, token):
        if not token:
            raise ApiError(401, 'Bearer token required')
        with self.transaction() as db:
            row = db.execute('SELECT id FROM accounts WHERE token_hash=?', (digest(token),)).fetchone()
        if row is None:
            raise ApiError(401, 'Invalid account token')
        return row['id']

    @staticmethod
    def realm(db, realm_id):
        row = db.execute('SELECT * FROM realms WHERE id=?', (realm_id,)).fetchone()
        if row is None:
            raise ApiError(404, 'Realm not found')
        return row

    def create_realm(self, account, name, build):
        name = self.name(name)
        if not isinstance(build, str) or not build or len(build) > 64:
            raise ApiError(400, 'Build identifier required')
        realm_id = str(uuid.uuid4())
        with self.transaction() as db:
            db.execute('INSERT INTO realms(id,name,creator,build,created) VALUES(?,?,?,?,?)',
                       (realm_id, name, account, build, self.clock()))
        return {'realm_id': realm_id, 'revision': 0, 'build': build}

    def list_realms(self):
        now = self.clock()
        with self.transaction() as db:
            rows = db.execute('''SELECT r.id AS realm_id,r.name,r.build,r.revision,r.epoch,
                CASE WHEN r.host_expires>? THEN r.host ELSE NULL END AS host,
                (SELECT count(*) FROM presence p WHERE p.realm=r.id AND p.expires>?) AS players
                FROM realms r ORDER BY r.created''', (now, now)).fetchall()
        return [dict(r) for r in rows]

    def join(self, account, realm_id, build):
        now = self.clock()
        with self.transaction() as db:
            r = self.realm(db, realm_id)
            if build != r['build']:
                raise ApiError(409, 'Game build mismatch')
            db.execute('DELETE FROM presence WHERE expires<=?', (now,))
            existing = db.execute('SELECT 1 FROM presence WHERE realm=? AND account=?',
                                  (realm_id, account)).fetchone()
            count = db.execute('SELECT count(*) FROM presence WHERE realm=?', (realm_id,)).fetchone()[0]
            if not existing and count >= 4:
                raise ApiError(409, 'Realm already has four active players')
            db.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',
                       (realm_id, account, now + LEASE_SECONDS))
            if r['host'] is None or r['host_expires'] <= now:
                db.execute('UPDATE realms SET host=?,host_expires=?,epoch=epoch+1 WHERE id=?',
                           (account, now + LEASE_SECONDS, realm_id))
            return self.lease_response(self.realm(db, realm_id), account)

    @staticmethod
    def lease_response(r, account):
        return {'realm_id': r['id'], 'host': r['host'], 'is_host': r['host'] == account,
                'epoch': r['epoch'], 'revision': r['revision'],
                'lease_expires': r['host_expires'], 'heartbeat_interval_seconds': 10,
                'game_transport_ready': False}

    def heartbeat(self, account, realm_id, epoch):
        now = self.clock()
        with self.transaction() as db:
            r = self.realm(db, realm_id)
            p = db.execute('SELECT expires FROM presence WHERE realm=? AND account=?',
                           (realm_id, account)).fetchone()
            if p is None or p['expires'] <= now:
                raise ApiError(409, 'Presence expired; join again')
            if epoch != r['epoch']:
                raise ApiError(409, 'Host epoch changed; join again')
            if r['host'] == account:
                if r['host_expires'] <= now:
                    raise ApiError(409, 'Host lease expired; join again')
                db.execute('UPDATE realms SET host_expires=? WHERE id=?', (now + LEASE_SECONDS, realm_id))
            db.execute('UPDATE presence SET expires=? WHERE realm=? AND account=?',
                       (now + LEASE_SECONDS, realm_id, account))
            return self.lease_response(self.realm(db, realm_id), account)

    def leave(self, account, realm_id):
        with self.transaction() as db:
            self.realm(db, realm_id)
            db.execute('DELETE FROM presence WHERE realm=? AND account=?', (realm_id, account))
            db.execute('UPDATE realms SET host=NULL,host_expires=0 WHERE id=? AND host=?',
                       (realm_id, account))
        return {'left': True}

    def save(self, account, realm_id, epoch, expected_revision, data):
        if not data or len(data) > MAX_SNAPSHOT:
            raise ApiError(413, 'Snapshot must contain 1 byte to 64 MiB')
        now, checksum = self.clock(), hashlib.sha256(data).hexdigest()
        with self.transaction() as db:
            r = self.realm(db, realm_id)
            if r['host'] != account or r['host_expires'] <= now or r['epoch'] != epoch:
                raise ApiError(409, 'A current host lease and epoch are required to save')
            if expected_revision != r['revision']:
                raise ApiError(409, 'Revision changed; refusing to overwrite another save')
            revision = r['revision'] + 1
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?)',
                       (realm_id, revision, checksum, now, account, data))
            db.execute('UPDATE realms SET revision=? WHERE id=?', (revision, realm_id))
        return {'revision': revision, 'sha256': checksum, 'size': len(data)}

    def load(self, realm_id, revision=None):
        with self.transaction() as db:
            r = self.realm(db, realm_id)
            if revision is None:
                revision = r['revision']
            row = db.execute('SELECT * FROM snapshots WHERE realm=? AND revision=?',
                             (realm_id, revision)).fetchone()
        if row is None:
            raise ApiError(404, 'Snapshot not found')
        return dict(row)


class Handler(BaseHTTPRequestHandler):
    server_version = 'PrivateRealmControl/0.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        # Never log authentication headers or request bodies.
        print('%s %s' % (self.address_string(), fmt % args), flush=True)

    def respond(self, status, value, headers=None):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/octet-stream' if isinstance(value, bytes) else 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (headers or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self, limit=16384):
        if self.headers.get('Transfer-Encoding'):
            raise ApiError(400, 'Transfer-Encoding is unsupported; send Content-Length')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ApiError(400, 'Invalid Content-Length')
        if length < 0 or length > limit:
            raise ApiError(413, 'Request too large')
        body = self.rfile.read(length)
        if len(body) != length:
            raise ApiError(400, 'Incomplete body')
        return body

    def read_json(self):
        try:
            result = json.loads(self.read_body())
        except (ValueError, UnicodeError):
            raise ApiError(400, 'Invalid JSON')
        if not isinstance(result, dict):
            raise ApiError(400, 'Expected a JSON object')
        return result

    def dispatch(self):
        store = self.server.store
        path = urlsplit(self.path).path.rstrip('/')
        if path == '/health' and self.command == 'GET':
            return self.respond(200, {'service': 'private-realm-control', 'status': 'ok',
                                     'game_transport_ready': False, 'game_adapter_ready': False})
        if path == '/v1/enroll' and self.command == 'POST':
            data = self.read_json()
            return self.respond(201, store.enroll(data.get('name'), data.get('code')))
        auth = self.headers.get('Authorization', '')
        account = store.authenticate(auth[7:] if auth.startswith('Bearer ') else '')
        if path == '/v1/realms':
            if self.command == 'GET':
                return self.respond(200, {'realms': store.list_realms()})
            if self.command == 'POST':
                data = self.read_json()
                return self.respond(201, store.create_realm(account, data.get('name'), data.get('build')))
        parts = path.split('/')
        if len(parts) not in (5, 6) or parts[1:3] != ['v1', 'realms']:
            raise ApiError(404, 'Route not found; this service does not implement MoonBackend or Photon')
        realm_id, action = parts[3:5]
        if len(parts) == 5 and self.command == 'POST':
            data = self.read_json()
            if action == 'join':
                return self.respond(200, store.join(account, realm_id, data.get('build')))
            if action == 'heartbeat':
                return self.respond(200, store.heartbeat(account, realm_id, data.get('epoch')))
            if action == 'leave':
                return self.respond(200, store.leave(account, realm_id))
        if action == 'snapshots' and self.command == 'GET':
            try:
                revision = int(parts[5]) if len(parts) == 6 else None
            except ValueError:
                raise ApiError(400, 'Revision must be an integer')
            row = store.load(realm_id, revision)
            return self.respond(200, row['data'], {'X-Revision': row['revision'], 'X-SHA256': row['sha256']})
        if action == 'snapshots' and len(parts) == 5 and self.command == 'PUT':
            try:
                epoch = int(self.headers['X-Host-Epoch'])
                revision = int(self.headers['If-Match'])
            except (ValueError, TypeError):
                raise ApiError(400, 'X-Host-Epoch and If-Match integer headers required')
            return self.respond(201, store.save(account, realm_id, epoch, revision, self.read_body(MAX_SNAPSHOT)))
        raise ApiError(404, 'Route not found')

    def handle_request(self):
        try:
            self.dispatch()
        except ApiError as exc:
            self.respond(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            import traceback
            traceback.print_exc()
            self.respond(500, {'error': 'Internal service error'})

    do_GET = do_POST = do_PUT = handle_request


def make_server(store, host='127.0.0.1', port=18766):
    server = ThreadingHTTPServer((host, port), Handler)
    server.store = store
    return server


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=18766)
    p.add_argument('--db', default='data/realms.sqlite3')
    args = p.parse_args()
    store = Store(args.db, os.environ.get('REALM_ENROLLMENT_CODE', ''))
    make_server(store, args.host, args.port).serve_forever()
