"""Photon Protocol18 subset and TCP framing for the private research server."""
import hashlib
import secrets
import struct
from dataclasses import dataclass

PRIME=int('FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A63A3620FFFFFFFFFFFFFFFF',16)
GENERATOR=22


def uint(value):
    if value<0:raise ValueError('Unsigned integer required')
    out=bytearray()
    while value>=128:out.append((value&127)|128);value>>=7
    out.append(value);return bytes(out)


@dataclass(frozen=True)
class Byte:
    value:int


@dataclass(frozen=True)
class IntArray:
    values:tuple


def value(obj):
    if obj is None:return b'\x08'
    if obj is False:return b'\x1b'
    if obj is True:return b'\x1c'
    if isinstance(obj,Byte):return bytes([3,obj.value]) if obj.value else b'\x22'
    if isinstance(obj,int):
        if obj==0:return b'\x1e'
        if 0<obj<256:return bytes([11,obj])
        if -256<obj<0:return bytes([12,-obj])
        return b'\x09'+uint((obj<<1)^(obj>>31))
    if isinstance(obj,str):
        raw=obj.encode('utf-8');return b'\x07'+uint(len(raw))+raw
    if isinstance(obj,bytes):return b'\x43'+uint(len(obj))+obj
    if isinstance(obj,IntArray):return b'\x49'+uint(len(obj.values))+b''.join(uint((n<<1)^(n>>31)) for n in obj.values)
    if isinstance(obj,dict):return b'\x15'+uint(len(obj))+b''.join(value(k)+value(v) for k,v in obj.items())
    if isinstance(obj,list):return b'\x17'+uint(len(obj))+b''.join(value(v) for v in obj)
    raise ValueError('Unsupported Photon value '+type(obj).__name__)


class Reader:
    def __init__(self,data):self.data=data;self.pos=0
    def take(self,n):
        if n<0 or n>len(self.data)-self.pos:raise ValueError('Truncated Photon packet')
        result=self.data[self.pos:self.pos+n];self.pos+=n;return result
    def byte(self):return self.take(1)[0]
    def uint(self):
        v=0
        for shift in range(0,35,7):
            b=self.byte();v|=(b&127)<<shift
            if b<128:return v
        raise ValueError('Invalid compressed integer')
    def zigzag(self):
        n=self.uint();return (n>>1)^-(n&1)
    def val(self,t=None):
        t=self.byte() if t is None else t
        if t==8:return None
        if t==2:return bool(self.byte())
        if t in (27,28):return t==28
        if t==3:return self.byte()
        if t==4:return struct.unpack('<h',self.take(2))[0]
        if t==5:return struct.unpack('<f',self.take(4))[0]
        if t==6:return struct.unpack('<d',self.take(8))[0]
        if t==7:return self.take(self.uint()).decode('utf-8')
        if t==9:return self.zigzag()
        if t in (11,12):return self.byte()*(1 if t==11 else -1)
        if t in (13,14):return int.from_bytes(self.take(2),'little')*(1 if t==13 else -1)
        if t in (29,30,31,32,33,34):return 0
        if t==67:return self.take(self.uint())
        if t==21:
            count=self.uint();out={}
            for _ in range(count):
                key_type=self.byte();k=self.val(key_type)
                if key_type in (3,34):k=Byte(k)
                out[k]=self.val()
            return out
        if t==23:return [self.val() for _ in range(self.uint())]
        if t==71:return [self.val(7) for _ in range(self.uint())]
        if t==73:return [self.zigzag() for _ in range(self.uint())]
        if t==66:
            count=self.uint();raw=self.take((count+7)//8)
            return [bool(raw[i//8]&(1<<(i%8))) for i in range(count)]
        raise ValueError('Unsupported Photon type '+str(t))
    def params(self):
        count=self.byte();out={}
        for _ in range(count):
            k=self.byte();out[k]=self.val()
        return out


def parameters(params):
    if len(params)>255:raise ValueError('Too many parameters')
    return bytes([len(params)])+b''.join(bytes([k])+value(v) for k,v in params.items())


def response(op,params=None,return_code=0,debug=None,internal=False):
    return bytes([0xf3,7 if internal else 3,op])+struct.pack('<h',return_code)+value(debug)+parameters(params or {})


def event(code,params):return bytes([0xf3,4,code])+parameters(params)


def frame(body,channel=0,reliable=True):return b'\xfb'+struct.pack('>I',len(body)+7)+bytes([channel,int(reliable)])+body


class KeyExchange:
    def __init__(self):
        self.secret=secrets.randbits(256)
        self.public=pow(GENERATOR,self.secret,PRIME)
        self.key=None
    @staticmethod
    def raw(n):return n.to_bytes((n.bit_length()+7)//8,'big')
    def derive(self,peer):
        public=int.from_bytes(peer,'big')
        if not 1<public<PRIME-1:raise ValueError('Invalid DH public key')
        shared=pow(public,self.secret,PRIME)
        self.key=hashlib.sha256(self.raw(shared)).digest()
        return self.raw(self.public)
    def decrypt(self,data):
        from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
        from cryptography.hazmat.primitives.padding import PKCS7
        if self.key is None:raise ValueError('Encryption not initialized')
        dec=Cipher(algorithms.AES(self.key),modes.CBC(bytes(16))).decryptor()
        unpad=PKCS7(128).unpadder()
        return unpad.update(dec.update(data)+dec.finalize())+unpad.finalize()
