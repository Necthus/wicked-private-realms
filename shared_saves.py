"""Commit deterministic multiplayer saves only after participant agreement."""
from dataclasses import dataclass,field
import hashlib
import secrets
import threading
from photon_wire import event
from save_wire import Cycle,cycle_info,chunk,finish_request,finish_response,backend_result


@dataclass
class Record:
    cycle:Cycle
    peers:dict=field(default_factory=dict)
    accounts:dict=field(default_factory=dict)
    requested:bool=False
    notified:set=field(default_factory=set)
    @property
    def committed(self):return self.cycle.committed


class SharedSaves:
    def __init__(self,game,realm,timeline,log=lambda *a,**kw:None):
        self.game=game;self.realm=realm;self.timeline=timeline;self.log=log
        self.session=secrets.token_hex(32);self.records={};self.lock=threading.RLock()
        self.known_characters=set()

    def can_leave(self,actor):
        with self.lock:
            return all(r.committed for r in self.records.values() if actor in r.cycle.info['actors'])

    def reply(self,peer,index,kind=0,ticks=0,mask=()):
        peer.send_packet(event(13,{0:finish_response(index,kind,ticks,mask),254:0}))
        if kind==0:peer.send_packet(event(8,{0:backend_result(index,True,ticks),254:0}))

    def message(self,peer,code,data):
        with self.lock:
            if code==11:
                info=cycle_info(data);index=info['index']
                if peer.actor not in info['actors']:raise ValueError('Save sender is not a declared participant')
                accounts={}
                for actor in info['actors']:
                    player=self.timeline.players.get(actor)
                    if player is None or not player.active:raise ValueError('Save participant is not active in this simulation')
                    accounts[actor]=player.peer.identity['account']
                record=self.records.get(index)
                if record is None:
                    if len(self.records)>=8:
                        done=[n for n,r in self.records.items() if r.committed]
                        if not done:raise ValueError('Too many pending multiplayer saves')
                        self.records.pop(min(done))
                    record=Record(Cycle(info,{}),accounts=accounts);self.records[index]=record
                elif record.cycle.info!=info or record.accounts!=accounts:
                    raise ValueError('Players disagree on save checksums, participants, or save dimensions')
                record.peers[peer.actor]=peer
                self.log('shared-save-declaration',cycle=index,actor=peer.actor,participants=info['actors'],provider=info['actor'])
                self.attempt(index)
            elif code==10:
                index,total,sequence,payload=chunk(data);record=self.records.get(index)
                if record is None or record.cycle.info['actor']!=peer.actor:raise ValueError('Only the declared provider can upload save chunks')
                record.cycle.add(total,sequence,payload)
                self.attempt(index)
            elif code==12:
                index=finish_request(data);record=self.records.get(index)
                if record is None or peer.actor not in record.cycle.info['actors']:raise ValueError('Unknown save-cycle finish request')
                record.requested=True
                if not record.cycle.complete():
                    self.reply(peer,index,kind=1,mask=record.cycle.mask())
                else:self.attempt(index)
            else:raise ValueError('Unknown save message')

    def attempt(self,index):
        record=self.records[index];cycle=record.cycle
        if not record.requested or not cycle.complete():return
        if set(record.peers)!=set(cycle.info['actors']):return
        if not cycle.committed:
            raw,save=cycle.data(known_retired_characters=self.known_characters);provider=record.accounts[cycle.info['actor']]
            cycle.ticks=self.game.commit_transport_save(provider,self.realm,self.session,index,raw,save,
                                                        authorized_accounts=set(record.accounts.values()))
            cycle.committed=True
            self.known_characters.update(cycle.info['characters'])
            self.log('shared-save-committed',cycle=index,participants=cycle.info['actors'],bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        for actor,peer in record.peers.items():
            if actor not in record.notified:
                self.reply(peer,index,ticks=cycle.ticks,mask=cycle.mask())
                record.notified.add(actor)
