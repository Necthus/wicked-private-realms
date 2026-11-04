import struct
import unittest
import uuid
from character_transfer import replace_owner,zstd
from save_wire import xxhash64,cerimal_checksum


class CharacterTransferTests(unittest.TestCase):
    old='11111111-2222-3333-4444-555555555555'
    new='66666666-2222-3333-4444-555555555555'
    def document(self,plain,compressed):
        schema=b'example-schema'
        payload=zstd(plain,True) if compressed else plain
        return b'CERIMAL\x03'+struct.pack('<IIIQ',len(schema),len(payload),compressed,xxhash64(schema+payload))+schema+payload

    def test_compressed_and_plain_transfer_changes_only_owner(self):
        plain=b'character-name-and-stats:'+uuid.UUID(self.old).bytes_le+b':remaining-fields'
        for compressed in (0,1):
            with self.subTest(compressed=compressed):
                result=replace_owner(self.document(plain,compressed),self.old,self.new)
                cerimal_checksum(result)
                size=struct.unpack_from('<I',result,8)[0]
                self.assertEqual(b'example-schema',result[28:28+size])
                payload=result[28+size:]
                self.assertEqual(plain.replace(uuid.UUID(self.old).bytes_le,uuid.UUID(self.new).bytes_le),zstd(payload) if compressed else payload)

    def test_missing_or_ambiguous_owner_rejected(self):
        for plain in (b'no-owner',uuid.UUID(self.old).bytes_le*2,uuid.UUID(self.old).bytes_le+uuid.UUID(self.new).bytes_le):
            with self.subTest(plain=plain),self.assertRaises(ValueError):replace_owner(self.document(plain,0),self.old,self.new)

    def test_checksum_failure_rejected(self):
        data=bytearray(self.document(uuid.UUID(self.old).bytes_le,0));data[-1]^=1
        with self.assertRaises(ValueError):replace_owner(bytes(data),self.old,self.new)
