"""HTTPS control API and authenticated Photon CONNECT gateway on one TLS port."""
import http.client
import json
import socket
import ssl
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit

from server import ApiError
from tls_transport import relay,MAX_BODY


class GatewayServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,context,game,backend_url,transport_address):
        super().__init__(address,GatewayHandler)
        self.context=context;self.game=game;self.backend=urlsplit(backend_url);self.transport_address=transport_address
    def get_request(self):
        raw,address=self.socket.accept();raw.settimeout(15)
        try:return self.context.wrap_socket(raw,server_side=True,do_handshake_on_connect=False),address
        except BaseException:raw.close();raise
    def process_request_thread(self,request,address):
        try:request.do_handshake()
        except (OSError,ssl.SSLError):request.close();return
        super().process_request_thread(request,address)


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def handle(self):
        try:super().handle()
        except (ConnectionResetError,BrokenPipeError,TimeoutError,ssl.SSLError):pass
    def do_CONNECT(self):
        remote=None
        try:
            if self.path!='/transport':raise ApiError(404,'Unknown transport endpoint')
            claims=self.server.game.verify_claims(self.headers.get('Authorization',''))
            if 'realm' in claims or 'purpose' in claims:raise ApiError(403,'A launcher session is required')
            remote=socket.create_connection(self.server.transport_address,timeout=10)
            self.send_response(200,'Connection Established');self.end_headers();self.wfile.flush()
            self.close_connection=True
            relay(self.connection,remote)
        except ApiError as error:self.error(error.status,error.message)
        except OSError:
            if remote:remote.close()
            self.error(502,'Internal game service unavailable')
    def error(self,status,message):
        raw=json.dumps({'error':message}).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close')
        self.end_headers();self.close_connection=True
        try:self.wfile.write(raw)
        except OSError:pass
    def forward(self):
        if not (self.path=='/health' or self.path.startswith('/game/')):
            self.error(404,'Unknown API endpoint');return
        backend=None
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<=size<=MAX_BODY or self.headers.get('Transfer-Encoding'):
                self.error(413,'Request too large');return
            body=self.rfile.read(size) if size else None
            if body is not None and len(body)!=size:raise ValueError('Incomplete request')
            headers={k:v for k,v in self.headers.items() if k.lower() in ('authorization','content-type')}
            backend=http.client.HTTPConnection(self.server.backend.hostname,self.server.backend.port or 80,timeout=30)
            backend.request(self.command,self.path,body,headers)
            response=backend.getresponse();raw=response.read(MAX_BODY+1)
            if len(raw)>MAX_BODY:raise ValueError('Backend response too large')
            if self.path=='/health' and response.status==200:
                raw=json.dumps({'status':'ok','service':'wicked-private-realms','transport':'authenticated-tls-connect','protocol':'private-tls-v1'}).encode()
            self.send_response(response.status)
            for key,value in response.getheaders():
                if key.lower() not in ('connection','content-length','transfer-encoding','server','date'):self.send_header(key,value)
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        except (OSError,ValueError):self.error(502,'Internal API service unavailable')
        finally:
            if backend:backend.close()
    do_GET=do_POST=forward
