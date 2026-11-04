import json
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import unittest
import urllib.request

from client.direct_connection import DirectBridge,remote_login
from game_backend import GameBackend,GameHandler
from http.server import ThreadingHTTPServer
from launcher_auth import password_hash
from public_gateway import GatewayServer
from run_private_server import tls_context
from server import Store
from tls_transport import https_request,pinned_socket,server_address


class PublicGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.store=Store(root/'db.sqlite3','test-internal-secret-'*3)
        self.password='unit-test-server-password'
        self.game=GameBackend(self.store,'http://127.0.0.1:18770',True,password_hash(self.password))
        self.api=ThreadingHTTPServer(('127.0.0.1',0),GameHandler);self.api.store=self.store;self.api.game=self.game
        class Echo(socketserver.BaseRequestHandler):
            def handle(self):
                while True:
                    data=self.request.recv(65536)
                    if not data:return
                    self.request.sendall(data)
        self.echo=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo);self.echo.daemon_threads=True
        context,_=tls_context(root/'tls')
        self.gateway=GatewayServer(('127.0.0.1',0),context,self.game,
            'http://127.0.0.1:'+str(self.api.server_address[1]),self.echo.server_address)
        self.trust=root/'client-trust';self.address=self.gateway.server_address
        self.services=[self.api,self.echo,self.gateway];self.threads=[]
        for service in self.services:
            thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start();self.threads.append(thread)
    def tearDown(self):
        for service in reversed(self.services):service.shutdown();service.server_close()
        for thread in self.threads:thread.join(timeout=3)
        self.tmp.cleanup()
    def login(self):return remote_login(self.address,self.trust,'76561197960278073',self.password)

    def test_password_gate_and_original_game_api_work_over_tls(self):
        with self.assertRaisesRegex(RuntimeError,'Incorrect server password'):
            remote_login(self.address,self.trust,'76561197960278073','wrong')
        with self.store.transaction() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM accounts').fetchone()[0])
        profile=self.login()
        status,_,raw=https_request(self.address,self.trust,'POST','/game/auth/v3/token',
            json.dumps({'entitlementTicket':profile['token']}).encode(),{'Content-Type':'application/json'})
        self.assertEqual(200,status);self.assertEqual(profile['account_id'],json.loads(raw)['data'][0]['accountId'])

    def test_unauthenticated_transport_is_rejected(self):
        connection=pinned_socket(self.address,self.trust)
        try:
            connection.sendall(b'CONNECT /transport HTTP/1.1\r\nHost: localhost\r\n\r\n')
            self.assertIn(b' 401 ',connection.recv(4096).split(b'\r\n')[0])
        finally:connection.close()

    def test_loopback_bridge_relays_api_and_binary_stream_without_ssh(self):
        profile=self.login();bridge=DirectBridge(self.address,self.trust,profile['token'],0,0).start()
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(bridge.api_port)+'/health',timeout=10) as response:
                self.assertEqual('ok',json.load(response)['status'])
            with socket.create_connection(('127.0.0.1',bridge.transport_port),timeout=10) as connection:
                payload=bytes(range(256))*4096
                sender=threading.Thread(target=connection.sendall,args=(payload,));sender.start()
                received=bytearray()
                while len(received)<len(payload):
                    data=connection.recv(min(65536,len(payload)-len(received)))
                    self.assertTrue(data);received.extend(data)
                sender.join(timeout=10);self.assertFalse(sender.is_alive());self.assertEqual(payload,received)
        finally:bridge.close()

    def test_changed_certificate_rejected_before_login(self):
        self.login()
        replacement,_=tls_context(Path(self.tmp.name)/'replacement-tls')
        self.gateway.context=replacement
        with self.assertRaisesRegex(RuntimeError,'certificate changed'):self.login()

    def test_address_formats(self):
        self.assertEqual(('example.com',8443),server_address('example.com'))
        self.assertEqual(('127.0.0.1',9443),server_address('127.0.0.1:9443'))
        self.assertEqual(('example.com',443),server_address('https://example.com'))
        for value in ('http://example.com','https://user:password@example.com','example.com/private'):
            with self.subTest(value=value),self.assertRaises(ValueError):server_address(value)
