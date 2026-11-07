import socket
import struct
import threading
import time
import unittest

from tools.authority_lan_protocol import (KIND_CONTROL, KIND_INPUT, KIND_STATE,
                                          LanPeer, decode, encode)


class AuthorityLanProtocolTests(unittest.TestCase):
    def test_truncated_protocol_header_is_rejected(self):
        left, right = socket.socketpair()
        try:
            left.sendall(struct.pack('!I', 1) + b'x')
            with self.assertRaisesRegex(ValueError, 'Truncated LAN frame'):
                decode(right)
        finally:
            left.close()
            right.close()

    def test_bad_packet_closes_peer_and_records_failure(self):
        left, right = socket.socketpair()
        peer = LanPeer(left)
        reader = threading.Thread(target=lambda: peer.receive_loop(lambda *_: None))
        reader.start()
        try:
            right.sendall(struct.pack('!I', 5) + struct.pack('!BI', 255, 0))
            reader.join(timeout=2)
            self.assertFalse(reader.is_alive())
            self.assertTrue(peer.closed.is_set())
            self.assertIn('Unsupported LAN frame kind', peer.error)
        finally:
            peer.close()
            right.close()
            reader.join(timeout=2)

    def test_callback_rejection_is_reported(self):
        left, right = socket.socketpair()
        peer = LanPeer(left)
        def reject(*_):
            raise ValueError('State digest mismatch')
        reader = threading.Thread(target=lambda: peer.receive_loop(reject))
        reader.start()
        try:
            right.sendall(encode(KIND_STATE, {'tick': 1}, b'corrupted'))
            reader.join(timeout=2)
            self.assertFalse(reader.is_alive())
            self.assertEqual(peer.error, 'State digest mismatch')
        finally:
            peer.close()
            right.close()
            reader.join(timeout=2)

    def test_binary_frames_roundtrip(self):
        left, right = socket.socketpair()
        try:
            left.sendall(encode(KIND_INPUT, {'sequence': 4, 'tick': 19}, b'x' * 47))
            kind, meta, data = decode(right)
            self.assertEqual(kind, KIND_INPUT)
            self.assertEqual(meta, {'sequence': 4, 'tick': 19})
            self.assertEqual(data, b'x' * 47)
        finally:
            left.close(); right.close()

    def test_peer_coalesces_complete_states_but_keeps_control(self):
        left, right = socket.socketpair()
        peer = LanPeer(left)
        try:
            peer.send_state({'tick': 1, 'checksum': '1'}, b'old')
            peer.send_state({'tick': 2, 'checksum': '2'}, b'new')
            peer.send_control({'event': 'ack', 'tick': 2})
            received = []
            deadline = time.monotonic() + 2
            while len(received) < 2 and time.monotonic() < deadline:
                received.append(decode(right))
            self.assertEqual(received[0][0], KIND_CONTROL)
            self.assertEqual(received[0][1]['event'], 'ack')
            self.assertEqual(received[1][0], KIND_STATE)
            self.assertEqual(received[1][1]['tick'], 2)
            self.assertEqual(received[1][2], b'new')
        finally:
            peer.close(); right.close()


if __name__ == '__main__':
    unittest.main()
