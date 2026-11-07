"""Shared confirmed-input timeline, snapshot handoff and player slot ownership.

All connected replicas receive the same bytes for every confirmed frame. The
game clients run the deterministic simulation; this service orders their inputs.
"""
from collections import OrderedDict,deque
from dataclasses import dataclass,field
import copy
import math
import struct
import threading
import time

from photon_wire import event
from quantum_wire import input_frame,simulation_start,pack


@dataclass
class Player:
    actor:int
    index:int
    peer:object
    ready:bool=False
    active:bool=False
    horizon:int=0
    last:bytes=b''
    flags:int=1
    inputs:dict=field(default_factory=dict)
    commands:deque=field(default_factory=deque)
    add_data:bytes|None=None
    snapshot_pending:bool=False
    activated_at:float=0.0
    last_input_at:float|None=None
    last_forward_at:float|None=None
    last_received_tick:int|None=None
    timeout_requested:bool=False
    missing_since:float|None=None
    fallback_active:bool=False


class ConfirmedRoom:
    def __init__(self,request,clock=time.monotonic,log=lambda *a,**kw:None,
                 stall_timeout=30.0,input_wait_timeout=0.25):
        if request['initialTick']!=0:raise ValueError('Fresh room requires a fresh session request')
        self.request=copy.deepcopy(request)
        self.config=self.request['config']
        if self.config['PlayerCount']!=4 or self.config['InputFixedSize']!=47 or self.config['UpdateFPS']!=60:
            raise ValueError('Unsupported shared simulation configuration')
        self.config['InputDeltaCompression']=False
        self.tick=self.config['RollbackWindow']-1
        self.clock=clock;self.started=clock();self.log=log
        self.stall_timeout=float(stall_timeout)
        if not math.isfinite(self.stall_timeout) or self.stall_timeout<0:
            raise ValueError('Input stall timeout must be finite and nonnegative')
        self.input_wait_timeout=float(input_wait_timeout)
        if not math.isfinite(self.input_wait_timeout) or self.input_wait_timeout<0:
            raise ValueError('Input wait timeout must be finite and nonnegative')
        self.fallback_next_at=None
        self.last_frame_at=self.started;self.last_stall_report=None
        self.lock=threading.RLock();self.players={};self.history=OrderedDict()
        self.snapshot_source=None;self.snapshot_parts=[];self.snapshot_tick=None;self.snapshot_size=0
        self.closed=False

    def check_stalls(self):
        """Return blocking peers to disconnect, without taking transport locks.

        Buffered peers may hit their prediction limit while waiting for someone
        else. They must not be mistaken for the replica that blocked the room.
        A live TCP heartbeat or replay of old inputs does not renew input progress.
        """
        if not self.lock.acquire(blocking=False):return []
        try:
            if self.closed:return []
            now=self.clock();idle=now-self.last_frame_at
            active=[p for p in self.players.values() if p.ready and p.active]
            blocked=[p for p in active if p.horizon<=self.tick]
            if not blocked:return []
            if idle>=5 and (self.last_stall_report is None or now-self.last_stall_report>=10):
                self.last_stall_report=now
                age=lambda t:None if t is None else round(now-t,3)
                self.log('shared-input-stall',tick=self.tick,idle_seconds=round(idle,3),
                         blockers=[p.actor for p in blocked],players=[{
                             'actor':p.actor,'ready':p.ready,'active':p.active,'horizon':p.horizon,
                             'last_received_tick':p.last_received_tick,'input_age_seconds':age(p.last_input_at),
                             'progress_age_seconds':age(p.last_forward_at),
                             'reported_rtt_ms':getattr(p.peer,'max_ping',None),
                             'snapshot_pending':p.snapshot_pending} for p in self.players.values()])
            expired=[]
            for p in blocked:
                # Initial scene/asset loading gets at least two minutes. Runtime
                # input stalls use the configured limit, disabled when set to 0.
                grace=max(120.0,self.stall_timeout) if p.last_forward_at is None else self.stall_timeout
                since=p.activated_at if p.last_forward_at is None else p.last_forward_at
                if not self.stall_timeout or p.timeout_requested or now-since<grace:continue
                if idle<grace and not p.fallback_active:continue
                p.timeout_requested=True;expired.append(p.peer)
                self.log('shared-input-timeout',actor=p.actor,tick=self.tick,horizon=p.horizon,
                         idle_seconds=round(idle,3),progress_age_seconds=round(now-since,3))
            return expired
        finally:self.lock.release()

    def seconds(self):return max(0.0,self.clock()-self.started)

    def send_protocol(self,player,payload):player.peer.send_packet(event(100,{245:payload,254:0}))

    def start(self,peer,request):
        with self.lock:
            if self.closed:raise ValueError('This simulation session has closed; reconnect to the realm')
            if request['protocolVersion']!=self.request['protocolVersion']:
                raise ValueError('Quantum protocol mismatch')
            for key in ('PlayerCount','UpdateFPS','InputFixedSize'):
                if request['config'][key]!=self.config[key]:raise ValueError('Quantum config mismatch')
            if peer.actor in self.players:raise ValueError('Duplicate Quantum start')
            if any(p.peer.identity['account']==peer.identity['account'] for p in self.players.values()):
                raise ValueError('This account is already connected to the realm')
            taken={p.index for p in self.players.values()}
            index=next((i for i in range(4) if i not in taken),None)
            if index is None:raise ValueError('Four simulation slots are occupied')
            existing=[p for p in self.players.values() if p.active and p.ready]
            p=Player(peer.actor,index,peer,last=bytes(self.config['InputFixedSize']))
            self.players[p.actor]=p
            peer.quantum_request=self.request
            payload,_=simulation_start(self.request,self.seconds(),waiting_for_snapshot=bool(existing))
            self.send_protocol(p,payload)
            if not existing and not self.history:
                p.ready=True
            elif existing:
                p.snapshot_pending=True
                if self.snapshot_source is None:
                    source=min(existing,key=lambda x:x.actor)
                    self.snapshot_source=source.actor;self.snapshot_parts=[];self.snapshot_tick=None;self.snapshot_size=0
                    raw,_=pack('FrameSnapshotRequest',value=self.tick)
                    self.send_protocol(source,raw)
                    self.log('shared-snapshot-requested',source=source.actor,target=p.actor,reference_tick=self.tick)
            else:
                raise ValueError('No active replica is available to supply a snapshot')
            self.log('shared-simulation-start',actor=p.actor,index=index,waiting_snapshot=p.snapshot_pending,tick=self.tick)
            return p

    def ready_players(self):return [p for p in self.players.values() if p.ready]

    def _emit_frame(self,joins=None,removals=None,fallbacks=None):
        joins=joins or {};removals=removals or set();fallbacks=fallbacks or set();tick=self.tick+1
        entries=[None]*4
        for p in sorted(self.players.values(),key=lambda x:x.index):
            if p.actor in removals:
                p.active=False;p.inputs.clear();p.commands.clear()
            if not p.ready:continue
            if p.actor in joins:p.active=True
            if not p.active:continue
            item=p.inputs.pop(tick,None)
            if item is not None:
                p.last=item['data'];p.flags=item['flags']&1
                flags=item['flags']&1
                if p.fallback_active:self.log('shared-input-recovered',actor=p.actor,tick=tick)
                p.fallback_active=False;p.missing_since=None
            elif p.actor in fallbacks:
                # A stalled player supplies no new intent. Never hold a stale
                # attack/movement button or repeat the previous frame's RPC.
                p.last=bytes(self.config['InputFixedSize']);p.flags=0;flags=4
            else:
                if not p.flags&1:p.last=bytes(self.config['InputFixedSize'])
                flags=(p.flags&1)|4
            rpc=joins.get(p.actor)
            if rpc is None and p.commands:
                rpc=p.commands.popleft();flags|=8
            if p.actor in joins:flags=1
            entries[p.index]=(p.last,flags,rpc)
        ping=max((getattr(p.peer,'max_ping',10) for p in self.players.values() if p.active),default=10)
        payload,_=input_frame(tick,entries,input_size=self.config['InputFixedSize'],server_time=self.seconds(),max_ping=ping)
        self.tick=tick;self.history[tick]=payload;self.last_frame_at=self.clock()
        while len(self.history)>3600:self.history.popitem(last=False)
        failed=[]
        for p in list(self.ready_players()):
            try:p.peer.send_packet(event(102,{245:payload,254:0}))
            except OSError:failed.append(p.actor)
        for actor in failed:
            self.log('shared-replica-send-failed',actor=actor)
            self.leave(actor)
        if tick%60==0:self.log('shared-input-progress',tick=tick,players=sum(p.active for p in self.players.values()))
        return payload

    def add_player(self,actor,data,slot=0):
        if slot!=0:raise ValueError('Only one local player per client is supported')
        if not data or len(data)>65527:raise ValueError('Invalid runtime player data')
        with self.lock:
            p=self.players[actor]
            if p.active:raise ValueError('Player is already active')
            p.add_data=data
            if not p.ready:return
            self._activate(p)

    def _activate(self,p):
        p.activated_at=self.clock()
        rpc=struct.pack('<ii',p.actor,0)+p.add_data
        p.horizon=max(p.horizon,self.tick+2)
        self._emit_frame(joins={p.actor:rpc})
        self._emit_frame()
        self.log('shared-player-added',actor=p.actor,index=p.index,tick=self.tick-1)

    def submit(self,actor,inputs):
        with self.lock:
            p=self.players.get(actor)
            if p is None:return
            if not p.ready or not p.active:return
            if inputs:
                p.last_input_at=self.clock()
                p.last_received_tick=max(item['tick'] for item in inputs)
            previous_horizon=p.horizon
            for item in inputs:
                if item['slot']!=0 or len(item['data'])!=self.config['InputFixedSize']:
                    raise ValueError('Invalid player input')
                tick=item['tick']
                if tick<=self.tick:continue
                if tick>self.tick+240:raise ValueError('Input exceeds prediction window')
                previous=p.inputs.get(tick)
                if previous is not None and (previous['data']!=item['data'] or previous['flags']!=item['flags']):
                    # Quantum can revise predicted input before the tick is
                    # confirmed. Only the newest unconfirmed value is ordered;
                    # an already broadcast frame is never rewritten.
                    self.log('shared-pending-input-revised',actor=actor,tick=tick)
                p.inputs[tick]=item;p.horizon=max(p.horizon,tick)
            if p.horizon>previous_horizon:p.last_forward_at=self.clock()
            self._advance()

    def _advance(self):
        for _ in range(240):
            active=[p for p in self.players.values() if p.active and p.ready]
            if not active or max(p.horizon for p in active)<=self.tick:
                self.fallback_next_at=None;return
            blocked=[p for p in active if p.horizon<=self.tick]
            now=self.clock()
            for p in active:
                if p not in blocked:p.missing_since=None
                elif p.missing_since is None:p.missing_since=now
            if blocked:
                if not self.input_wait_timeout:return
                for p in blocked:
                    # Keep the existing initial scene-loading grace. A later
                    # connection awaiting its snapshot is not active yet.
                    if p.last_forward_at is None and now-p.activated_at<max(120.0,self.stall_timeout):return
                    if not p.fallback_active and now-p.missing_since<self.input_wait_timeout:return
                # A backlog of healthy inputs must not fast-forward the game
                # when the first missing-input deadline expires.
                interval=1.0/self.config['UpdateFPS']
                if self.fallback_next_at is None:self.fallback_next_at=now
                if now+1e-9<self.fallback_next_at:return
                # Retain fractional credit across timer jitter; catch up at
                # most a few frames after a delayed scheduler wake-up.
                self.fallback_next_at=max(self.fallback_next_at,now-4*interval)+interval
                for p in blocked:
                    if not p.fallback_active:
                        p.fallback_active=True
                        self.log('shared-input-fallback',actor=p.actor,tick=self.tick+1,
                                 wait_seconds=round(now-p.missing_since,3))
                self._emit_frame(fallbacks={p.actor for p in blocked})
            else:
                self.fallback_next_at=None
                self._emit_frame()

    def service_inputs(self):
        """Expire input waits even when the client handlers receive no packets."""
        if not self.lock.acquire(blocking=False):return
        try:
            if not self.closed:self._advance()
        finally:self.lock.release()

    def command(self,actor,data,slot=0):
        with self.lock:
            p=self.players[actor]
            if slot!=0:raise ValueError('Command has no local player slot')
            if not p.active and not p.snapshot_pending:return
            if len(p.commands)>=512:raise ValueError('Command queue exceeded')
            p.commands.append(data)

    def remove_player(self,actor,slot=-1):
        if slot not in (-1,0):raise ValueError('Unexpected removal slot')
        with self.lock:
            p=self.players.get(actor)
            if p is None or not p.active:return
            self._emit_frame(removals={actor});self._emit_frame();self._advance()
            self.log('shared-player-removed',actor=actor,tick=self.tick)

    def leave(self,actor):
        with self.lock:
            p=self.players.get(actor)
            if p is not None:p.ready=False
            self.remove_player(actor)
            self.players.pop(actor,None)
            if not self.players:
                self.closed=True
            if self.snapshot_source==actor:
                # No stale snapshot may be silently assigned to another source.
                self.snapshot_source=None;self.snapshot_parts=[]
                for p in self.players.values():
                    if p.snapshot_pending:
                        raw,_=pack('Disconnect',reason='Snapshot source left; reconnect to the realm')
                        try:self.send_protocol(p,raw)
                        except OSError:pass

    def snapshot(self,actor,message):
        with self.lock:
            if actor!=self.snapshot_source:raise ValueError('Unrequested snapshot source')
            tick,total=message['tick'],message['total_size'];data=message['data']
            if data is None or not 0<total<=64*1024*1024 or tick>self.tick:
                raise ValueError('Invalid snapshot dimensions')
            if not self.snapshot_parts:self.snapshot_tick=tick;self.snapshot_size=total
            if tick!=self.snapshot_tick or total!=self.snapshot_size:raise ValueError('Snapshot changed in transit')
            self.snapshot_parts.append(message)
            received=sum(len(p['data']) for p in self.snapshot_parts)
            if received>total:raise ValueError('Snapshot exceeds declared length')
            if not message['last']:return
            if received!=total:raise ValueError('Incomplete final snapshot')
            if self.history and tick<next(iter(self.history))-1:raise ValueError('Snapshot fell outside retained input history')
            waiting=[p for p in self.players.values() if p.snapshot_pending]
            delivered=[]
            for p in waiting:
                try:
                    for part in self.snapshot_parts:
                        raw,_=pack('FrameSnapshot',tick=part['tick'],total_size=part['total_size'],last=part['last'],data=part['data'])
                        self.send_protocol(p,raw)
                    for n,raw in self.history.items():
                        if n>tick:p.peer.send_packet(event(102,{245:raw,254:0}))
                except OSError:
                    self.log('shared-snapshot-send-failed',actor=p.actor)
                    self.leave(p.actor);continue
                p.snapshot_pending=False;p.ready=True;p.horizon=self.tick
                delivered.append(p)
                self.log('shared-snapshot-delivered',target=p.actor,tick=tick,bytes=received,catchup_to=self.tick)
            self.snapshot_source=None;self.snapshot_parts=[]
            for p in delivered:
                if p.add_data is not None:self._activate(p)
