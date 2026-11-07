"""Experimental full-state transport primitives for a trusted simulation worker.

Only complete, independently recoverable snapshots belong in these mailboxes.
Inputs, transactions and one-shot events need their own reliable handling.
Authentication and TLS are the enclosing transport's responsibility; this module
opens no listeners and is not enabled by the shipping server entry point.
"""
from dataclasses import dataclass, field
import hashlib
import math
import socket
import struct
import threading

MAX_STATE_BYTES = 64 * 1024 * 1024
HEADER = struct.Struct('!4sBIQI32s')
MAGIC = b'WRAS'
VERSION = 1


@dataclass(frozen=True)
class Snapshot:
    tick: int
    checksum: int
    data: bytes
    digest: bytes = field(init=False, repr=False)

    def __post_init__(self):
        if type(self.tick) is not int or not 0 <= self.tick < 2**31:
            raise ValueError('Invalid simulation tick')
        if type(self.checksum) is not int or not 0 <= self.checksum < 2**64:
            raise ValueError('Invalid simulation checksum')
        if not isinstance(self.data, bytes) or not 0 < len(self.data) <= MAX_STATE_BYTES:
            raise ValueError('Invalid full snapshot payload')
        object.__setattr__(self, 'digest', hashlib.sha256(self.data).digest())

    def encode(self):
        return HEADER.pack(MAGIC, VERSION, self.tick, self.checksum, len(self.data), self.digest) + self.data


def _receive_exact(connection, length):
    result = bytearray()
    while len(result) < length:
        block = connection.recv(min(length - len(result), 65536))
        if not block:
            raise EOFError('Snapshot stream ended inside a packet')
        result.extend(block)
    return bytes(result)


def receive_snapshot(connection):
    magic, version, tick, checksum, length, digest = HEADER.unpack(_receive_exact(connection, HEADER.size))
    if magic != MAGIC or version != VERSION or tick >= 2**31 or not 0 < length <= MAX_STATE_BYTES:
        raise ValueError('Unsupported or invalid snapshot header')
    snapshot = Snapshot(tick, checksum, _receive_exact(connection, length))
    if snapshot.digest != digest:
        raise ValueError('Snapshot payload digest differs from header')
    return snapshot


class SnapshotMailbox:
    """One pending full snapshot; a slow receiver never grows an unbounded queue."""

    def __init__(self):
        self._condition = threading.Condition()
        self._latest = None
        self._closed = False
        self.replaced = 0

    def offer(self, snapshot):
        if not isinstance(snapshot, Snapshot):
            raise TypeError('Expected a complete simulation snapshot')
        with self._condition:
            if self._closed:
                return False
            if self._latest is not None:
                self.replaced += 1
            self._latest = snapshot
            self._condition.notify()
            return True

    def take(self, timeout=None):
        with self._condition:
            if not self._condition.wait_for(lambda: self._latest is not None or self._closed, timeout):
                return None
            snapshot, self._latest = self._latest, None
            return snapshot

    def close(self):
        with self._condition:
            self._closed = True
            self._latest = None
            self._condition.notify_all()

    @property
    def closed(self):
        with self._condition:
            return self._closed


class SnapshotHub:
    """Publish trusted worker states without ever doing socket I/O in the room lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest = None
        self._receivers = set()

    def subscribe(self):
        mailbox = SnapshotMailbox()
        with self._lock:
            self._receivers.add(mailbox)
            if self._latest is not None:
                mailbox.offer(self._latest)
        return mailbox

    def unsubscribe(self, mailbox):
        with self._lock:
            self._receivers.discard(mailbox)
        mailbox.close()

    def publish(self, snapshot):
        if not isinstance(snapshot, Snapshot):
            raise TypeError('Expected a complete simulation snapshot')
        with self._lock:
            if self._latest is not None and snapshot.tick <= self._latest.tick:
                raise ValueError('Authority snapshots must advance monotonically')
            self._latest = snapshot
            for receiver in self._receivers:
                receiver.offer(snapshot)


def send_snapshots(connection, mailbox, write_timeout=2.0):
    """Run in a receiver's own thread; a partial-write timeout closes its stream."""
    if not math.isfinite(write_timeout) or write_timeout <= 0:
        raise ValueError('Snapshot write timeout must be finite and positive')
    try:
        connection.settimeout(write_timeout)
        while not mailbox.closed:
            snapshot = mailbox.take(timeout=0.25)
            if snapshot is not None:
                connection.sendall(snapshot.encode())
    except (OSError, TimeoutError):
        pass
    finally:
        mailbox.close()
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()
