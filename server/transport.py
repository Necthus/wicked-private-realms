"""Private Photon/Quantum transport; shared-timeline mode remains experimental."""
import json
import math
import socket
import socketserver
import struct
import time
import sys
import os
import secrets
import threading
from datetime import datetime,timezone
from urllib.parse import parse_qs
from collections import deque
import hashlib
from pathlib import Path
from photon_wire import KeyExchange,Reader,response,frame,Byte,IntArray,event
from quantum_wire import start_request,simulation_start,unpack,pack,input_frame,client_inputs
from save_wire import Cycle,cycle_info,chunk,finish_request,finish_response,backend_result
from quantum_wire import messages
from room_simulation import ConfirmedRoom
from shared_saves import SharedSaves

LOG=Path(os.environ.get('WICKED_TRANSPORT_LOG',str(Path(__file__).resolve().parent/'logs/transport.jsonl')))

def emit(event,**data):
    if not os.environ.get('WICKED_TRACE_WIRE'):
        if event in ('packet','ping','ping-response-sent'):return
        if event=='operation' and data.get('code')==253:return
        if event=='room-event' and data.get('code') in (0,101,102):return
    record={'time':datetime.now(timezone.utc).isoformat(),'event':event,**data}
    print(json.dumps(record),flush=True)
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open('a',encoding='utf-8') as f:f.write(json.dumps(record)+'\n')


class Handler(socketserver.BaseRequestHandler):
    def exact(self,n):
        data=b''
        while len(data)<n:
            chunk=self.request.recv(n-len(data))
            if not chunk:raise EOFError
            data+=chunk
        return data

    def handle(self):
        self.request.settimeout(30)
        self.request.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        key_exchange=KeyExchange()
        self.identity=None
        self.ticket=None
        self.role='master'
        self.room=None
        self.send_lock=threading.RLock()
        self.pending_commands=deque()
        self.save_cycles={}
        emit('connected')
        try:
            while True:
                first=self.exact(1)
                if first==b'\xfb':
                    rest=self.exact(6);size=struct.unpack('>I',rest[:4])[0]
                    if size<7 or size>2*1024*1024:raise ValueError('Invalid frame length')
                    body=self.exact(size-7)
                    emit('packet',channel=rest[4],reliable=rest[5],length=len(body),message_type=body[1] if len(body)>1 else None)
                    if body[:2]==b'\xf3\x00':
                        init_reply=b'\xf3\x01\x00'
                        self.send_packet(init_reply)
                        emit('init-response-sent')
                    elif body[:2]==b'\xf3\x06':
                        reader=Reader(body[2:]);operation=reader.byte();params=reader.params()
                        if operation!=0 or not isinstance(params.get(1),bytes):raise ValueError('Unsupported internal operation')
                        public=key_exchange.derive(params[1])
                        self.send_packet(response(0,{1:public},internal=True))
                        emit('key-exchange-completed',peer_key_bytes=len(params[1]))
                    else:
                        kind=body[1]&127
                        if kind==2:
                            payload=key_exchange.decrypt(body[2:]) if body[1]&128 else body[2:]
                            reader=Reader(payload);op=reader.byte();params=reader.params()
                            emit('operation',code=op,parameter_types={str(k):type(v).__name__ for k,v in params.items()})
                            if not self.operation(op,params):return
                            continue
                        # No fabricated authentication/room success. Stop at the
                        # first unknown operation and preserve its framing only.
                        emit('unimplemented-message',message_type=body[1] if len(body)>1 else None)
                        return
                elif first==b'\xf0':
                    payload=self.exact(4)
                    emit('ping',prefix=payload.hex())
                    self.send_ping(payload)
                    emit('ping-response-sent')
                else:
                    emit('unknown-prefix',prefix=first.hex());return
        except (EOFError,TimeoutError,ConnectionError,OSError,ValueError) as e:
            emit('closed',actor=getattr(self,'actor',None),reason=type(e).__name__,detail=str(e)[:180])
        finally:
            self.leave_room()

    def send_packet(self,payload):
        self.send_raw(frame(payload))

    def send_ping(self,payload):
        # Timestamp inside the same lock as the write, after any queued frame.
        with self.send_lock:
            self.send_raw(b'\xf0'+struct.pack('>I',int(time.monotonic()*1000)&0xffffffff)+payload)

    def send_raw(self,payload):
        # One TCP stream may be written by several players' handler threads.
        # Heartbeats must never be inserted into the middle of a Photon frame.
        lock=getattr(self,'send_lock',None)
        if lock is None:self.request.sendall(payload);return
        started=time.monotonic()
        try:
            with lock:self.request.sendall(payload)
        finally:
            elapsed=time.monotonic()-started
            if elapsed>=.25:
                emit('transport-slow-send',actor=getattr(self,'actor',None),bytes=len(payload),seconds=round(elapsed,3))

    def leave_room(self):
        room=getattr(self,'room',None)
        if room is None:return
        with self.server.room_lock:
            if 'timeline' in room:room['timeline'].leave(self.actor)
            if 'shared_saves' in room:room['shared_saves'].player_left(self.actor)
            if room['players'].get(self.actor) is self:del room['players'][self.actor]
            self.room=None
            for peer in list(room['players'].values()):
                try:peer.send_packet(event(254,{254:self.actor,233:False}))
                except OSError:pass
            if not room['players']:self.server.rooms.pop(self.identity['realm'],None)

    def operation(self,op,params):
        game=getattr(self.server,'game',None)
        if op==230 and game is not None:
            token=params.get(221)
            if token in self.server.tickets:
                self.ticket=token;self.identity=self.server.tickets[token];self.role='game'
            else:
                query=params.get(216,'')
                parsed=parse_qs(query)
                emit('auth-fields',query_keys=list(parsed),has_user_id=225 in params)
                jwt=(parsed.get('PlayJwt') or [''])[0]
                try:
                    claims=game.verify_claims(jwt)
                    if claims.get('purpose')!='private-transport-test' or not claims.get('realm'):
                        raise ValueError('A realm-bound transport ticket is required')
                    with game.store.transaction() as db:
                        game.entity(db,claims['realm'],'realm',claims['sub'])
                        account_name=db.execute('SELECT name FROM accounts WHERE id=?',(claims['sub'],)).fetchone()['name']
                    self.identity={'account':claims['sub'],'realm':claims['realm'],'name':account_name}
                    self.ticket=secrets.token_urlsafe(32)
                    self.server.tickets[self.ticket]=self.identity
                except Exception as e:
                    emit('authentication-rejected',reason=type(e).__name__)
                    self.send_packet(response(op,return_code=32767,debug='Private realm ticket required'))
                    return True
            self.send_packet(response(op,{225:self.identity['account'],221:self.ticket}))
            emit('authentication-accepted',role=self.role)
            return True
        if self.identity is not None and op in (226,227):
            room=params.get(255)
            if room!=self.identity['realm']:
                self.send_packet(response(op,return_code=32767,debug='Room does not match the private ticket'))
                return True
            if self.role=='master':
                self.send_packet(response(op,{230:'127.0.0.1:18769',255:room}))
                emit('game-server-assigned')
            else:
                if not getattr(self.server,'allow_room_probe',False):
                    self.send_packet(response(op,return_code=32758,debug='Quantum room simulation is not implemented yet'))
                    emit('room-join-rejected-simulation-pending');return True
                with self.server.room_lock:
                    state=self.server.rooms.setdefault(room,{'players':{},'next_actor':1,'properties':params.get(248,{})})
                    if len(state['players'])>=4:
                        self.send_packet(response(op,return_code=32765,debug='Private room is full'));return True
                    self.actor=state['next_actor'];state['next_actor']+=1
                    self.player_properties=dict(params.get(249,{}) or {})
                    self.player_properties[Byte(253)]=self.identity['account']
                    if self.identity.get('name'):
                        self.player_properties[Byte(255)]=self.identity['name']
                    state['players'][self.actor]=self;self.room=state
                    props=dict(state['properties']);props.update({Byte(255):Byte(4),Byte(253):True,Byte(254):False,Byte(248):min(state['players'])})
                    actors={n:peer.player_properties for n,peer in state['players'].items()}
                    self.send_packet(response(op,{254:self.actor,249:actors,248:props,252:IntArray(tuple(actors))}))
                    joined=event(255,{254:self.actor,252:IntArray(tuple(actors)),249:self.player_properties})
                    for peer in list(state['players'].values()):peer.send_packet(joined)
                    emit('room-member-joined',actor=self.actor,players=len(actors),simulation_ready=False)
            return True
        if self.identity is not None and op==253 and getattr(self,'room',None) is not None:
            code=params.get(244);data=params.get(245)
            if isinstance(data,bytes) and code in (10,11,12):
                capture_dir=LOG.parent/'save-wire-captures'
                capture_dir.mkdir(exist_ok=True)
                filename=str(time.time_ns())+'-actor'+str(self.actor)+'-event'+str(code)+'.bin'
                (capture_dir/filename).write_bytes(data)
                if getattr(self.server,'shared_simulation',False):
                    saves=self.room.get('shared_saves')
                    if saves is None:raise ValueError('No shared simulation to save')
                    try:saves.message(self,code,data)
                    except Exception as exc:
                        emit('shared-save-rejected',actor=self.actor,code=code,reason=type(exc).__name__,detail=str(exc)[:200])
                        if code==12:
                            index=finish_request(data)
                            self.send_packet(event(13,{0:finish_response(index,4,0,[]),254:0}))
                    return True
                return self.save_message(code,data)
            emit('room-event',actor=getattr(self,'actor',None),code=code,data_type=type(data).__name__,size=len(data) if isinstance(data,(bytes,str,list,dict)) else None,
                 targets=params.get(252),receiver_group=params.get(246))
            if getattr(self.server,'shared_simulation',False):
                return self.shared_message(code,data,params)
            if isinstance(data,bytes) and code in (100,101):
                if data and data[0]==21:
                    (LOG.parent/'quantum-start-request.bin').write_bytes(data)
                    request=start_request(data)
                    if request['id']!=self.identity['account'] or request['protocolVersion']!='3.0.0.0':
                        raise ValueError('Unsupported or mismatched Quantum session request')
                    if request['initialTick']!=0:
                        raise ValueError('Snapshot reconnection not implemented')
                    self.quantum_request=request
                    self.session_started=time.monotonic()
                    # Plain full inputs are easier to verify during bring-up.
                    # The confirmed server config controls the client's encoder.
                    request['config']['InputDeltaCompression']=False
                    payload,bits=simulation_start(request)
                    self.send_packet(event(100,{245:payload,254:0}))
                    emit('quantum-simulation-start-sent',config_players=request['config']['PlayerCount'],bits=bits)
                else:
                    emit('quantum-protocol-captured',code=code,length=len(data),first_message=data[0] if data else None)
                    (LOG.parent/('quantum-message-'+str(data[0] if data else 'empty')+'.bin')).write_bytes(data)
                    if data and data[0]==10:
                        # RttUpdate is client-to-server here; the client's
                        # dispatcher does not bind it as an incoming message.
                        self.max_ping=max(0,min(10000,unpack(data)['value']))
                    if data and data[0]==11 and hasattr(self,'quantum_request'):
                        added=unpack(data)
                        if added['slot']!=0:raise ValueError('Only one local player slot is implemented')
                        capacity=self.quantum_request['config']['PlayerCount']
                        entries=[None]*capacity
                        index=self.actor-1
                        if index>=capacity:raise ValueError('Actor exceeds simulation capacity')
                        rpc=struct.pack('<ii',self.actor,added['slot'])+added['data']
                        entries[index]=(bytes(self.quantum_request['config']['InputFixedSize']),1,rpc)
                        tick=self.quantum_request['config']['RollbackWindow']
                        payload,bits=input_frame(tick,entries,input_size=self.quantum_request['config']['InputFixedSize'],server_time=time.monotonic()-self.session_started,max_ping=getattr(self,'max_ping',10))
                        self.send_packet(event(102,{245:payload,254:0}))
                        entries[index]=(bytes(self.quantum_request['config']['InputFixedSize']),1,None)
                        payload,_=input_frame(tick+1,entries,input_size=self.quantum_request['config']['InputFixedSize'],server_time=time.monotonic()-self.session_started,max_ping=getattr(self,'max_ping',10))
                        self.send_packet(event(102,{245:payload,254:0}))
                        self.last_input_tick=tick+1
                        self.last_input=entries[index][0]
                        emit('player-add-input-sent',actor=self.actor,player_index=index,tick=tick,bits=bits)
                    if data and data[0]==14 and hasattr(self,'quantum_request'):
                        command=unpack(data)
                        if command['slot']!=0:raise ValueError('Unexpected command slot')
                        self.pending_commands.append(command['data'])
                    if data and data[0]==18 and hasattr(self,'last_input_tick'):
                        removed=unpack(data)
                        if removed['value'] not in (-1,0):raise ValueError('Unexpected remove-player slot')
                        if not all(c.committed for c in self.save_cycles.values()):
                            raise ValueError('Cannot remove player with an uncommitted save')
                        self.player_removed=True
                        # Quantum removes the player when a confirmed frame
                        # changes their slot to PlayerNotPresent, just as its
                        # LocalInputProvider does after OnRemovePlayer.
                        players=[None]*self.quantum_request['config']['PlayerCount']
                        for _ in range(2):
                            self.last_input_tick+=1
                            payload,_=input_frame(self.last_input_tick,players,input_size=self.quantum_request['config']['InputFixedSize'],
                                server_time=time.monotonic()-self.session_started,max_ping=getattr(self,'max_ping',10))
                            self.send_packet(event(102,{245:payload,254:0}))
                        emit('player-removal-input-sent',actor=self.actor,tick=self.last_input_tick)
            if code==102 and isinstance(data,bytes) and hasattr(self,'last_input_tick'):
                (LOG.parent/'client-input-latest.bin').write_bytes(data)
                size=self.quantum_request['config']['InputFixedSize']
                batch=client_inputs(data,size)
                confirmed=0
                for item in batch:
                    if item['slot']!=0:raise ValueError('Unexpected input slot')
                    if item['tick']<=self.last_input_tick:continue
                    if item['tick']>self.last_input_tick+120:raise ValueError('Input gap exceeds supported window')
                    if len(self.room['players'])!=1:raise ValueError('Multi-client input aggregation is not implemented')
                    for tick in range(self.last_input_tick+1,item['tick']+1):
                        current=tick==item['tick']
                        raw=item['data'] if current else self.last_input
                        flags=item['flags'] if current else 5
                        rpc=self.pending_commands.popleft() if self.pending_commands else None
                        if rpc is not None:flags|=8
                        players=[None]*self.quantum_request['config']['PlayerCount']
                        if not getattr(self,'player_removed',False):players[self.actor-1]=(raw,flags,rpc)
                        payload,_=input_frame(tick,players,input_size=size,server_time=time.monotonic()-self.session_started,max_ping=getattr(self,'max_ping',10))
                        self.send_packet(event(102,{245:payload,254:0}))
                        self.last_input_tick=tick;self.last_input=raw;confirmed+=1
                if confirmed and self.last_input_tick%60<confirmed:
                    emit('single-client-input-progress',tick=self.last_input_tick)
            return True
        if self.identity is not None and op==252 and getattr(self,'room',None) is not None:
            properties=dict(params.get(251,{}) or {})
            target=params.get(254,0)
            if isinstance(target,bool) or not isinstance(target,int) or target<0:
                self.send_packet(response(op,return_code=32767,debug='Invalid property target'))
                return True
            if target:
                if target!=self.actor or target not in self.room['players']:
                    self.send_packet(response(op,return_code=32767,debug='Cannot edit another player'))
                    return True
                if Byte(255) in properties and self.identity.get('name'):
                    properties[Byte(255)]=self.identity['name']
                self.room['players'][target].player_properties.update(properties)
            else:self.room['properties'].update(properties)
            self.send_packet(response(op))
            # SetProperties requests use ActorNr (254), but PropertiesChanged
            # events use TargetActorNr (253). Omitting 253 makes RealtimeClient
            # apply actor NickName (property 255) as room MaxPlayers (also 255).
            # A SteamID64 nickname then throws Int32 overflow during room join.
            for peer in list(self.room['players'].values()):peer.send_packet(event(253,{253:target,251:properties}))
            return True
        if self.identity is not None and op==254:
            self.leave_room();self.send_packet(response(op));return True
        self.send_packet(response(op,return_code=32767,debug='Private protocol research: operation not implemented'))
        return False

    def shared_message(self,code,data,event_options=None):
        if not isinstance(data,bytes):raise ValueError('Expected binary shared-session message')
        if code in (100,101) and data and data[0]==21:
            request=start_request(data)
            if request['id']!=self.identity['account'] or request['protocolVersion']!='3.0.0.0':
                raise ValueError('Unsupported shared-session identity or protocol')
            with self.server.room_lock:
                if 'timeline' not in self.room:
                    self.room['timeline']=ConfirmedRoom(request,log=emit,stall_timeout=self.server.input_stall_timeout)
                    self.room['shared_saves']=SharedSaves(self.server.game,self.identity['realm'],self.room['timeline'],log=emit)
                timeline=self.room['timeline']
            timeline.start(self,request)
            return True
        timeline=self.room.get('timeline')
        if timeline is None:return True
        if code in (100,101):
            for msg in messages(data):
                name=msg['name']
                if name=='RttUpdate':self.max_ping=max(0,min(10000,msg['value']))
                elif name=='AddPlayer':timeline.add_player(self.actor,msg['data'],msg['slot'])
                elif name=='Command':timeline.command(self.actor,msg['data'],msg['slot'])
                elif name=='RemovePlayer':
                    if not self.room['shared_saves'].can_leave(self.actor):raise ValueError('Uncommitted multiplayer save prevents player removal')
                    timeline.remove_player(self.actor,msg['value']);self.player_removed=True
                elif name=='FrameSnapshot':timeline.snapshot(self.actor,msg)
                elif name=='TickChecksum':pass
                else:raise ValueError('Unhandled client Quantum message '+name)
        elif code==102:
            timeline.submit(self.actor,client_inputs(data,timeline.config['InputFixedSize']))
        elif code==0 and len(data)==12:
            tick,checksum=struct.unpack('<iQ',data)
            with timeline.lock:
                checks=self.room.setdefault('checksums',{})
                values=checks.setdefault(tick,{})
                values[self.actor]=checksum
                if len(set(values.values()))>1:emit('shared-checksum-mismatch',tick=tick,actors=list(values))
                while len(checks)>120:checks.pop(min(checks))
        elif code==7:
            options=event_options or {}
            with self.server.room_lock:players=dict(self.room['players'])
            targets=options.get(252)
            if targets is not None:
                recipients={actor for actor in targets if actor in players}
            else:
                # Photon defaults to Others when ReceiverGroup is omitted.
                # The retail chat UI has already displayed the sender's line.
                group=options.get(246,0)
                if group==0:recipients=set(players)-{self.actor}
                elif group==1:recipients=set(players)
                elif group==2:recipients={min(players)} if players else set()
                else:raise ValueError('Unsupported chat receiver group')
            packet=event(7,{245:data,254:self.actor})
            sent=[]
            for actor,peer in players.items():
                if actor in recipients:
                    try:peer.send_packet(packet);sent.append(actor)
                    except OSError:pass  # A departing peer must not disconnect the sender.
            emit('shared-chat-forwarded',actor=self.actor,recipients=sorted(sent),bytes=len(data))
        elif code==9:emit('shared-client-loaded',actor=self.actor)
        else:emit('shared-unhandled-game-event',code=code,length=len(data))
        return True

    def save_message(self,code,data):
        try:
            if len(self.room['players'])!=1:raise ValueError('Multiplayer save validation is pending')
            if code==11:
                info=cycle_info(data)
                if info['actor']!=self.actor or info['actors']!=[self.actor]:
                    raise ValueError('Save provider or participants do not match room')
                existing=self.save_cycles.get(info['index'])
                if existing is not None:
                    if existing.info!=info:raise ValueError('Conflicting save declaration')
                else:
                    if len(self.save_cycles)>=8:
                        completed=[n for n,c in self.save_cycles.items() if c.committed]
                        if not completed:raise ValueError('Too many pending save cycles')
                        self.save_cycles.pop(min(completed))
                    self.save_cycles[info['index']]=Cycle(info,{})
                emit('save-cycle-declared',cycle=info['index'],chunks=info['chunks'])
            elif code==10:
                index,total,sequence,payload=chunk(data)
                if index not in self.save_cycles:raise ValueError('Save declaration is missing')
                self.save_cycles[index].add(total,sequence,payload)
            elif code==12:
                index=finish_request(data)
                cycle=self.save_cycles.get(index)
                if cycle is None:
                    self.send_packet(event(13,{0:finish_response(index,4,0,[]),254:0}));return True
                if not cycle.complete():
                    self.send_packet(event(13,{0:finish_response(index,1,0,cycle.mask()),254:0}));return True
                if not cycle.committed:
                    raw,save=cycle.data()
                    ticks=self.server.game.commit_transport_save(self.identity['account'],self.identity['realm'],
                        hashlib.sha256(self.ticket.encode()).hexdigest(),index,raw,save)
                    cycle.committed=True;cycle.ticks=ticks
                    emit('save-cycle-committed',cycle=index,bytes=len(raw),characters=len(save['characters']),sha256=hashlib.sha256(raw).hexdigest())
                # These two game-specific handlers explicitly read parameter 0.
                # Quantum's CustomData key 245 is not their wire envelope.
                self.send_packet(event(13,{0:finish_response(index,0,cycle.ticks,cycle.mask()),254:0}))
                self.send_packet(event(8,{0:backend_result(index,True,cycle.ticks),254:0}))
            return True
        except Exception as exc:
            emit('save-cycle-rejected',reason=type(exc).__name__,detail=str(exc)[:180])
            if code==12:
                try:self.send_packet(event(13,{0:finish_response(finish_request(data),4,0,[]),254:0}))
                except (OSError,ValueError):pass
            return True


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
    def __init__(self,*args,game=None,allow_room_probe=False,shared_simulation=False,input_stall_timeout=None,**kwargs):
        self.input_stall_timeout=float(os.environ.get('WICKED_INPUT_STALL_TIMEOUT','30') if input_stall_timeout is None else input_stall_timeout)
        if not math.isfinite(self.input_stall_timeout) or self.input_stall_timeout<0:
            raise ValueError('Input stall timeout must be finite and nonnegative')
        self._watchdog_stop=threading.Event()
        super().__init__(*args,**kwargs)
        self.game=game
        self.tickets={}
        self.rooms={}
        self.room_lock=threading.RLock()
        self.allow_room_probe=allow_room_probe
        self.shared_simulation=shared_simulation
        self._watchdog=threading.Thread(target=self._watch_rooms,name='wicked-transport-watchdog',daemon=True)
        self._watchdog.start()

    def _watch_rooms(self):
        while not self._watchdog_stop.wait(1.0):
            self.check_room_stalls()

    def check_room_stalls(self):
        # Never hold the global membership lock while waiting for a room lock
        # or network I/O; handlers acquire membership before the timeline lock.
        with self.room_lock:
            timelines=[state.get('timeline') for state in self.rooms.values()]
        for timeline in timelines:
            if timeline is None:continue
            try:peers=timeline.check_stalls()
            except Exception as error:
                emit('shared-watchdog-error',reason=type(error).__name__,detail=str(error)[:180]);continue
            for peer in peers:
                request=getattr(peer,'request',None)
                if request is None:continue
                try:request.shutdown(socket.SHUT_RDWR)
                except (OSError,ValueError):pass
                try:request.close()
                except (OSError,ValueError):pass
                # The handler's EOF/finally path removes membership and fails
                # any unfinished save agreement, then socketserver closes it.

    def server_close(self):
        self._watchdog_stop.set()
        watchdog=getattr(self,'_watchdog',None)
        if watchdog is not None:watchdog.join(timeout=2)
        super().server_close()


if __name__=='__main__':
    import argparse
    from server import Store
    from game_backend import GameBackend
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=root/'data/game-probe.sqlite3')
    parser.add_argument('--port',type=int,default=18769)
    parser.add_argument('--http-port',type=int,default=18770)
    parser.add_argument('--shared',action='store_true')
    args=parser.parse_args()
    code=os.environ.get('REALM_ENROLLMENT_CODE')
    if not code:code=(root/'data/dev-enrollment.txt').read_text().strip()
    store=Store(args.db,code.strip())
    game=GameBackend(store,'http://127.0.0.1:'+str(args.http_port),transport_experiment=True)
    with Server(('127.0.0.1',args.port),Handler,game=game,allow_room_probe=True,shared_simulation=args.shared) as server:
        emit('listening',port=args.port,shared=args.shared);server.serve_forever()
