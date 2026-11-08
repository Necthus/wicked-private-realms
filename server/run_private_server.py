"""Start a configured private realm server: one public TLS port, no SSH clients."""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib
from http.server import ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import threading

from game_backend import GameBackend,GameHandler
from launcher_auth import password_hash
from public_gateway import GatewayServer
from server import Store


def tls_context(directory,hostname='localhost',certificate=None,private_key=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    cert_path=Path(certificate) if certificate else directory/'server-cert.pem'
    key_path=Path(private_key) if private_key else directory/'server-key.pem'
    if not cert_path.exists() or not key_path.exists():
        if certificate or private_key:raise ValueError('Configured TLS certificate/key does not exist')
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Wicked Private Realms')])
        names=[x509.DNSName('localhost')]
        try:names.append(x509.IPAddress(ipaddress.ip_address(hostname)))
        except ValueError:
            if hostname!='localhost':names.append(x509.DNSName(hostname))
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
            .not_valid_after(now+timedelta(days=3650)).add_extension(x509.SubjectAlternativeName(names),critical=False)
            .sign(key,hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        os.chmod(key_path,0o600)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path),str(key_path))
    cert=x509.load_pem_x509_certificate(cert_path.read_bytes())
    fingerprint=hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return context,fingerprint


def run(config_path):
    config_path=Path(config_path).resolve();config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    password=os.environ.get('REALM_JOIN_PASSWORD') or config.get('join_password','')
    if not password or password=='CHANGE_ME':raise ValueError('Set join_password in server-config.json before starting')
    directory=Path(config.get('data_directory','server-data'))
    if not directory.is_absolute():directory=config_path.parent/directory
    directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'server-secret.json'
    if not state_path.exists():
        state_path.write_text(json.dumps({'secret':secrets.token_urlsafe(48)}),encoding='utf-8');os.chmod(state_path,0o600)
    secret=os.environ.get('REALM_ENROLLMENT_CODE') or json.loads(state_path.read_text())['secret']
    db_path=Path(config.get('database',str(directory/'game.sqlite3')))
    if not db_path.is_absolute():db_path=config_path.parent/db_path
    store=Store(db_path,secret)
    game=GameBackend(store,'http://127.0.0.1:18770',True,password_hash(password))
    services=[];threads=[]
    def start(service):
        services.append(service);thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start();threads.append(thread)
    backend_url=config.get('existing_backend')
    transport_port=int(config.get('transport_port',18769))
    if not backend_url:
        api_port=int(config.get('internal_api_port',18770))
        api=ThreadingHTTPServer(('127.0.0.1',api_port),GameHandler);api.daemon_threads=True;api.store=store;api.game=game
        start(api);backend_url='http://127.0.0.1:'+str(api.server_address[1])
        os.environ['WICKED_TRANSPORT_LOG']=str(directory/'transport.jsonl')
        from transport import Server,Handler
        transport=Server(('127.0.0.1',transport_port),Handler,game=game,allow_room_probe=True,shared_simulation=True,
                         input_stall_timeout=os.environ.get('WICKED_INPUT_STALL_TIMEOUT',config.get('input_stall_timeout',30)),
                         input_wait_timeout=os.environ.get('WICKED_INPUT_WAIT_TIMEOUT',config.get('input_wait_timeout',0.12)))
        transport_port=transport.server_address[1];start(transport)
    context,fingerprint=tls_context(directory,config.get('public_hostname','localhost'),config.get('tls_certificate'),config.get('tls_private_key'))
    port=int(config.get('port',8443))
    gateway=GatewayServer((config.get('listen','0.0.0.0'),port),context,game,backend_url,('127.0.0.1',transport_port))
    print('Private realm server listening on TLS port '+str(gateway.server_address[1]),flush=True)
    print('Server certificate SHA-256: '+fingerprint,flush=True)
    print('Data directory: '+str(directory),flush=True)
    print('Clients connect directly; SSH keys are not used.',flush=True)
    try:gateway.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        gateway.server_close()
        for service in services:service.shutdown();service.server_close()
        for thread in threads:thread.join(timeout=3)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',default='server-config.json');p.add_argument('--init',action='store_true')
    args=p.parse_args()
    if args.init:
        path=Path(args.config)
        if path.exists():raise SystemExit('Configuration already exists: '+str(path))
        path.write_text(json.dumps({'listen':'0.0.0.0','port':8443,'join_password':'CHANGE_ME','data_directory':'server-data'},indent=2)+'\n',encoding='utf-8')
        print('Created '+str(path)+'. Set join_password, then run without --init.')
    else:run(args.config)
