import hashlib,json,unittest
from pathlib import Path
from photon_wire import *

class PhotonWireTests(unittest.TestCase):
    def test_native_response_vectors(self):
        refs=json.loads((Path(__file__).parent/'research/photon-reference-vectors.json').read_text())
        for item in refs['responses']:
            params={1:bytes([1,2,3,4])} if item['includeKey'] else {}
            self.assertEqual(item['hex'],response(0,params,internal=True)[2:].hex())
        self.assertEqual(PRIME,int.from_bytes(bytes.fromhex(refs['OakleyPrime768']),'little'))
        self.assertEqual(GENERATOR,refs['Generator'])
    def test_native_cipher_vector(self):
        v=json.loads((Path(__file__).parent/'research/photon-reference-vectors.json').read_text())['syntheticCrypto']
        key=KeyExchange();key.key=hashlib.sha256(bytes.fromhex(v['publicKey'])).digest()
        self.assertEqual(bytes([1,2,3,4,5]),key.decrypt(bytes.fromhex(v['ciphertext'])))
    def test_parameter_roundtrip(self):
        data={1:'test',2:bytes(range(200)),3:{'a':True,255:'value'},4:12345,5:-50,6:None}
        self.assertEqual(data,Reader(parameters(data)).params())
    def test_dh_agreement(self):
        a,b=KeyExchange(),KeyExchange()
        a.derive(b.raw(b.public));b.derive(a.raw(a.public));self.assertEqual(a.key,b.key)
    def test_reject_invalid(self):
        with self.assertRaises(ValueError):Reader(b'\x43\x05ab').val()
        with self.assertRaises(ValueError):KeyExchange().derive(b'\x01')

if __name__=='__main__':unittest.main(verbosity=2)
