from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import threading
import unittest

from authoritative_checkpoints import CheckpointStore, CheckpointWriter
from authoritative_state import Snapshot


BUILD = 'ab' * 32


class CheckpointTests(unittest.TestCase):
    def test_reopen_exact_data_and_metadata_and_fence_a_replaced_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'authority.sqlite3'
            store = CheckpointStore(path)
            first = store.begin_epoch('realm')
            store.save(first, BUILD, Snapshot(100, 2**64 - 1, b'full-frame'), {'players': [0, 1]})
            reopened = CheckpointStore(path)
            checkpoint = reopened.load('realm', BUILD)
            self.assertEqual((100, 2**64 - 1, b'full-frame'),
                             (checkpoint.snapshot.tick, checkpoint.snapshot.checksum, checkpoint.snapshot.data))
            self.assertEqual({'players': [0, 1]}, checkpoint.metadata)
            second = reopened.begin_epoch('realm', expected_generation=1)
            with self.assertRaisesRegex(ValueError, 'Stale authority'):
                store.save(first, BUILD, Snapshot(101, 1, b'old-worker'), {})
            reopened.save(second, BUILD, Snapshot(102, 2, b'new-worker'), {})
            self.assertEqual(b'new-worker', store.load('realm', BUILD).snapshot.data)
            with self.assertRaises(ValueError):
                reopened.begin_epoch('realm', expected_generation=1)

    def test_wrong_build_old_tick_and_corrupt_storage_cannot_be_loaded_or_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'authority.sqlite3'
            store = CheckpointStore(path)
            epoch = store.begin_epoch('realm')
            store.save(epoch, BUILD, Snapshot(10, 7, b'valid'), {'key': 'value'})
            with self.assertRaises(ValueError):
                store.load('realm', 'cd' * 32)
            with self.assertRaises(ValueError):
                store.save(epoch, BUILD, Snapshot(9, 8, b'old'), {})
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute('UPDATE authority_checkpoints SET data=?', (b'corrupt',))
            with self.assertRaisesRegex(ValueError, 'integrity'):
                store.load('realm', BUILD)

    def test_writer_keeps_latest_state_without_blocking_the_producer_and_freezes_metadata(self):
        entered, release = threading.Event(), threading.Event()

        class SlowStore:
            encode_metadata = staticmethod(CheckpointStore.encode_metadata)

            def __init__(self):
                self.writes = []

            def save(self, epoch, build, snapshot, metadata):
                if snapshot.tick == 1:
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError('test did not release the storage writer')
                self.writes.append((snapshot.tick, metadata))

        store = SlowStore()
        writer = CheckpointWriter(store, None, BUILD)
        try:
            writer.offer(Snapshot(1, 1, b'first'), {'value': 1})
            self.assertTrue(entered.wait(2))
            metadata = {'value': 2}
            for tick in range(2, 102):
                writer.offer(Snapshot(tick, tick, b'latest'), metadata)
            metadata['value'] = 999
            self.assertEqual(101, writer.pending[0].tick)
            with self.assertRaises(ValueError):
                writer.offer(Snapshot(100, 100, b'old'), {})
        finally:
            release.set()
            writer.close()
        self.assertEqual([(1, {'value': 1}), (101, {'value': 2})], store.writes)

    def test_storage_failure_is_reported_on_shutdown(self):
        class BrokenStore:
            encode_metadata = staticmethod(CheckpointStore.encode_metadata)

            def save(self, *_args):
                raise OSError('disk failure')

        writer = CheckpointWriter(BrokenStore(), None, BUILD)
        writer.offer(Snapshot(1, 1, b'first'), {})
        with self.assertRaisesRegex(RuntimeError, 'disk failure'):
            writer.close()
