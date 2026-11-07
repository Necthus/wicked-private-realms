"""Bounded loopback transport between independently injected game processes."""
import socket
import struct
import threading

from authoritative_state import Snapshot, SnapshotHub, _receive_exact as receive_exact, receive_snapshot, send_snapshots
from tools.authority_probe.relay import loopback_pair

INPUT = struct.Struct('!4sII47s')
COMMAND = struct.Struct('!4sII')


class CrossProcessRelay:
    def __init__(self, source, slot=0, inject_fault=True):
        self.source = source
        self.slot = slot
        self.inject_fault = inject_fault
        self.receiver = None
        self.hub = SnapshotHub()
        self.mailbox = self.hub.subscribe()
        self.writer, self.reader = loopback_pair()
        self.input_writer, self.input_reader = loopback_pair()
        self.command_writer, self.command_reader = loopback_pair()
        self.input_writer.settimeout(1)
        self.command_writer.settimeout(1)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.latest_tick = -1
        self.pending_state = None
        self.inflight_tick = None
        self.fault_start = self.fault_end = None
        self.stats = {'states_forwarded': 0, 'states_dropped': 0,
                      'inputs_forwarded': 0, 'inputs_dropped': 0, 'last_sequence': -1,
                      'commands_forwarded': 0, 'last_command_sequence': -1, 'states_coalesced': 0}
        self.errors = []
        self.threads = [threading.Thread(target=fn, daemon=True)
                        for fn in (self._send_states, self._receive_states, self._receive_inputs, self._receive_commands)]
        for thread in self.threads:
            thread.start()

    def _send_states(self):
        send_snapshots(self.writer, self.mailbox)

    def _fault(self, tick):
        return self.fault_start is not None and self.fault_start <= tick < self.fault_end

    def arm_fault(self, tick):
        with self.lock:
            if self.inject_fault and self.fault_start is None:
                self.fault_start = ((tick + 300 + 5) // 6) * 6
                self.fault_end = self.fault_start + 180

    def publish(self, meta, data):
        with self.lock:
            self.latest_tick = meta['tick']
        self.hub.publish(Snapshot(meta['tick'], int(meta['checksum'], 16), bytes(data)))

    def input(self, meta, data):
        sequence, tick = meta['sequence'], meta['tick']
        if type(sequence) is not int or type(tick) is not int or not 0 <= sequence < 2**32 or not 0 <= tick < 2**31 or len(data) != 47:
            raise ValueError('Invalid native input packet')
        self.input_writer.sendall(INPUT.pack(b'WRI1', sequence, tick, data))

    def command(self, meta, data):
        sequence = meta['sequence']
        if type(sequence) is not int or not 0 <= sequence < 2**32 or not 0 < len(data) <= 1024 * 1024:
            raise ValueError('Invalid native command packet')
        self.command_writer.sendall(COMMAND.pack(b'WRC1', sequence, len(data)) + data)

    def _receive_commands(self):
        try:
            while not self.stopped.is_set():
                magic, sequence, length = COMMAND.unpack(receive_exact(self.command_reader, COMMAND.size))
                if magic != b'WRC1' or not 0 < length <= 1024 * 1024:
                    raise ValueError('Invalid native command header')
                data = receive_exact(self.command_reader, length)
                with self.lock:
                    if sequence <= self.stats['last_command_sequence']:
                        raise ValueError('Replayed native command')
                    self.stats['last_command_sequence'] = sequence
                # Commands remain reliable during an outage; only this peer waits.
                while not self.stopped.is_set():
                    with self.lock:
                        waiting = self._fault(self.latest_tick)
                    if not waiting:
                        break
                    self.stopped.wait(0.05)
                if not self.stopped.is_set():
                    self.source.post({'type': 'authority-command', 'payload': {'sequence': sequence, 'slot': self.slot}}, data)
                    with self.lock:
                        self.stats['commands_forwarded'] += 1
        except Exception as error:
            if not self.stopped.is_set():
                self.errors.append(str(error))

    def _receive_states(self):
        try:
            while not self.stopped.is_set():
                snapshot = receive_snapshot(self.reader)
                with self.lock:
                    drop = self._fault(snapshot.tick)
                    if drop:
                        self.stats['states_dropped'] += 1
                        self.pending_state = None
                    else:
                        if self.pending_state is not None:
                            self.stats['states_coalesced'] += 1
                        self.pending_state = snapshot
                self._dispatch_state()
        except Exception as error:
            if not self.stopped.is_set():
                self.errors.append(str(error))

    def _dispatch_state(self):
        with self.lock:
            if self.receiver is None or self.inflight_tick is not None or self.pending_state is None:
                return
            receiver, snapshot = self.receiver, self.pending_state
            self.pending_state = None
            self.inflight_tick = snapshot.tick
            self.stats['states_forwarded'] += 1
        receiver.post({'type': 'authoritative-state', 'payload': {
            'tick': snapshot.tick, 'checksum': format(snapshot.checksum, 'x')}}, snapshot.data)

    def acknowledge(self, tick):
        with self.lock:
            if tick != self.inflight_tick:
                raise ValueError('Unexpected snapshot acknowledgement')
            self.inflight_tick = None
        self._dispatch_state()

    def _receive_inputs(self):
        try:
            while not self.stopped.is_set():
                magic, sequence, tick, data = INPUT.unpack(receive_exact(self.input_reader, INPUT.size))
                with self.lock:
                    if magic != b'WRI1' or sequence <= self.stats['last_sequence']:
                        raise ValueError('Invalid or reordered native input stream')
                    self.stats['last_sequence'] = sequence
                    drop = self._fault(self.latest_tick)
                    self.stats['inputs_dropped' if drop else 'inputs_forwarded'] += 1
                if not drop:
                    self.source.post({'type': 'authority-input', 'payload': {'sequence': sequence, 'tick': tick, 'slot': self.slot}}, data)
        except Exception as error:
            if not self.stopped.is_set():
                self.errors.append(str(error))

    def report(self):
        with self.lock:
            return {**self.stats, 'fault_start': self.fault_start, 'fault_end': self.fault_end,
                    'latest_source_tick': self.latest_tick, 'errors': list(self.errors), 'player_slot': self.slot,
                    'inflight_tick': self.inflight_tick,
                    'pending_tick': self.pending_state.tick if self.pending_state is not None else None}

    def close(self):
        self.stopped.set()
        self.hub.unsubscribe(self.mailbox)
        for connection in (self.writer, self.reader, self.input_writer, self.input_reader, self.command_writer, self.command_reader):
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        for thread in self.threads:
            thread.join(timeout=3)
        return self.report()
