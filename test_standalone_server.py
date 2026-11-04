import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

from client.direct_connection import DirectBridge,remote_login
from photon_wire import frame


class StandaloneServerTests(unittest.TestCase):
    def test_new_server_needs_only_configuration_and_no_ssh_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);config=root/'server-config.json'
            config.write_text(json.dumps({'listen':'127.0.0.1','port':0,'internal_api_port':0,'transport_port':0,
                'join_password':'standalone-unit-password','data_directory':'server-data'}))
            env=dict(os.environ,PYTHONUNBUFFERED='1')
            env.pop('REALM_ENROLLMENT_CODE',None);env.pop('REALM_JOIN_PASSWORD',None)
            process=subprocess.Popen([sys.executable,str(Path(__file__).parent/'run_private_server.py'),'--config',str(config)],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',env=env)
            output=queue.Queue()
            def collect():
                for line in process.stdout:output.put(line)
            thread=threading.Thread(target=collect,daemon=True);thread.start()
            bridge=None
            try:
                line=output.get(timeout=20)
                self.assertTrue(line.startswith('Private realm server listening on TLS port '),line)
                port=int(line.split()[-1]);address=('127.0.0.1',port)
                profile=remote_login(address,root/'trust','76561197960278073','standalone-unit-password')
                bridge=DirectBridge(address,root/'trust',profile['token'],0,0).start()
                with socket.create_connection(('127.0.0.1',bridge.transport_port),timeout=10) as connection:
                    connection.sendall(frame(b'\xf3\x00synthetic-client'))
                    data=b''
                    while len(data)<10:
                        part=connection.recv(10-len(data));self.assertTrue(part);data+=part
                    self.assertEqual(b'\xf3\x01\x00',data[-3:])
                self.assertTrue((root/'server-data/server-cert.pem').is_file())
                self.assertTrue((root/'server-data/game.sqlite3').is_file())
                self.assertFalse(any('ssh' in p.name.lower() for p in (root/'server-data').rglob('*')))
            finally:
                if bridge:bridge.close()
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                thread.join(timeout=3);process.stdout.close()
