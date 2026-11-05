"""Black-box tests of compiled .NET host against the Python gateway (no game)."""
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import tests  # Adds the server and archived reference client to the test import path.
from tests.test_public_gateway import PublicGatewayTests
from run_private_server import tls_context


@unittest.skipUnless(os.name=='nt','compiled Windows client tests')
class NativeHostTests(PublicGatewayTests):
    def setUp(self):
        super().setUp()
        self.native_temp=tempfile.TemporaryDirectory(prefix='native host isolated ')
        self.host=Path(self.native_temp.name)/'WickedNativeHost.exe'
        shutil.copy2(ROOT/'native/dist/WickedNativeHost.exe',self.host)
        shutil.copy2(ROOT/'native/dist/WickedAdapter.dll',self.host.with_name('WickedAdapter.dll'))
        self.input={'server':self.address[0]+':'+str(self.address[1]),'steam_id':'76561197960278073','password':self.password}
    def tearDown(self):
        self.native_temp.cleanup();super().tearDown()
    def run_host(self,*args,config=None):
        return subprocess.run([str(self.host),*args],input=json.dumps(config or self.input)+'\n',cwd=Path(self.native_temp.name),
            capture_output=True,text=True,encoding='utf-8',timeout=25)
    def test_compiled_host_unit_checks(self):
        result=self.run_host('--self-test');self.assertEqual(0,result.returncode,result.stderr)
        self.assertIn('self-tests passed',result.stdout)
    def test_native_login_and_certificate_change_rejected(self):
        result=self.run_host('--network-check');self.assertEqual(0,result.returncode,result.stderr)
        context,_=tls_context(Path(self.tmp.name)/'changed-native-cert');self.gateway.context=context
        result=self.run_host('--network-check');self.assertNotEqual(0,result.returncode)
        self.assertNotIn('Signed in:',result.stdout)
    def test_native_wrong_password_creates_no_account(self):
        result=self.run_host('--network-check',config=dict(self.input,password='wrong'))
        self.assertNotEqual(0,result.returncode);self.assertIn('Incorrect server password',result.stderr)
        with self.store.transaction() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM accounts').fetchone()[0])
    def test_compiled_loopback_bridge_api_and_binary_backpressure(self):
        cancel=Path(self.native_temp.name)/'stop'
        process=subprocess.Popen([str(self.host),'--bridge-only','--cancel-file',str(cancel)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=self.native_temp.name,text=True,encoding='utf-8')
        output=queue.Queue()
        def collect():
            for line in process.stdout:output.put(line)
        reader=threading.Thread(target=collect,daemon=True);reader.start()
        process.stdin.write(json.dumps(self.input)+'\n');process.stdin.close()
        try:
            for _ in range(12):
                line=output.get(timeout=15)
                if 'Native loopback bridge ready.' in line:break
            else:self.fail('Native bridge did not start')
            with urllib.request.urlopen('http://127.0.0.1:18770/health',timeout=10) as response:self.assertEqual('ok',json.load(response)['status'])
            body=json.dumps({'steam_id':self.input['steam_id'],'password':self.password}).encode()
            request=urllib.request.Request('http://127.0.0.1:18770/game/launcher/login',body,{'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=10) as response:self.assertEqual(self.input['steam_id'],json.load(response)['steam_id'])
            wrong=json.dumps({'steam_id':self.input['steam_id'],'password':'wrong'}).encode()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18770/game/launcher/login',wrong,{'Content-Type':'application/json'}),timeout=10)
            self.assertEqual(403,error.exception.code)
            with socket.create_connection(('127.0.0.1',18769),timeout=15) as connection:
                payload=bytes(range(256))*8192;sender=threading.Thread(target=connection.sendall,args=(payload,));sender.start()
                received=bytearray()
                while len(received)<len(payload):
                    part=connection.recv(min(65536,len(payload)-len(received)));self.assertTrue(part);received.extend(part)
                sender.join(timeout=10);self.assertFalse(sender.is_alive());self.assertEqual(payload,received)
            cancel.write_text('stop');process.wait(timeout=10);self.assertEqual(0,process.returncode,process.stderr.read())
        finally:
            if process.poll() is None:process.kill();process.wait()
            reader.join(timeout=3);process.stdout.close();process.stderr.close()


if __name__=='__main__':unittest.main(verbosity=2)
