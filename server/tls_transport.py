"""TLS gateway transport shared by the distributable server and client."""
import hashlib
import http.client
import json
import select
import socket
import ssl
import threading
from pathlib import Path
from urllib.parse import urlsplit

MAX_BODY=192*1024*1024
_trust_lock=threading.Lock()


def server_address(value):
    value=value.strip()
    if not value:raise ValueError('Enter the server hostname or IP')
    explicit='://' in value
    parsed=urlsplit(value if explicit else 'https://'+value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('Use a server IP/hostname, optionally followed by :TLS-port')
    port=parsed.port or (443 if explicit else 8443)
    if not 1<=port<=65535:raise ValueError('Invalid server port')
    return parsed.hostname,port


def pinned_socket(address,trust_directory,timeout=15):
    host,port=address
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    # Private servers generate persistent self-signed certificates. Authenticate
    # the exact certificate before sending any application data (TOFU).
    context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
    raw=socket.create_connection(address,timeout=timeout)
    try:connection=context.wrap_socket(raw,server_hostname=host)
    except BaseException:raw.close();raise
    fingerprint=hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest()
    directory=Path(trust_directory);directory.mkdir(parents=True,exist_ok=True)
    name=hashlib.sha256((host.lower()+':'+str(port)).encode()).hexdigest()+'.json'
    path=directory/name
    try:
        with _trust_lock:
            if path.exists():
                saved=json.loads(path.read_text(encoding='utf-8'))
                if saved.get('sha256')!=fingerprint:
                    raise RuntimeError('Server TLS certificate changed; verify the server fingerprint before removing its saved trust entry')
            else:
                path.write_text(json.dumps({'host':host,'port':port,'sha256':fingerprint},indent=2)+'\n',encoding='utf-8')
        return connection
    except BaseException:connection.close();raise


def https_request(address,trust_directory,method,path,body=None,headers=None):
    if not path.startswith('/') or '\r' in path or '\n' in path:raise ValueError('Invalid request path')
    connection=http.client.HTTPSConnection(address[0],address[1],timeout=15)
    try:
        connection.sock=pinned_socket(address,trust_directory)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse()
        raw=response.read(MAX_BODY+1)
        if len(raw)>MAX_BODY:raise ValueError('Server response is too large')
        return response.status,response.getheaders(),raw
    finally:connection.close()


def relay(left,right,stop=None):
    """Single I/O thread for both directions, including SSL pending plaintext.

    SSL sockets are never concurrently accessed by competing read/write threads.
    Bounded buffers provide backpressure to slow peers.
    """
    sockets=(left,right);buffers={left:bytearray(),right:bytearray()}
    left.setblocking(False);right.setblocking(False)
    try:
        while not (stop and stop.is_set()):
            reads=[s for s in sockets if len(buffers[sockets[1] if s is left else sockets[0]])<256*1024]
            writes=[s for s in sockets if buffers[s]]
            pending=[s for s in reads if isinstance(s,ssl.SSLSocket) and s.pending()]
            readable,writable,_=select.select(reads,writes,[],0 if pending else .5)
            readable=list(set(readable+pending))
            for current in readable:
                try:data=current.recv(65536)
                except (BlockingIOError,ssl.SSLWantReadError,ssl.SSLWantWriteError):continue
                if not data:return
                buffers[right if current is left else left].extend(data)
            for current in writable:
                try:count=current.send(buffers[current])
                except (BlockingIOError,ssl.SSLWantReadError,ssl.SSLWantWriteError):continue
                if count<=0:return
                del buffers[current][:count]
    except (OSError,ValueError):return
    finally:
        for current in sockets:
            try:current.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            current.close()
