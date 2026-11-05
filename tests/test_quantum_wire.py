import json
import unittest
from pathlib import Path
from quantum_wire import pack,unpack,start_request,simulation_start,input_frame,client_inputs


class QuantumWireTests(unittest.TestCase):
    def test_native_client_input_stream(self):
        v=json.loads((Path(__file__).resolve().parents[1]/'research/quantum-input-vectors.json').read_text())['clientInput']
        result=client_inputs(bytes.fromhex(v['hex']))
        self.assertEqual([{'slot':0,'tick':123,'data':bytes(range(47)),'flags':1}],result)
    def test_input_frames_match_native_encoder(self):
        vectors=json.loads((Path(__file__).resolve().parents[1]/'research/quantum-input-vectors.json').read_text())['vectors']
        for v in vectors:
            entries=[None]*4
            if v['active']:entries[0]=(bytes(47),1,bytes([1,0,0,0,0,0,0,0,7,8,9]))
            raw,bits=input_frame(1,entries,server_time=1/60)
            self.assertEqual(v['hex'],raw.hex())
            self.assertEqual(v['bits'],bits)
    @unittest.skipUnless((Path(__file__).resolve().parents[1]/'research/quantum-start-reference.bin').is_file(),'Optional private native session fixture is not distributed')
    def test_actual_start_request_matches_native_decoder(self):
        root=Path(__file__).resolve().parents[1]/'research'
        raw=(root/'quantum-start-reference.bin').read_bytes()
        expected=json.loads((root/'quantum-start-decoded.json').read_text())
        response=expected.pop('simulationStart',None)
        actual=start_request(raw)
        actual['runtimeConfig']=list(actual['runtimeConfig'])
        self.assertEqual(expected,actual)
        if response:
            native,bits=simulation_start(start_request(raw))
            self.assertEqual(response['hex'],native.hex())
            self.assertEqual(response['bits'],bits)
    def test_matches_actual_client_vectors(self):
        vectors=json.loads((Path(__file__).resolve().parents[1]/'research/quantum-wire-vectors.json').read_text())['vectors']
        for vector in vectors:
            with self.subTest(name=vector['name'],value=vector['value']):
                name,value=vector['name'],vector['value']
                if name=='TickChecksum':fields={'tick':value,'checksum':123456789}
                elif name=='Disconnect':fields={'reason':value}
                elif name=='AddPlayer':fields={'slot':value,'data':bytes([0,1,2,255])}
                elif name=='Command':fields={'slot':value,'tick':123,'data':bytes([0,1,2,255])}
                elif name=='FrameSnapshot':fields={'tick':value,'total_size':4,'last':True,'data':bytes([0,1,2,255])}
                else:fields={'value':value}
                encoded,bits=pack(name,**fields)
                self.assertEqual(vector['hex'],encoded.hex())
                self.assertEqual(vector['bits'],bits)
                decoded=unpack(bytes.fromhex(vector['hex']),vector['bits'])
                for k,v in fields.items():self.assertEqual(v,decoded[k])

    def test_rejects_truncated_and_unknown(self):
        with self.assertRaises(ValueError):unpack(b'\xff')
        with self.assertRaises(ValueError):unpack(b'\x0a\x01')
        with self.assertRaises(ValueError):unpack(b'\x0c\xfe\xff')

    def test_length_limit(self):
        with self.assertRaises(ValueError):pack('AddPlayer',slot=0,data=b'x'*65536)


if __name__=='__main__':unittest.main(verbosity=2)
