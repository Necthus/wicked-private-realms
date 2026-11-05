"""Strict parser for build 29466's ProtocolQ3 save cycle messages."""
from dataclasses import dataclass
import struct
import uuid

MAX_BYTES=64*1024*1024
MAX_CHUNKS=8192
MASK=(1<<64)-1


def xxhash64(data):
    """XXH64 seed 0, matching Moon.Cerimal.SDocument's stored-data checksum."""
    p1,p2,p3,p4,p5=11400714785074694791,14029467366897019727,1609587929392839161,9650029242287828579,2870177450012600261
    rot=lambda v,n:((v<<n)|(v>>(64-n)))&MASK
    def rnd(acc,v):return (rot((acc+v*p2)&MASK,31)*p1)&MASK
    def merge(acc,v):return ((acc^rnd(0,v))*p1+p4)&MASK
    size=len(data);offset=0
    if size>=32:
        a,b,c,d=(p1+p2)&MASK,p2,0,(-p1)&MASK
        while offset<=size-32:
            v=struct.unpack_from('<4Q',data,offset)
            a,b,c,d=rnd(a,v[0]),rnd(b,v[1]),rnd(c,v[2]),rnd(d,v[3]);offset+=32
        result=(rot(a,1)+rot(b,7)+rot(c,12)+rot(d,18))&MASK
        for v in (a,b,c,d):result=merge(result,v)
    else:result=p5
    result=(result+size)&MASK
    while offset<=size-8:
        result^=rnd(0,struct.unpack_from('<Q',data,offset)[0]);result=(rot(result,27)*p1+p4)&MASK;offset+=8
    if offset<=size-4:
        result^=struct.unpack_from('<I',data,offset)[0]*p1&MASK
        result=(rot(result,23)*p2+p3)&MASK;offset+=4
    while offset<size:
        result^=data[offset]*p5&MASK;result=rot(result,11)*p1&MASK;offset+=1
    result^=result>>33;result=result*p2&MASK;result^=result>>29;result=result*p3&MASK;result^=result>>32
    return result


def cerimal_checksum(data):
    if len(data)<28 or data[:8]!=b'CERIMAL\x03':raise ValueError('Unsupported Cerimal document')
    metadata_size,content_size,compression,expected=struct.unpack_from('<IIIQ',data,8)
    if compression not in (0,1) or metadata_size+content_size+28!=len(data):
        raise ValueError('Invalid Cerimal section lengths or compression')
    if xxhash64(data[28:])!=expected:raise ValueError('Cerimal checksum mismatch')
    return expected


class Reader:
    def __init__(self,data):
        if len(data)>MAX_BYTES:raise ValueError('Save exceeds size limit')
        self.data=data;self.pos=0
    def take(self,n):
        if n<0 or n>len(self.data)-self.pos:raise ValueError('Truncated save message')
        result=self.data[self.pos:self.pos+n];self.pos+=n;return result
    def number(self,fmt='i'):
        return struct.unpack('<'+fmt,self.take(struct.calcsize('<'+fmt)))[0]
    def blob(self):
        return self.take(self.number())
    def end(self):
        if self.pos!=len(self.data):raise ValueError('Trailing save data')


def bundle(data):
    reader=Reader(data)
    metadata,contents=reader.blob(),reader.blob()
    count=reader.number()
    if not 0<=count<=4:raise ValueError('Invalid character count')
    characters=[]
    for _ in range(count):
        identifier=str(uuid.UUID(bytes_le=reader.take(16)))
        characters.append({'id':identifier,'metadata':reader.blob(),'contents':reader.blob()})
    reader.end()
    return {'metadata':metadata,'contents':contents,'characters':characters}


def cycle_info(data):
    reader=Reader(data)
    index,players,actor=reader.number(),reader.number(),reader.number()
    flush=reader.number('B');count=reader.number()
    if flush not in (0,1) or not 1<=players<=4 or not 1<=count<=MAX_CHUNKS or index<0:
        raise ValueError('Invalid save-cycle header')
    actors=[reader.number() for _ in range(players)]
    if len(set(actors))!=players or actor not in actors:raise ValueError('Invalid save participants')
    ids=[str(uuid.UUID(bytes_le=reader.take(16))) for _ in range(players)]
    if len(set(ids))!=players:raise ValueError('Duplicate save character')
    hashes_meta=[reader.number('Q') for _ in range(players+1)]
    hashes_content=[reader.number('Q') for _ in range(players+1)]
    sizes_meta=[reader.number() for _ in range(players+1)]
    sizes_content=[reader.number() for _ in range(players+1)]
    if any(n<0 or n>MAX_BYTES for n in sizes_meta+sizes_content) or sum(sizes_meta+sizes_content)>MAX_BYTES:
        raise ValueError('Invalid save sizes')
    reader.end()
    return {'index':index,'actor':actor,'flush':bool(flush),'chunks':count,'actors':actors,
            'characters':ids,'metadata_hashes':hashes_meta,'contents_hashes':hashes_content,
            'metadata_sizes':sizes_meta,'contents_sizes':sizes_content}


def chunk(data):
    if len(data)<12 or len(data)>32768+12:raise ValueError('Invalid chunk size')
    index,total,sequence=struct.unpack_from('<iII',data)
    if index<0 or not 1<=total<=MAX_CHUNKS or sequence>=total:raise ValueError('Invalid chunk header')
    return index,total,sequence,data[12:]


def finish_request(data):
    if len(data)!=4:raise ValueError('Invalid finish request')
    index=struct.unpack('<i',data)[0]
    if index<0:raise ValueError('Invalid cycle index')
    return index


def finish_response(index,kind,ticks,words):
    if not 0<=kind<=4 or len(words)>(MAX_CHUNKS+63)//64:raise ValueError('Invalid finish response')
    return struct.pack('<iBqi',index,kind,ticks,len(words))+b''.join(struct.pack('<Q',v) for v in words)


def backend_result(index,success,ticks):
    return struct.pack('<iBq',index,int(success),ticks)


@dataclass
class Cycle:
    info:dict
    parts:dict
    committed:bool=False
    ticks:int=0
    digest:str|None=None

    def add(self,total,sequence,data):
        if total!=self.info['chunks']:raise ValueError('Chunk count changed')
        if sequence in self.parts:
            if self.parts[sequence]!=data:raise ValueError('Conflicting duplicate save chunk')
            return
        if sum(map(len,self.parts.values()))+len(data)>MAX_BYTES:raise ValueError('Save too large')
        self.parts[sequence]=data

    def mask(self):
        out=[0]*((self.info['chunks']+63)//64)
        for n in self.parts:out[n//64]|=1<<(n%64)
        return out

    def complete(self):return len(self.parts)==self.info['chunks']

    def data(self,known_retired_characters=()):
        if not self.complete():raise ValueError('Missing save chunks')
        raw=b''.join(self.parts[n] for n in range(self.info['chunks']))
        result=bundle(raw)
        ids=[c['id'] for c in result['characters']]
        declared=set(self.info['characters']);actual=set(ids)
        if len(ids)!=len(actual) or not declared<=actual or not (actual-declared)<=set(known_retired_characters):
            raise ValueError('Character identities differ from save-cycle declaration')
        by_id={c['id']:c for c in result['characters']}
        for identifier in actual-declared:
            cerimal_checksum(by_id[identifier]['metadata']);cerimal_checksum(by_id[identifier]['contents'])
        metadata=[result['metadata']]+[by_id[i]['metadata'] for i in self.info['characters']]
        contents=[result['contents']]+[by_id[i]['contents'] for i in self.info['characters']]
        if list(map(len,metadata))!=self.info['metadata_sizes'] or list(map(len,contents))!=self.info['contents_sizes']:
            raise ValueError('Bundle sizes differ from save-cycle declaration')
        if list(map(cerimal_checksum,metadata))!=self.info['metadata_hashes'] or list(map(cerimal_checksum,contents))!=self.info['contents_hashes']:
            raise ValueError('Document checksums differ from save-cycle declaration')
        # Departed players can remain in the game-produced bundle. Their
        # unconfirmed/stale contents must not overwrite a newer character save.
        result['characters']=[by_id[i] for i in self.info['characters']]
        return raw,result
