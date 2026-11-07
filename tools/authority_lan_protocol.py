"""Small bounded TCP protocol used by the two-machine development trial.

The protocol carries control JSON, 47-byte input records, reliable commands and
complete Quantum snapshots. It is intentionally separate from the shipping
Photon gateway until the player-facing correction path is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import select
import socket
import struct
import threading

MAX_FRAME = 64 * 1024 * 1024 + 256 * 1024
FRAME = struct.Struct('!I')
META = struct.Struct('!BI')
KIND_CONTROL = 1
KIND_STATE = 2
KIND_INPUT = 3
KIND_COMMAND = 4


def _exact(sock: socket.socket, size: int) -> bytes:
    if not 0 < size <= MAX_FRAME:
        raise ValueError('Invalid LAN frame size')
    data = bytearray()
    while len(data) < size:
        part = sock.recv(min(65536, size - len(data)))
        if not part:
            raise EOFError('LAN peer closed inside a frame')
        data.extend(part)
    return bytes(data)


def encode(kind: int, meta: dict, data: bytes = b'') -> bytes:
    if kind not in (KIND_CONTROL, KIND_STATE, KIND_INPUT, KIND_COMMAND):
        raise ValueError('Unsupported LAN frame kind')
    header = json.dumps(meta, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    body = META.pack(kind, len(header)) + header + bytes(data)
    if len(body) > MAX_FRAME:
        raise ValueError('LAN frame is too large')
    return FRAME.pack(len(body)) + body


def decode(sock: socket.socket) -> tuple[int, dict, bytes]:
    body = _exact(sock, FRAME.unpack(_exact(sock, FRAME.size))[0])
    if len(body) < META.size:
        raise ValueError('Truncated LAN frame')
    kind, header_size = META.unpack(body[:META.size])
    if kind not in (KIND_CONTROL, KIND_STATE, KIND_INPUT, KIND_COMMAND):
        raise ValueError('Unsupported LAN frame kind')
    end = META.size + header_size
    if end > len(body):
        raise ValueError('Truncated LAN frame header')
    meta = json.loads(body[META.size:end].decode('utf-8'))
    if not isinstance(meta, dict):
        raise ValueError('LAN metadata must be an object')
    return kind, meta, body[end:]


@dataclass
class LanPeer:
    """One reader and one bounded writer for a connected peer."""

    connection: socket.socket
    name: str = 'peer'

    def __post_init__(self):
        self.connection.settimeout(2.0)
        self.closed = threading.Event()
        self.error = None
        self.controls: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self.latest_state = None
        self.state_lock = threading.Lock()
        self.writer = threading.Thread(target=self._write_loop, name=self.name + '-writer', daemon=True)
        self.writer.start()

    def send_control(self, message: dict):
        frame = encode(KIND_CONTROL, message)
        self.controls.put(frame, timeout=2)

    def send_state(self, meta: dict, data: bytes):
        frame = encode(KIND_STATE, meta, data)
        with self.state_lock:
            self.latest_state = frame

    def send_input(self, meta: dict, data: bytes):
        self.controls.put(encode(KIND_INPUT, meta, data), timeout=2)

    def send_command(self, meta: dict, data: bytes):
        self.controls.put(encode(KIND_COMMAND, meta, data), timeout=2)

    def _write_loop(self):
        try:
            while not self.closed.is_set():
                try:
                    frame = self.controls.get(timeout=0.1)
                except queue.Empty:
                    with self.state_lock:
                        frame, self.latest_state = self.latest_state, None
                    if frame is None:
                        continue
                self.connection.sendall(frame)
        except OSError as error:
            if not self.closed.is_set():
                self.error = str(error)
        finally:
            self.close()

    def receive_loop(self, callback):
        try:
            while not self.closed.is_set():
                readable, _, _ = select.select([self.connection], [], [], 0.5)
                if not readable:
                    continue
                callback(*decode(self.connection))
        except EOFError:
            pass
        except Exception as error:
            # Include protocol/callback failures in the trial report. A rejected
            # packet must not silently look like a successful play session.
            if not self.closed.is_set():
                self.error = str(error)
        finally:
            self.close()

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()
