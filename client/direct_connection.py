"""Loopback game endpoints forwarded to the authenticated public TLS gateway."""
import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tls_transport import https_request,pinned_socket,relay,MAX_BODY


def remote_login(address,trust_directory,identifier,password):
    status,_,raw=https_request(address,trust_directory,'POST','/game/launcher/login',
        json.dumps({'steam_id':identifier,'password':password}).encode(),{'Content-Type':'application/json'})
    data=json.loads(raw)
    if status!=200:raise RuntimeError(data.get('error','Private server login failed (HTTP '+str(status)+')'))
    data['endpoint']='http://127.0.0.1:18770/game'
    return data


class DirectBridge:
    def __init__(self,address,trust_directory,token,api_port=18770,transport_port=18769):
        self.address=address;self.trust_directory=Path(trust_directory);self.token=token
        self.stop=threading.Event();self.servers=[];self.threads=[]
        bridge=self
        class ApiProxy(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def dispatch(self):
                try:
                    if not (self.path=='/health' or self.path.startswith('/game/')):
                        self.send_error(404);return
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<=size<=MAX_BODY or self.headers.get('Transfer-Encoding'):
                        self.send_error(413);return
                    body=self.rfile.read(size) if size else None
                    if body is not None and len(body)!=size:raise ValueError('Incomplete request')
                    headers={k:v for k,v in self.headers.items() if k.lower() in ('authorization','content-type','current-epoch-ms')}
                    status,response_headers,raw=https_request(bridge.address,bridge.trust_directory,self.command,self.path,body,headers)
                    self.send_response(status)
                    for key,value in response_headers:
                        if key.lower() not in ('connection','transfer-encoding','content-length','server','date'):self.send_header(key,value)
                    self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                except (OSError,ValueError,RuntimeError):
                    try:self.send_error(502,'Private TLS server connection failed')
                    except OSError:pass
            do_GET=do_POST=dispatch
        class GameProxy(socketserver.BaseRequestHandler):
            def handle(self):
                remote=None
                try:
                    remote=pinned_socket(bridge.address,bridge.trust_directory)
                    host=bridge.address[0]
                    request='CONNECT /transport HTTP/1.1\r\nHost: '+host+'\r\nAuthorization: Bearer '+bridge.token+'\r\n\r\n'
                    remote.sendall(request.encode('ascii'))
                    headers=bytearray()
                    while not headers.endswith(b'\r\n\r\n'):
                        chunk=remote.recv(1)
                        if not chunk:raise OSError('Gateway closed during authentication')
                        headers.extend(chunk)
                        if len(headers)>16384:raise ValueError('Invalid gateway response')
                    if headers.split(b'\r\n',1)[0].split(b' ')[1]!=b'200':raise RuntimeError('Game gateway rejected authentication')
                    relay(self.request,remote,bridge.stop)
                except (OSError,ValueError,RuntimeError):
                    if remote is not None:remote.close()
        class GameServer(socketserver.ThreadingTCPServer):
            allow_reuse_address=True;daemon_threads=True
        try:
            self.servers.append(ThreadingHTTPServer(('127.0.0.1',api_port),ApiProxy))
            self.servers[-1].daemon_threads=True
            self.servers.append(GameServer(('127.0.0.1',transport_port),GameProxy))
        except BaseException:
            for server in self.servers:server.server_close()
            raise

    @property
    def api_port(self):return self.servers[0].server_address[1]
    @property
    def transport_port(self):return self.servers[1].server_address[1]
    def start(self):
        for server in self.servers:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.threads.append(thread)
        return self
    def close(self):
        self.stop.set()
        for server in self.servers:
            if self.threads:server.shutdown()
            server.server_close()
        for thread in self.threads:thread.join(timeout=2)
