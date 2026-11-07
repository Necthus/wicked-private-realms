// Real serialized game frames traverse independent loopback streams before
// becoming replica state. The first replica loses three seconds of updates.
'use strict';
const streamObserver = Process.attachModuleObserver({onAdded(mod) {
  if(mod.name.toLowerCase()!=='gameassembly.dll')return;
  const rt=createAuthorityRuntime(mod);
  const {invoke,pin,text,klass,unbox,objectNew,enterLockstep,exitLockstep,setFrameRate,api,scoped}=rt;
  const arrayNew=api('il2cpp_array_new','pointer',['pointer','uint64']);
  const zero=Memory.alloc(8);zero.writeU64(0);
  const inputDriver=probeConfig.crossSource?createAuthorityInput(mod,rt):null;
  let authority=ptr(0),stage='waiting',lastProgress=0,lastSnapshot=-1;
  const replicas=[];
  const pending=new Map();
  let desiredRestore=null;
  let resetChecked=false;
  Interceptor.attach(mod.base.add(0x70de2f0),{onEnter(args){
    if(desiredRestore!==null){
      const matches=args[0].equals(desiredRestore.quantum);
      if(!resetChecked||!matches)send({event:'stream-reset-target',matches,actual:args[0].toString(),expected:desiredRestore.quantum.toString()});
      resetChecked=true;
      if(!matches)send({event:'authority-probe-error',error:'Snapshot restore reset the wrong deterministic session'});
    }
  }});
  Interceptor.attach(mod.base.add(0x70e9080),{
    onEnter(args){this.provider=args[0];},
    onLeave(){
      if(desiredRestore!==null&&this.provider.add(0x50).readPointer().equals(desiredRestore.quantum)){
        invoke('Photon.Deterministic','RawInputCache','Clear',this.provider.add(0x58).readPointer(),[]);
      }
    }
  });
  let viewScheduled=false;
  let viewReady=false;
  let boundInputGame=ptr(0);
  let sharedDefinition=null;
  const joinRequests=[];
  const peerDefinitions={};
  let checkpointRestored=!probeConfig.restoreCheckpoint;
  if(probeConfig.restoreCheckpoint)rt.installCheckpoint(()=>authority,frame=>{
    rt.configurePresence(authority.add(0x3d8).readPointer(),frame,0);
    viewReady=!probeConfig.hostPlayer;checkpointRestored=true;
  });
  if(probeConfig.restoreCheckpoint){
    const insertOriginal=new NativeFunction(mod.base.add(0x871bd10),'void',['pointer','pointer'],{exceptions:'propagate'});
    const insert=new NativeCallback((self,method)=>{
      if(self.equals(authority)){self.add(0x3a8).writeU8(1);return;}
      insertOriginal(self,method);
    },'void',['pointer','pointer']);
    keep.push(insert);Interceptor.replace(mod.base.add(0x871bd10),insert);
  }
  if(probeConfig.crossSource){
    function receiveJoin(){recv('authority-add-player',message=>{joinRequests.push(message.payload);receiveJoin();});}
    receiveJoin();
    Interceptor.attach(mod.base.add(0x70dbe40),{onEnter(args){this.arguments=args[1];},onLeave(){
      scoped(()=>{
        const config=this.arguments.add(0x40).readPointer();
        const bytes=config.add(0x18).readU64().toNumber();
        send({event:'authority-runtime-config',bytes},config.add(0x20).readByteArray(bytes));
        const serialized=invoke('Photon.Deterministic','DeterministicSessionConfig','ToByteArray',ptr(0),[this.arguments.add(8).readPointer()]);
        const length=serialized.add(0x18).readU64().toNumber();
        send({event:'authority-session-config',bytes:length},serialized.add(0x20).readByteArray(length));
      });
    }});
  }
  let playerInserted=false;
  let inputPolls=0;
  if(probeConfig.crossSource)Interceptor.attach(mod.base.add(0x5bf6470),{onEnter(args){
    const slot=args[2].toInt32();
    if(slot>0&&!authority.isNull()&&args[0].equals(authority.add(0x3d0).readPointer())){
      // The authority ingests remote input slots; its bootstrap view owns only slot zero.
      const remove=new NativeFunction(mod.base.add(0x70daf10),'void',['pointer','int','pointer']);
      remove(authority.add(0x3d8).readPointer().add(0x10).readPointer(),slot,ptr(0));
    }
  }});
  const localInputOriginal=new NativeFunction(mod.base.add(0x9274400),'void',['pointer','pointer','pointer'],{exceptions:'propagate'});
  const localInput=new NativeCallback((self,callback,method)=>{
    if(!boundInputGame.isNull()&&callback.add(0x10).readPointer().equals(boundInputGame)&&callback.add(0x24).readS32()===0)
      localInputOriginal(self,callback,method);
  },'void',['pointer','pointer','pointer']);
  keep.push(localInput);Interceptor.replace(mod.base.add(0x9274400),localInput);
  Interceptor.attach(mod.base.add(0x5bf2990),{onEnter(args){
    const integration=args[1].add(0x10).readPointer();
    if(probeConfig.crossSource&&!integration.isNull())integration.add(0x188).writeS32(0);
    if(probeConfig.player&&!probeConfig.viewAuthority&&!integration.isNull()&&!authority.isNull()&&
        args[0].equals(authority.add(0x3d0).readPointer()))integration.add(0x18c).writeS32(2);
    send({event:'stream-add-player',game:args[0].toString(),
      integration:integration.toString(),rebind:args[1].add(0x18).readS32(),
      spawnMode:integration.isNull()?null:integration.add(0x18c).readS32()});
  }});
  Interceptor.attach(mod.base.add(0x5bf9a10),{
    onEnter(args){this.output=args[0];this.slot=args[3].toInt32();this.source=probeConfig.player&&!authority.isNull()&&
      args[1].equals(authority.add(0x3d0).readPointer())&&(probeConfig.crossSource||this.slot===0);},
    onLeave(){if(this.source){
      // A playable host keeps the game's native slot-zero keyboard/controller input.
      if(inputDriver&&!(probeConfig.hostPlayer&&this.slot===0))inputDriver.apply(this.output,authority.add(0x3d0).readPointer(),this.slot);
      else if(!(probeConfig.hostPlayer&&this.slot===0)){this.output.add(0x1c).writeS32(1);this.output.add(0x110).writeU8(1);}
      inputPolls++;
    }}
  });
  function receiveState(){recv('authoritative-state',(message,data)=>{
    const meta=message.payload;
    if(![1,2].includes(meta.replica)||!Number.isInteger(meta.tick)||meta.tick<0||data===null){
      send({event:'authority-probe-error',error:'Invalid relay message'});return;
    }
    const old=pending.get(meta.replica);
    if(!old||old.meta.tick<meta.tick)pending.set(meta.replica,{meta,data});
    receiveState();
  });}
  receiveState();
  function frameOf(owner){
    return invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',owner.add(0x3d8).readPointer(),[]);
  }
  const originalUpdate=new NativeFunction(mod.base.add(0x8718590),'void',['pointer','pointer'],{exceptions:'propagate'});
  const update=new NativeCallback((self,method)=>{
    const quantum=self.add(0x10).readPointer();
    const slow=replicas[0];
    if(slow&&quantum.equals(slow.session.add(0x3d8).readPointer())&&slow.lastTick!==null){
      const frame=quantum.add(0x108).readPointer().add(0x10).readPointer().add(0x10).readPointer();
      if(!frame.isNull()&&frame.add(0x28).readS32()>=slow.lastTick+120){
        if(!slow.stalled){slow.stalled=true;send({event:'stream-replica-stalled',replica:1,tick:frame.add(0x28).readS32()});}
        return;
      }
    }
    originalUpdate(self,method);
  },'void',['pointer','pointer']);
  keep.push(update);
  Interceptor.replace(mod.base.add(0x8718590),update);
  Interceptor.attach(mod.base.add(0x8720970),{onEnter(args){
    if(desiredRestore!==null&&args[0].readPointer().equals(desiredRestore.game))args[2]=ptr(desiredRestore.tick);
  }});
  Interceptor.attach(mod.base.add(0x87191a0),{
    onEnter(args){this.owner=args[0];},
    onLeave(){
      if(stage!=='running'||!checkpointRestored||!this.owner.equals(authority))return;
      try{scoped(()=>{
        const frame=frameOf(authority),tick=frame.add(0x28).readS32();
        if(tick<300||tick-lastSnapshot<6)return;
        lastSnapshot=tick;
        const data=invoke('Quantum','Frame','Serialize',frame,[zero]);
        const bytes=data.add(0x18).readU64().toNumber();
        if(bytes<=0||bytes>64*1024*1024)throw new Error('Invalid stream snapshot size');
        const checksum=unbox(invoke('Quantum','Frame','CalculateChecksum',frame,[])).readU64().toString(16);
        const extra=probeConfig.crossSource?{player:rt.playerState(frame),input:inputDriver.stats(),
          bootstrap:{definition:sharedDefinition,peers:peerDefinitions},
          players:[0,1].map(slot=>({slot,...rt.playerState(frame,slot+1),input:inputDriver.stats(slot)}))}:{};
        send({event:'stream-snapshot',tick,bytes,checksum,...extra},data.add(0x20).readByteArray(bytes));
      });}catch(e){stage='failed';send({event:'authority-probe-error',error:String(e)});}
    }
  });
  let busy=false;
  Interceptor.attach(mod.base.add(0x87255e0),{
    onEnter(args){this.vf=args[0];},
    onLeave(){
      if(busy)return;busy=true;
      try{scoped(()=>{
        const vf=this.vf;
        if(stage==='waiting'){
          if(!vf.add(0x5c).readU8()||vf.add(0x568).readPointer().isNull())return;
          stage='creating';setFrameRate(60,ptr(0));
          const def=rt.createDefinition(vf,'AuthorityStreamProbe',probeConfig.player,probeConfig.sharedDefinition||null);
          if(probeConfig.crossSource){
            sharedDefinition=rt.describeDefinition(def);
            Object.assign(peerDefinitions,probeConfig.peerDefinitions||{0:sharedDefinition});
            send({event:'authority-definition',definition:sharedDefinition});
            for(const [slot,definition] of Object.entries(peerDefinitions)){
              if(Number(slot)>0)send({event:'authority-peer-definition',slot:Number(slot),definition,restored:true});
            }
          }
          const handler=vf.add(0x328).readPointer();
          authority=invoke('frameworks.session','SessionHandlerAPI','StartSession',handler,[unbox(def)]);
          for(let index=1;index<=(probeConfig.authorityOnly?0:2);index++)replicas.push({
            id:index,session:invoke('frameworks.session','SessionHandlerAPI','StartSession',handler,[unbox(def)]),
            lastTick:null,applied:0,stalled:false,healthyDuringFault:0,recovered:false
          });
          if(probeConfig.viewAuthority){
            invoke('Moon.GameSession','ClientFlowAPI','ProceedToGame',vf.add(0x4e0).readPointer(),[authority]);
          }
          stage='running';send({event:'stream-sessions-created',replicas:replicas.length,boundViews:0});return;
        }
        if(stage!=='running'||authority.add(0x10).readS32()!==4)return;
        const sourceFrame=frameOf(authority),sourceTick=sourceFrame.add(0x28).readS32();
        if(probeConfig.viewAuthority)boundInputGame=authority.add(0x3d0).readPointer();
        if(inputDriver&&inputDriver.hasCommands()){
          const view=Memory.alloc(8);view.writePointer(authority.add(0x3d0).readPointer().add(0x410).readPointer());
          rt.enterLockstep(view,ptr(0));
          try{inputDriver.drain(authority.add(0x3d8).readPointer());}
          finally{rt.exitLockstep(view,ptr(0));}
        }
        if(probeConfig.crossSource&&viewReady&&joinRequests.length){
          const request=joinRequests.shift(),game=authority.add(0x3d0).readPointer();
          const quantum=authority.add(0x3d8).readPointer();
          const capacity=quantum.add(0x30).readPointer().add(0x10).readS32();
          if(!Number.isInteger(request.slot)||request.slot<1||request.slot>=capacity||!/^[0-9a-f]{32}$/.test(request.character))
            throw new Error('Invalid authority player slot or capacity: '+request.slot+'/'+capacity);
          const def=rt.createDefinition(vf,'AuthorityPeer'+request.slot,true,{...sharedDefinition,character:request.character});
          peerDefinitions[request.slot]=rt.describeDefinition(def);
          const data=rt.createPlayerData(def,true),slot=Memory.alloc(4);slot.writeS32(request.slot);
          const view=Memory.alloc(8);view.writePointer(game.add(0x410).readPointer());
          rt.enterLockstep(view,ptr(0));
          try{invoke('Quantum','QuantumGame','AddPlayer',game,[slot,data]);}
          finally{rt.exitLockstep(view,ptr(0));}
          send({event:'authority-peer-definition',slot:request.slot,definition:rt.describeDefinition(def),capacity});
        }
        if(probeConfig.player&&!probeConfig.viewAuthority&&!playerInserted&&sourceTick>=150){
          playerInserted=true;
          invoke('frameworks.session','Session','InsertPlayer',authority,[]);
          send({event:'stream-player-requested',tick:sourceTick});
        }
        if(probeConfig.bindView&&!viewScheduled&&sourceTick>=420){
          viewScheduled=true;
          const target=replicas[1].session;
          boundInputGame=target.add(0x3d0).readPointer();
          const flow=vf.add(0x4e0).readPointer();
          invoke('Moon.GameSession','ClientFlowAPI','ProceedToGame',flow,[target]);
          send({event:'stream-view-scheduled',replica:2});
        }
        for(const replica of replicas){
          const incoming=pending.get(replica.id);
          if(!incoming||replica.session.add(0x10).readS32()!==4)continue;
          pending.delete(replica.id);
          if(replica.lastTick!==null&&incoming.meta.tick<=replica.lastTick)continue;
          const bytes=pin(arrayNew(klass('System','Byte'),incoming.data.byteLength));
          bytes.add(0x20).writeByteArray(incoming.data);
          const snapshot=pin(objectNew(klass('Moon.Forsaken','GameStateSnapshot')));
          invoke('Moon.Forsaken','GameStateSnapshot','.ctor',snapshot,[]);
          snapshot.add(0x28).writePointer(bytes);
          const game=replica.session.add(0x3d0).readPointer();
          const view=Memory.alloc(8);view.writePointer(game.add(0x410).readPointer());
          enterLockstep(view,ptr(0));
          try{
            desiredRestore={tick:incoming.meta.tick,game,quantum:replica.session.add(0x3d8).readPointer(),thread:Process.getCurrentThreadId()};
            invoke('Moon.Forsaken','SnapshotViewAPI','RestoreSnapshot',ptr(0),[snapshot,replica.session]);
            const frame=frameOf(replica.session);
            const actual=unbox(invoke('Quantum','Frame','CalculateChecksum',frame,[])).readU64().toString(16);
            if(actual!==incoming.meta.checksum||frame.add(0x28).readS32()!==incoming.meta.tick){
              throw new Error('Stream replica '+replica.id+' differs from authority tick '+incoming.meta.tick+
                ': actualTick='+frame.add(0x28).readS32()+', expectedChecksum='+incoming.meta.checksum+', actualChecksum='+actual);
            }
            replica.lastTick=incoming.meta.tick;replica.applied++;
            if(replica.id===2&&incoming.meta.tick>=600&&incoming.meta.tick<780)replica.healthyDuringFault++;
            if(replica.id===1&&incoming.meta.tick>=780&&replica.stalled)replica.recovered=true;
            send({event:'stream-state-verified',replica:replica.id,tick:incoming.meta.tick,checksum:actual,matches:true});
          }finally{desiredRestore=null;exitLockstep(view,ptr(0));}
        }
        if(Date.now()-lastProgress>=1000){
          const viewState=unbox(invoke('frameworks.session','SessionViewBinding','get_State',
            invoke('frameworks.session','SessionViewBinding','get_Instance',ptr(0),[]),[])).readS32();
          const clientFlow=vf.add(0x4d8).readPointer().add(0x10).readS32();
          if(!viewReady&&viewState===11&&clientFlow===4){
            const heroView=Memory.alloc(8);heroView.writePointer(ptr(0));
            const bound=unbox(invoke('Moon.ViewSynchronization.Player','PlayerViewsAPI','TryGetBoundHeroView',
              vf.add(0x70).readPointer(),[heroView])).readU8()!==0&&!heroView.readPointer().isNull();
            if(bound){viewReady=true;send({event:'stream-view-ready',clientFlow,boundHero:true});}
          }
          const playerRef=Memory.alloc(4);playerRef.writeS32(1);
          const hero=unbox(invoke('Quantum','Frame','GetPlayerHero',sourceFrame,[playerRef]));
          const playerData=invoke('Quantum','Frame','GetPlayerData',sourceFrame,[playerRef]);
          const players=[];
          for(let id=1;id<=4;id++){
            playerRef.writeS32(id);
            const h=unbox(invoke('Quantum','Frame','GetPlayerHero',sourceFrame,[playerRef]));
            const d=invoke('Quantum','Frame','GetPlayerData',sourceFrame,[playerRef]);
            players.push({id,hero:h.readU64().toString(16),data:!d.isNull(),
              integration:d.isNull()?null:d.add(0x10).readPointer().toString()});
          }
          lastProgress=Date.now();send({event:'stream-authority-progress',tick:sourceTick,
            players,
            hero:hero.readU64().toString(16),inputPolls,
            playerData:playerData.isNull()?null:{integration:playerData.add(0x10).readPointer().toString(),rebind:playerData.add(0x18).readS32()},
            usedEntities:sourceFrame.add(0x88).readS32(),freeEntities:sourceFrame.add(0x8c).readS32(),
            activePlayers:unbox(invoke('Quantum','Frame','get_ActivePlayerCount',sourceFrame,[])).readS32(),
            viewState,clientFlow,
            replicas:replicas.map(x=>({id:x.id,lastTick:x.lastTick,applied:x.applied,stalled:x.stalled,recovered:x.recovered}))});
        }
        if(sourceTick>=1200){
          if(probeConfig.authorityOnly&&sourceTick>=(probeConfig.stopTick||(probeConfig.viewAuthority?3600:1200))){stage='complete';send({event:'stream-complete',tick:sourceTick});return;}
          if(probeConfig.authorityOnly)return;
          if(replicas[0].recovered&&replicas[1].healthyDuringFault>=5&&replicas.every(x=>x.applied>=30)){
            stage='complete';send({event:'stream-complete',tick:sourceTick,healthyDuringFault:replicas[1].healthyDuringFault});
          }else if(sourceTick>=1800)throw new Error('Stream recovery criteria were not met');
        }
      });}catch(e){stage='failed';send({event:'authority-probe-error',error:String(e),stack:e.stack});}
      finally{busy=false;}
    }
  });
}});
