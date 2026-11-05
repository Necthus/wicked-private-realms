"""Subset of Quantum 3 protocol verified against the running build 29466 codec.

This is message encoding, not a functioning deterministic session server.
Strings and arrays have different null-bit conventions in the observed codec.
"""
import struct

IDS={'SimulationStop':6,'TickChecksum':8,'RttUpdate':10,'AddPlayer':11,
     'Disconnect':12,'FrameSnapshot':13,'Command':14,'FrameSnapshotRequest':17,'RemovePlayer':18,
     'StartRequest':21,'SimulationStart':5}
NAMES={v:k for k,v in IDS.items()}


class Bits:
    def __init__(self,data=b'',limit=None):
        self.data=bytearray(data);self.pos=0;self.limit=len(data)*8 if limit is None else limit

    def write(self,value,width):
        if value<0:value &= (1<<width)-1
        if value>>width:raise ValueError('Integer exceeds field size')
        for i in range(width):
            index=self.pos>>3
            if index==len(self.data):self.data.append(0)
            if (value>>i)&1:self.data[index]|=1<<(self.pos&7)
            self.pos+=1

    def read(self,width):
        if self.pos+width>self.limit:raise ValueError('Truncated Quantum message')
        value=0
        for i in range(width):
            value|=((self.data[self.pos>>3]>>(self.pos&7))&1)<<i;self.pos+=1
        return value

    def write_blob(self,data,string=False):
        self.write(int(data is None) if string else int(data is not None),1)
        if data is None:return
        if len(data)>65535:raise ValueError('Blob exceeds 16-bit length')
        self.write(len(data),16)
        for byte in data:self.write(byte,8)

    def read_blob(self,string=False):
        flag=self.read(1)
        if flag==int(string):return None
        size=self.read(16)
        if size*8>self.limit-self.pos:raise ValueError('Truncated Quantum blob')
        return bytes(self.read(8) for _ in range(size))

CONFIG_LAYOUT=[('PlayerCount',32),('LockstepSimulation',1),('InputDeltaCompression',1),
 ('InputDelayMin',32),('InputDelayMax',32),('InputDelayPingStart',32),('UpdateFPS',32),
 ('ChecksumInterval',32),('RollbackWindow',32),('InputHardTolerance',32),('InputRedundancy',32),
 ('InputRepeatMaxDistance',32),('SessionStartTimeout',32),('TimeCorrectionRate',32),
 ('MinTimeCorrectionFrames',32),('MinOffsetCorrectionDiff',32),('TimeScaleMin',32),
 ('TimeScalePingMin',32),('TimeScalePingMax',32),('ChecksumCrossPlatformDeterminism',1),('InputFixedSize',32)]


def read_config(stream):
    if stream.read(1):return None
    result={}
    for name,width in CONFIG_LAYOUT:
        v=stream.read(width)
        result[name]=bool(v) if width==1 else (v-(1<<32) if v&(1<<31) else v)
    return result


def write_config(stream,config):
    stream.write(int(config is None),1)
    if config is not None:
        for name,width in CONFIG_LAYOUT:stream.write(int(config[name]),width)


def start_request(data):
    stream=Bits(data)
    if stream.read(8)!=21:raise ValueError('Expected Quantum StartRequest')
    read_string=lambda:stream.read_blob(string=True).decode('utf-8')
    result={'id':read_string(),'protocolVersion':read_string(),'initialTick':stream.read(32)}
    result['config']=read_config(stream)
    result['runtimeConfig']=stream.read_blob()
    result['consumedBits']=stream.pos
    return result


def simulation_start(request,server_time=0.0,reconnect=False,waiting_for_snapshot=False):
    stream=Bits();stream.write(5,8);stream.write(int(reconnect),1)
    stream.write(int.from_bytes(struct.pack('<d',server_time),'little'),64)
    stream.write(int(waiting_for_snapshot),1)
    stream.write_blob(request['runtimeConfig'])
    write_config(stream,request['config'])
    return bytes(stream.data),stream.pos


def input_frame(tick,players,input_size=47,server_time=0.0,max_ping=10):
    """Encode a confirmed full input set; entries are None or (data, flags, rpc)."""
    if not 1<=len(players)<=64:raise ValueError('Unsupported player capacity')
    if not 0<=input_size<1024 or tick<0:raise ValueError('Invalid input frame')
    stream=Bits();stream.write(max_ping,32)
    stream.write(int.from_bytes(struct.pack('<d',server_time),'little'),64)
    stream.write(int.from_bytes(struct.pack('<d',1.0),'little'),64)
    stream.write(len(players),8);stream.write(input_size,10)
    while stream.pos%8:stream.write(0,1)
    stream.write(tick,32);stream.write(1,1);stream.write((1<<len(players))-1,len(players))
    for entry in players:
        stream.write(int(entry is None),1)
        if entry is not None:
            data,flags,rpc=entry
            if data is not None and len(data)!=input_size:raise ValueError('Input length mismatch')
            stream.write(int(bool(data)),1)
            if data:
                for b in data:stream.write(b,8)
            stream.write(flags,4)
    for entry in players:
        if entry is not None:stream.write_blob(entry[2])
    return bytes(stream.data),stream.pos


def client_inputs(data,input_size=47):
    """Decode Quantum's non-delta SimpleSerialize stream with RPCs excluded."""
    stream=Bits(data);result=[];width=8+32+input_size*8+1+8
    if not 0<=input_size<1024:raise ValueError('Invalid fixed input size')
    while stream.limit-stream.pos>=width:
        slot=stream.read(8);tick=stream.read(32)
        raw=bytes(stream.read(8) for _ in range(input_size))
        if stream.read(1):raise ValueError('Unexpected RPC in client input stream')
        flags=stream.read(8)
        result.append({'slot':slot,'tick':tick,'data':raw,'flags':flags})
    return result


def pack(name,**fields):
    stream=Bits();stream.write(IDS[name],8)
    if name in ('RttUpdate','FrameSnapshotRequest','SimulationStop','RemovePlayer'):
        stream.write(fields['value'],32)
    elif name=='TickChecksum':
        stream.write(fields['tick'],32);stream.write(fields['checksum'],64)
    elif name=='Disconnect':
        reason=fields.get('reason');stream.write_blob(None if reason is None else reason.encode('utf-8'),string=True)
    elif name in ('AddPlayer','Command'):
        stream.write(fields['slot'],32)
        if name=='Command':stream.write(fields['tick'],32)
        stream.write_blob(fields.get('data'))
    elif name=='FrameSnapshot':
        stream.write(fields['tick'],32);stream.write(fields['total_size'],32)
        stream.write(int(fields['last']),1);stream.write_blob(fields.get('data'))
    return bytes(stream.data),stream.pos


def unpack(data,bits=None):
    stream=Bits(data,bits)
    return read_message(stream)


def read_message(stream):
    start=stream.pos;code=stream.read(8)
    if code not in NAMES:raise ValueError('Unsupported Quantum message '+str(code))
    name=NAMES[code];result={'name':name}
    def signed():return struct.unpack('<i',struct.pack('<I',stream.read(32)))[0]
    if name in ('RttUpdate','FrameSnapshotRequest','SimulationStop','RemovePlayer'):result['value']=signed()
    elif name=='TickChecksum':result.update(tick=signed(),checksum=stream.read(64))
    elif name=='Disconnect':
        blob=stream.read_blob(string=True);result['reason']=None if blob is None else blob.decode('utf-8')
    elif name in ('AddPlayer','Command'):
        result['slot']=signed()
        if name=='Command':result['tick']=signed()
        result['data']=stream.read_blob()
    elif name=='FrameSnapshot':
        result.update(tick=signed(),total_size=signed(),last=bool(stream.read(1)),data=stream.read_blob())
    result['bits']=stream.pos-start
    return result


def messages(data):
    """Read PackMessages output, whose next message may start mid-byte."""
    stream=Bits(data)
    while stream.limit-stream.pos>=8:
        yield read_message(stream)
