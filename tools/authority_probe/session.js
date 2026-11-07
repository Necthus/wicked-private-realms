// Development probe for the verified v29466 runtime. No shipping client imports.
'use strict';
const authoritySessionObserver = Process.attachModuleObserver({onAdded(mod) {
  if (mod.name.toLowerCase() !== 'gameassembly.dll') return;
  const {invoke,pin,text,klass,unbox,objectNew,enterLockstep,exitLockstep,setFrameRate} = createAuthorityRuntime(mod);
  const zero=Memory.alloc(8);zero.writeU64(0);
  let stage='waiting', session=ptr(0), replica=ptr(0), last=0;
  let captured=null, capturedTick=null, cloneMatched=false, restoring=false, restoredTick=null;
  let replicaRestoredTick=null, replicaMatched=false;
  let targetRestoreTick=null;
  let inProbe=false;
  Interceptor.attach(mod.base.add(0x87191a0), {
    onEnter(args){this.owner=args[0];},
    onLeave(){
      if(captured!==null || stage!=='started'||!this.owner.equals(session))return;
      try {
        const quantum=this.owner.add(0x3d8).readPointer();
        const frame=invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',quantum,[]);
        const tick=frame.add(0x28).readS32();
        if(tick<300)return;
        captured=invoke('Quantum','Frame','Serialize',frame,[zero]);
        capturedTick=tick;
        const bytes=captured.add(0x18).readU64().toNumber();
        if(bytes<=0||bytes>64*1024*1024)throw new Error('Invalid serialized frame size: '+bytes);
        const checksum=invoke('Quantum','Frame','CalculateChecksum',frame,[]);
        const expected=unbox(checksum).readU64().toString(16);
        send({event:'authority-snapshot',tick,bytes,checksum:expected},captured.add(0x20).readByteArray(bytes));
        const game=this.owner.add(0x3d0).readPointer();
        const pool=game.add(0x410).readPointer().add(0x20).readPointer().add(0x20).readPointer();
        const ref=invoke('Quantum','FramePool','Alloc',pool,[]);
        try {
          const clone=invoke('Quantum','FramePool','GetFrame',pool,[unbox(ref)]);
          invoke('Quantum','Frame','Deserialize',clone,[captured]);
          const actual=unbox(invoke('Quantum','Frame','CalculateChecksum',clone,[])).readU64().toString(16);
          cloneMatched=actual===expected;
          send({event:'authority-snapshot-roundtrip',tick,restoredTick:clone.add(0x28).readS32(),expected,actual,matches:cloneMatched});
        } finally {
          invoke('Quantum','FramePool','DecrementRefCount',pool,[unbox(ref)]);
        }
      }catch(e){stage='failed';send({event:'authority-probe-error',error:String(e),stack:e.stack});}
    }
  });
  Interceptor.attach(mod.base.add(0x8720970), {
    onEnter(args){
      this.isReplica=!replica.isNull()&&args[0].readPointer().equals(replica.add(0x3d0).readPointer());
      if(this.isReplica&&targetRestoreTick!==null)args[2]=ptr(targetRestoreTick);
      this.requestedTick=args[2].toInt32();
      if(!this.isReplica)send({event:'authority-session-restoring',tick:this.requestedTick,sourceSnapshotTick:capturedTick});
    },
    onLeave(){if(!this.isReplica){restoredTick=this.requestedTick;send({event:'authority-session-restored',tick:restoredTick});}}
  });
  Interceptor.attach(mod.base.add(0x87255e0), {
    onEnter(args){this.vf=args[0];},
    onLeave(){
      if(inProbe || Date.now()-last<1000)return;
      last=Date.now();inProbe=true;
      try {
        const vf=this.vf;
        if(stage==='waiting') {
          const ready=vf.add(0x5c).readU8()!==0;
          const sm=vf.add(0x568).readPointer();
          send({event:'authority-preparing',initialized:ready,saveManager:!sm.isNull()});
          if(!ready||sm.isNull())return;
          stage='creating';
          setFrameRate(60, ptr(0));
          const account=invoke('Moon.Forsaken','SaveDataManager','get_LocalAccountId',sm,[]);
          const realmId=invoke('System','Guid','NewGuid',ptr(0),[]);
          const characterId=invoke('System','Guid','NewGuid',ptr(0),[]);
          const realmSave=invoke('Quantum','SavesAPI','CreateNewLocalRealm',ptr(0),[account,unbox(realmId)]);
          const charSave=invoke('Quantum','SavesAPI','CreateNewLocalCharacter',ptr(0),[account,unbox(characterId),text('AuthorityProbe')]);
          const realm=unbox(realmSave).add(0x60).readPointer();
          const character=unbox(charSave).add(0x120).readPointer();
          const definition=invoke('frameworks.session','SessionDefinition','CreateQuickplay',ptr(0),[realm,character,zero]);
          unbox(definition).add(0x278).writeU8(1);
          const handler=vf.add(0x328).readPointer();
          session=invoke('frameworks.session','SessionHandlerAPI','StartSession',handler,[unbox(definition)]);
          if(probeConfig.replicas===2){
            replica=invoke('frameworks.session','SessionHandlerAPI','StartSession',handler,[unbox(definition)]);
            send({event:'replica-created',boundView:false});
          }
          stage='started';
          send({event:'authority-session-created',headless:true,boundView:false,networkParticipants:0});
        } else if(stage==='started') {
          const state=session.add(0x10).readS32();
          const game=session.add(0x3d0).readPointer();
          const quantum=session.add(0x3d8).readPointer();
          let frame=null;
          if(!quantum.isNull()){
            const verified=invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',quantum,[]);
            if(!verified.isNull())frame=verified.add(0x28).readS32();
          }
          send({event:'authority-progress',state,game:!game.isNull(),quantum:!quantum.isNull(),frame,
                simulated:game.isNull()?null:game.add(0x2e8).readS64().toString()});
          if(captured!==null&&cloneMatched&&!restoring&&frame>=capturedTick+120){
            restoring=true;
            const snapshot=pin(objectNew(klass('Moon.Forsaken','GameStateSnapshot')));
            invoke('Moon.Forsaken','GameStateSnapshot','.ctor',snapshot,[]);
            snapshot.add(0x10).writePointer(text('AuthorityProbe'));
            snapshot.add(0x28).writePointer(captured);
            const sync=game.add(0x410).readPointer();
            const view=Memory.alloc(8);view.writePointer(sync);
            send({event:'authority-restore-request',lockstepState:sync.add(0x98).readS32()});
            enterLockstep(view,ptr(0));
            try {
              invoke('Moon.Forsaken','SnapshotViewAPI','RestoreSnapshot',ptr(0),[snapshot,session]);
            } finally {
              exitLockstep(view,ptr(0));
            }
          }
          if(restoredTick!==null&&frame>=restoredTick+120){
            if(!replica.isNull()){
              const replicaState=replica.add(0x10).readS32();
              if(replicaState!==4)throw new Error('Replica not running: '+replicaState);
              const targetGame=replica.add(0x3d0).readPointer();
              const targetSession=replica.add(0x3d8).readPointer();
              const sync=targetGame.add(0x410).readPointer();
              if(replicaRestoredTick===null){
                const view=Memory.alloc(8);view.writePointer(sync);
                enterLockstep(view,ptr(0));
                try {
                  const before=invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',targetSession,[]);
                  replicaRestoredTick=capturedTick;
                  const pool=sync.add(0x20).readPointer().add(0x20).readPointer();
                  const ref=invoke('Quantum','FramePool','Alloc',pool,[]);
                  let expected;
                  try {
                    const clone=invoke('Quantum','FramePool','GetFrame',pool,[unbox(ref)]);
                    invoke('Quantum','Frame','Deserialize',clone,[captured]);
                    clone.add(0x28).writeS32(replicaRestoredTick);
                    expected=unbox(invoke('Quantum','Frame','CalculateChecksum',clone,[])).readU64().toString(16);
                  } finally {invoke('Quantum','FramePool','DecrementRefCount',pool,[unbox(ref)]);}
                  const snapshot=pin(objectNew(klass('Moon.Forsaken','GameStateSnapshot')));
                  invoke('Moon.Forsaken','GameStateSnapshot','.ctor',snapshot,[]);
                  snapshot.add(0x10).writePointer(text('AuthorityReplicaProbe'));
                  snapshot.add(0x28).writePointer(captured);
                  targetRestoreTick=capturedTick;
                  invoke('Moon.Forsaken','SnapshotViewAPI','RestoreSnapshot',ptr(0),[snapshot,replica]);
                  const after=invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',targetSession,[]);
                  const actual=unbox(invoke('Quantum','Frame','CalculateChecksum',after,[])).readU64().toString(16);
                  replicaMatched=actual===expected;
                  send({event:'replica-state-verified',tick:replicaRestoredTick,sourceSnapshotTick:capturedTick,
                        expected,actual,matches:replicaMatched});
                  if(!replicaMatched)throw new Error('Replica state differs from authority snapshot');
                }finally{targetRestoreTick=null;exitLockstep(view,ptr(0));}
              }
              const targetFrame=invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',targetSession,[]);
              const targetTick=targetFrame.add(0x28).readS32();
              send({event:'replica-progress',state:replicaState,frame:targetTick});
              if(targetTick<replicaRestoredTick+120)return;
            }
            stage='complete';
            send({event:'authority-complete',frame,sourceSnapshotTick:capturedTick,restoredTick,cloneMatched,replicaMatched});
          }
        }
      } catch(e) {
        stage='failed';
        send({event:'authority-probe-error',error:String(e),stack:e.stack});
      } finally {inProbe=false;}
    }
  });
}});
