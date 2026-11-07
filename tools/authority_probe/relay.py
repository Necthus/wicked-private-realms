"""Loopback-only fault injection for real game snapshot streams."""
import socket
import threading

from authoritative_state import Snapshot, SnapshotHub, receive_snapshot, send_snapshots


def loopback_pair():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        client = socket.create_connection(listener.getsockname(), timeout=5)
        server, _ = listener.accept()
    client.settimeout(None)
    return server, client


class LoopbackRelay:
    def __init__(self, script, fault_start=600, fault_end=780):
        self.script = script
        self.fault_start = fault_start
        self.fault_end = fault_end
        self.hub = SnapshotHub()
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.peers = []
        self.errors = []
        for index in (1, 2):
            writer, reader = loopback_pair()
            mailbox = self.hub.subscribe()
            stats = {'replica': index, 'received': 0, 'forwarded': 0, 'dropped': 0, 'last_tick': -1}
            peer = {'writer': writer, 'reader': reader, 'mailbox': mailbox, 'stats': stats}
            sender = threading.Thread(target=self._send, args=(peer,), daemon=True)
            receiver = threading.Thread(target=self._receive, args=(peer,), daemon=True)
            peer['threads'] = (sender, receiver)
            self.peers.append(peer)
            sender.start()
            receiver.start()

    def _send(self, peer):
        try:
            send_snapshots(peer['writer'], peer['mailbox'])
        finally:
            self.hub.unsubscribe(peer['mailbox'])

    def _receive(self, peer):
        try:
            while not self.stopped.is_set():
                snapshot = receive_snapshot(peer['reader'])
                with self.lock:
                    stats = peer['stats']
                    if snapshot.tick <= stats['last_tick']:
                        raise ValueError('Snapshot stream moved backwards')
                    stats['last_tick'] = snapshot.tick
                    stats['received'] += 1
                    drop = stats['replica'] == 1 and self.fault_start <= snapshot.tick < self.fault_end
                    if drop:
                        stats['dropped'] += 1
                    else:
                        stats['forwarded'] += 1
                if not drop:
                    self.script.post({'type': 'authoritative-state', 'payload': {
                        'replica': stats['replica'], 'tick': snapshot.tick,
                        'checksum': format(snapshot.checksum, 'x'),
                    }}, snapshot.data)
        except Exception as error:
            if not self.stopped.is_set():
                with self.lock:
                    self.errors.append(str(error))

    def publish(self, tick, checksum, data):
        self.hub.publish(Snapshot(tick, int(checksum, 16), bytes(data)))

    def close(self):
        self.stopped.set()
        for peer in self.peers:
            self.hub.unsubscribe(peer['mailbox'])
            for connection in (peer['reader'], peer['writer']):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        for peer in self.peers:
            for thread in peer['threads']:
                thread.join(timeout=3)
        with self.lock:
            return {'peers': [dict(peer['stats']) for peer in self.peers], 'errors': list(self.errors)}
