"""Experimental durable full-frame checkpoints, written only by the authority.

This is not the legacy client-consensus save protocol. A new epoch fences old
workers, and a checkpoint is readable only with the exact game build identity.
"""
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading

from authoritative_state import Snapshot

MAX_METADATA_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Epoch:
    realm: str
    generation: int
    token: str


@dataclass(frozen=True)
class Checkpoint:
    snapshot: Snapshot
    metadata: dict
    generation: int


class CheckpointStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS authority_epochs (
                    realm TEXT PRIMARY KEY, generation INTEGER NOT NULL, token TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS authority_checkpoints (
                    realm TEXT PRIMARY KEY, generation INTEGER NOT NULL, build TEXT NOT NULL,
                    tick INTEGER NOT NULL, checksum TEXT NOT NULL, digest BLOB NOT NULL,
                    data BLOB NOT NULL, metadata BLOB NOT NULL, metadata_digest BLOB NOT NULL);
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def begin_epoch(self, realm, expected_generation=0):
        if not isinstance(realm, str) or not realm or len(realm) > 128:
            raise ValueError('Invalid authority realm')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT generation FROM authority_epochs WHERE realm=?', (realm,)).fetchone()
            generation = current[0] if current else 0
            if generation != expected_generation:
                raise ValueError('Authority epoch changed; refusing a competing worker')
            epoch = Epoch(realm, generation + 1, secrets.token_hex(32))
            db.execute('INSERT OR REPLACE INTO authority_epochs VALUES (?,?,?)',
                       (realm, epoch.generation, epoch.token))
        return epoch

    def generation(self, realm):
        with self._connect() as db:
            row = db.execute('SELECT generation FROM authority_epochs WHERE realm=?', (realm,)).fetchone()
        return row[0] if row else 0

    def realms(self):
        with self._connect() as db:
            return [row[0] for row in db.execute('SELECT realm FROM authority_checkpoints ORDER BY realm')]

    @staticmethod
    def encode_metadata(metadata):
        if not isinstance(metadata, dict):
            raise ValueError('Checkpoint metadata must be an object')
        data = json.dumps(metadata, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        if len(data) > MAX_METADATA_BYTES:
            raise ValueError('Checkpoint metadata is too large')
        return data

    def save(self, epoch, build, snapshot, metadata):
        if not isinstance(snapshot, Snapshot) or not isinstance(build, str) or len(build) != 64:
            raise ValueError('Invalid checkpoint state or build identity')
        if len(bytes.fromhex(build)) != 32:
            raise ValueError('Invalid checkpoint build digest')
        encoded = self.encode_metadata(metadata)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT generation,token FROM authority_epochs WHERE realm=?', (epoch.realm,)).fetchone()
            if current != (epoch.generation, epoch.token):
                raise ValueError('Stale authority cannot write a checkpoint')
            previous = db.execute('SELECT tick,build FROM authority_checkpoints WHERE realm=?', (epoch.realm,)).fetchone()
            if previous and (snapshot.tick <= previous[0] or build != previous[1]):
                raise ValueError('Checkpoint tick did not advance or the game build changed')
            db.execute('INSERT OR REPLACE INTO authority_checkpoints VALUES (?,?,?,?,?,?,?,?,?)',
                       (epoch.realm, epoch.generation, build, snapshot.tick, str(snapshot.checksum), snapshot.digest,
                        snapshot.data, encoded, hashlib.sha256(encoded).digest()))

    def load(self, realm, build):
        with self._connect() as db:
            row = db.execute('SELECT generation,build,tick,checksum,digest,data,metadata,metadata_digest '
                             'FROM authority_checkpoints WHERE realm=?', (realm,)).fetchone()
        if row is None:
            return None
        generation, saved_build, tick, checksum, digest, data, metadata, metadata_digest = row
        if saved_build != build:
            raise ValueError('Checkpoint belongs to a different game build')
        snapshot = Snapshot(tick, int(checksum), bytes(data))
        if snapshot.digest != digest or hashlib.sha256(metadata).digest() != metadata_digest:
            raise ValueError('Checkpoint integrity verification failed')
        decoded = json.loads(metadata)
        self.encode_metadata(decoded)
        return Checkpoint(snapshot, decoded, generation)


class CheckpointWriter:
    """Keep one pending complete checkpoint and perform disk I/O off the simulation path."""
    def __init__(self, store, epoch, build):
        self.store, self.epoch, self.build = store, epoch, build
        self.condition = threading.Condition()
        self.pending = None
        self.closed = False
        self.error = None
        self.last_saved_tick = None
        self.last_offered_tick = -1
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def offer(self, snapshot, metadata):
        # Freeze the metadata together with the immutable snapshot, before enqueue.
        if not isinstance(snapshot, Snapshot):
            raise TypeError('Expected a complete snapshot')
        frozen = json.loads(self.store.encode_metadata(metadata))
        with self.condition:
            if self.closed or self.error:
                raise RuntimeError('Checkpoint writer is closed or failed')
            if snapshot.tick <= self.last_offered_tick:
                raise ValueError('Pending checkpoint moved backwards')
            self.last_offered_tick = snapshot.tick
            self.pending = snapshot, frozen
            self.condition.notify()

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.pending is not None or self.closed)
                    if self.pending is None:
                        return
                    snapshot, metadata = self.pending
                    self.pending = None
                self.store.save(self.epoch, self.build, snapshot, metadata)
                self.last_saved_tick = snapshot.tick
        except Exception as error:
            with self.condition:
                self.error = str(error)
                self.condition.notify_all()

    def close(self, timeout=5):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise TimeoutError('Checkpoint storage did not finish before shutdown')
        if self.error:
            raise RuntimeError('Checkpoint storage failed: ' + self.error)
