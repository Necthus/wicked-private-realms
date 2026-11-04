import struct,unittest,uuid,json
from pathlib import Path
from save_wire import *

class SaveWireTests(unittest.TestCase):
    def fixture(self):
        ident=uuid.UUID('11111111-2222-3333-4444-555555555555')
        def blob(b):return struct.pack('<i',len(b))+b
        def doc(b):return b'CERIMAL\x03'+struct.pack('<IIIQ',0,len(b),0,xxhash64(b))+b
        docs=list(map(doc,[b'realm-meta',b'char-meta',b'realm-content',b'char-content']))
        raw=blob(docs[0])+blob(docs[2])+struct.pack('<i',1)+ident.bytes_le+blob(docs[1])+blob(docs[3])
        data=struct.pack('<iiiBi',2,1,1,0,2)+struct.pack('<i',1)+ident.bytes_le
        data+=struct.pack('<4Q',*(cerimal_checksum(d) for d in docs))+struct.pack('<4i',*map(len,docs))
        return raw,cycle_info(data)

    def test_cycle_roundtrip_and_missing_mask(self):
        raw,info=self.fixture();c=Cycle(info,{})
        c.add(2,1,raw[20:]);self.assertEqual([2],c.mask());self.assertFalse(c.complete())
        c.add(2,0,raw[:20]);self.assertTrue(c.complete())
        self.assertEqual(raw,c.data()[0]);self.assertEqual(b'char-content',c.data()[1]['characters'][0]['contents'][28:])

    def test_duplicate_and_conflicting_chunks(self):
        _,info=self.fixture();c=Cycle(info,{})
        c.add(2,0,b'a');c.add(2,0,b'a')
        with self.assertRaises(ValueError):c.add(2,0,b'b')
        with self.assertRaises(ValueError):c.add(3,1,b'a')

    def test_invalid_chunks_and_truncation(self):
        with self.assertRaises(ValueError):chunk(b'x'*11)
        with self.assertRaises(ValueError):chunk(struct.pack('<iII',1,1,1))
        with self.assertRaises(ValueError):cycle_info(b'\0'*17)
        with self.assertRaises(ValueError):bundle(b'\xff'*12)
        raw,_=self.fixture()
        with self.assertRaises(ValueError):bundle(raw+b'extra')

    def test_reply_layout(self):
        self.assertEqual(25,len(finish_response(2,0,123,[3])))
        self.assertEqual(struct.pack('<iBq',2,1,123),backend_result(2,True,123))

    @unittest.skipUnless((Path(__file__).parent/'research/save-bundle-reference.bin').is_file(),'Optional private native save fixture is not distributed')
    def test_native_reference(self):
        root=Path(__file__).parent/'research'
        native=json.loads((root/'save-native-vectors.json').read_text())
        info=cycle_info(bytes.fromhex(native['infoHex']))
        raw=(root/'save-bundle-reference.bin').read_bytes()
        c=Cycle(dict(info,chunks=1),{0:raw})
        result=c.data()[1];expected=native['decodedBundle']
        self.assertEqual(expected['metadataSize'],len(result['metadata']))
        self.assertEqual(expected['contentsSize'],len(result['contents']))
        self.assertEqual(expected['characterCount'],len(result['characters']))
        self.assertEqual(native['finishHex'],finish_response(2,0,638900000000000000,[7]).hex())
        self.assertEqual(native['backendHex'],backend_result(2,True,638900000000000000).hex())

    def test_corruption_rejected(self):
        raw,info=self.fixture();corrupted=bytearray(raw);corrupted[-1]^=1
        c=Cycle(dict(info,chunks=1),{0:bytes(corrupted)})
        with self.assertRaises(ValueError):c.data()

    def test_xxhash_known_empty(self):
        self.assertEqual(0xef46db3751d8e999,xxhash64(b''))

if __name__=='__main__':unittest.main(verbosity=2)
