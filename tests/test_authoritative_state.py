import socket
import threading
import unittest

from authoritative_state import HEADER, MAGIC, MAX_STATE_BYTES, VERSION, Snapshot, SnapshotHub, receive_snapshot, send_snapshots


class FullStateTransportTests(unittest.TestCase):
    def test_snapshot_binary_roundtrip(self):
        writer, reader = socket.socketpair()
        with writer, reader:
            original = Snapshot(123, 2**63 + 17, b'complete-world-state')
            writer.sendall(original.encode())
            self.assertEqual(original, receive_snapshot(reader))

    def test_oversized_header_rejected_without_reading_payload(self):
        writer, reader = socket.socketpair()
        with writer, reader:
            reader.settimeout(1)
            writer.sendall(HEADER.pack(MAGIC, VERSION, 1, 0, MAX_STATE_BYTES + 1, bytes(32)))
            with self.assertRaises(ValueError):
                receive_snapshot(reader)

    def test_corrupt_or_truncated_payload_rejected(self):
        for packet, expected in ((Snapshot(1, 0, b'world').encode()[:-1], EOFError),
                                 (Snapshot(1, 0, b'world').encode()[:-1] + b'X', ValueError)):
            with self.subTest(expected=expected):
                writer, reader = socket.socketpair()
                with writer, reader:
                    writer.sendall(packet)
                    writer.shutdown(socket.SHUT_WR)
                    with self.assertRaises(expected):
                        receive_snapshot(reader)

    def test_late_receiver_gets_latest_complete_state_and_rejects_old_authority(self):
        hub = SnapshotHub()
        hub.publish(Snapshot(1, 0, b'first'))
        hub.publish(Snapshot(2, 0, b'second'))
        mailbox = hub.subscribe()
        try:
            self.assertEqual(2, mailbox.take(timeout=0.1).tick)
            with self.assertRaises(ValueError):
                hub.publish(Snapshot(1, 0, b'old'))
        finally:
            hub.unsubscribe(mailbox)

    def test_slow_receiver_does_not_block_authority_or_healthy_receiver(self):
        hub = SnapshotHub()
        slow = hub.subscribe()
        healthy = hub.subscribe()
        slow_writer, slow_reader = socket.socketpair()
        healthy_writer, healthy_reader = socket.socketpair()
        slow_writer.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        slow_reader.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        write_started = threading.Event()
        write_finished = threading.Event()

        class ObservedSocket:
            def __getattr__(self, name):
                return getattr(slow_writer, name)

            def sendall(self, data):
                write_finished.clear()
                write_started.set()
                slow_writer.sendall(data)
                write_finished.set()

        slow_thread = threading.Thread(target=send_snapshots, args=(ObservedSocket(), slow, 5))
        healthy_thread = threading.Thread(target=send_snapshots, args=(healthy_writer, healthy, 2))
        latest = []
        received = threading.Event()
        published = threading.Event()

        def read_healthy():
            try:
                healthy_reader.settimeout(3)
                while True:
                    snapshot = receive_snapshot(healthy_reader)
                    latest.append(snapshot.tick)
                    if snapshot.tick == 20:
                        received.set()
                        return
            except (EOFError, OSError):
                return

        def publish():
            payload = b'x' * (2 * 1024 * 1024)
            for tick in range(1, 21):
                hub.publish(Snapshot(tick, 0, payload))
            published.set()

        reader_thread = threading.Thread(target=read_healthy)
        publisher = threading.Thread(target=publish)
        try:
            slow_thread.start()
            healthy_thread.start()
            reader_thread.start()
            publisher.start()
            self.assertTrue(published.wait(3), 'Authority blocked while publishing to a non-reading client')
            self.assertTrue(write_started.wait(1), 'Slow stream never attempted a socket write')
            self.assertTrue(received.wait(5), 'Healthy client did not receive the latest state')
            self.assertFalse(write_finished.is_set(), 'Slow stream did not exercise a blocked write')
            self.assertGreater(slow.replaced, 0)
            self.assertEqual(20, latest[-1])
        finally:
            hub.unsubscribe(slow)
            hub.unsubscribe(healthy)
            slow_reader.close()
            for thread in (publisher, slow_thread, healthy_thread, reader_thread):
                if thread.ident is not None:
                    thread.join(timeout=4)
            healthy_reader.close()

    def test_shutdown_wakes_waiting_sender(self):
        hub = SnapshotHub()
        mailbox = hub.subscribe()
        writer, reader = socket.socketpair()
        thread = threading.Thread(target=send_snapshots, args=(writer, mailbox))
        try:
            thread.start()
            hub.unsubscribe(mailbox)
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        finally:
            reader.close()


if __name__ == '__main__':
    unittest.main()
