import threading
import time
import unittest

from transport_writer import QueuedWriter


class BlockingSocket:
    def __init__(self):
        self.started=threading.Event();self.release=threading.Event();self.data=[]

    def sendall(self,data):
        self.started.set();self.release.wait(2)
        self.data.append(bytes(data))


class QueuedWriterTests(unittest.TestCase):
    def test_slow_socket_is_bounded_and_reports_overflow(self):
        connection=BlockingSocket();failures=[]
        writer=QueuedWriter(connection,failures.append,max_packets=2,max_bytes=100)
        try:
            writer.send(b'first')
            self.assertTrue(connection.started.wait(1))
            writer.send(b'second')
            with self.assertRaises(OSError):writer.send(b'third')
            self.assertEqual(['send queue exceeded its limit'],failures)
        finally:
            connection.release.set();writer.close();writer.thread.join(2)

    def test_packets_and_ping_are_written_in_order(self):
        class Socket:
            def __init__(self):self.data=[]
            def sendall(self,data):self.data.append(bytes(data))
        connection=Socket();writer=QueuedWriter(connection,lambda _:None)
        try:
            writer.send(b'frame')
            writer.send(b'ping',ping=True)
            deadline=time.monotonic()+1
            while len(connection.data)<2 and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(b'frame',connection.data[0])
            self.assertEqual(9,len(connection.data[1]))
            self.assertEqual(b'\xf0',connection.data[1][:1])
            self.assertEqual(b'ping',connection.data[1][-4:])
        finally:writer.close();writer.thread.join(2)

    def test_blocked_writer_deadline_does_not_affect_another_connection(self):
        slow=BlockingSocket();failures=[];now=[10.0]
        slow_writer=QueuedWriter(slow,failures.append,clock=lambda:now[0],timeout=5)
        class Socket:
            def __init__(self):self.data=[]
            def sendall(self,data):self.data.append(bytes(data))
        fast=Socket();fast_writer=QueuedWriter(fast,lambda _:self.fail('Healthy connection failed'))
        try:
            slow_writer.send(b'frame-1');self.assertTrue(slow.started.wait(1))
            for tick in range(10):
                slow_writer.send(bytes([tick]));fast_writer.send(bytes([tick]))
            fast_writer.drain(timeout=1)
            self.assertEqual([bytes([tick]) for tick in range(10)],fast.data)
            now[0]+=5;slow_writer.check_timeout()
            self.assertTrue(slow_writer.closed)
            self.assertEqual(['oldest queued write exceeded its deadline'],failures)
            with self.assertRaises(OSError):slow_writer.send(b'late')
            fast_writer.send(b'healthy');fast_writer.drain(timeout=1)
            self.assertEqual(b'healthy',fast.data[-1])
        finally:
            slow.release.set()
            for writer in (slow_writer,fast_writer):writer.close();writer.thread.join(2)

    def test_socket_failure_is_reported_once_and_closed_writer_rejects_packets(self):
        class Socket:
            def sendall(self,_):raise OSError('broken connection')
        failed=threading.Event();failures=[]
        def failure(reason):failures.append(reason);failed.set()
        writer=QueuedWriter(Socket(),failure)
        writer.send(b'first')
        self.assertTrue(failed.wait(1));writer.thread.join(1)
        writer.check_timeout();writer.fail('duplicate failure')
        self.assertEqual(['broken connection'],failures)
        with self.assertRaises(OSError):writer.send(b'late')

    def test_byte_limit_includes_the_write_currently_blocked_in_socket(self):
        connection=BlockingSocket();failures=[]
        writer=QueuedWriter(connection,failures.append,max_packets=100,max_bytes=8)
        try:
            writer.send(b'12345678');self.assertTrue(connection.started.wait(1))
            with self.assertRaises(OSError):writer.send(b'x')
            self.assertEqual(['send queue exceeded its limit'],failures)
        finally:
            connection.release.set();writer.close();writer.thread.join(2)

    def test_ping_timestamp_is_taken_after_previous_blocked_write(self):
        connection=BlockingSocket();now=[10.0]
        writer=QueuedWriter(connection,lambda _:None,clock=lambda:now[0])
        try:
            writer.send(b'frame');self.assertTrue(connection.started.wait(1))
            writer.send(b'ping',ping=True)
            now[0]=20.0;connection.release.set();writer.drain(timeout=1)
            self.assertEqual(b'frame',connection.data[0])
            self.assertEqual(20000,int.from_bytes(connection.data[1][1:5],'big'))
        finally:writer.close();writer.thread.join(2)
