// Independent-process receiver for development experiments. Not a release client.
'use strict';
const receiverObserver=Process.attachModuleObserver({onAdded(mod){
  if(mod.name.toLowerCase()!=='gameassembly.dll')return;
  const rt=createAuthorityRuntime(mod);
  const {invoke,unbox,pin,klass,api,objectNew,scoped}=rt;
  const arrayNew=api('il2cpp_array_new','pointer',['pointer','uint64']);
  const encodeDirection=new NativeFunction(mod.base.add(0x7211d30),'uint',['pointer','pointer']);
  const getPlayerInput=new NativeFunction(mod.base.add(0x5977890),'pointer',['pointer','int','pointer']);
  const ownedSlot=probeConfig.playerSlot||0;
  const remoteInputs=new Map();
  let session=ptr(0),stage='waiting',pending=null,lastTick=-1,applied=0,lastProgress=0;
  let bound=false,viewReady=false,desiredRestore=null,sequence=0,predictionStalled=false;
  let boundAt=null;
  let remapped=false;
  let commandSequence=0;
  Interceptor.attach(mod.base.add(0x70e9ec0),{onEnter(args){
    if(lastTick<0||args[3].toInt32()===0||session.isNull()||
        !args[0].equals(session.add(0x3d8).readPointer().add(0x58).readPointer()))return;
    const data=args[2],bytes=data.add(0x18).readU64().toNumber();
    if(bytes<=0||bytes>1024*1024){send({event:'authority-probe-error',error:'Invalid client command size'});return;}
    args[1]=ptr(ownedSlot);
    send({event:'receiver-command',sequence:commandSequence++,bytes,slot:ownedSlot},data.add(0x20).readByteArray(bytes));
  }});
  const localInputOriginal=new NativeFunction(mod.base.add(0x9274400),'void',['pointer','pointer','pointer'],{exceptions:'propagate'});
  const localInput=new NativeCallback((self,callback,method)=>{
    const slot=callback.add(0x24).readS32();
    if(slot!==ownedSlot)return;
    callback.add(0x24).writeS32(0);
    try{localInputOriginal(self,callback,method);}finally{callback.add(0x24).writeS32(slot);}
  },'void',['pointer','pointer','pointer']);
  keep.push(localInput);Interceptor.replace(mod.base.add(0x9274400),localInput);
  function receive(){recv('authoritative-state',(message,data)=>{
    if(!data||data.byteLength<=0||data.byteLength>64*1024*1024||!Number.isInteger(message.payload.tick)){
      send({event:'authority-probe-error',error:'Invalid received authoritative state'});return;
    }
    if(!pending||pending.meta.tick<message.payload.tick)pending={meta:message.payload,data};
    receive();
  });}
  receive();
  const insertOriginal=new NativeFunction(mod.base.add(0x871bd10),'void',['pointer','pointer'],{exceptions:'propagate'});
  const insert=new NativeCallback((self,method)=>{
    if(self.equals(session)){self.add(0x3a8).writeU8(1);return;}
    insertOriginal(self,method);
  },'void',['pointer','pointer']);
  keep.push(insert);Interceptor.replace(mod.base.add(0x871bd10),insert);
  Interceptor.attach(mod.base.add(0x8720970),{onEnter(args){
    if(desiredRestore!==null&&args[0].readPointer().equals(desiredRestore.game))args[2]=ptr(desiredRestore.tick);
  }});
  const updateOriginal=new NativeFunction(mod.base.add(0x8718590),'void',['pointer','pointer'],{exceptions:'propagate'});
  const update=new NativeCallback((self,method)=>{
    if(lastTick>=0&&!session.isNull()&&self.add(0x10).readPointer().equals(session.add(0x3d8).readPointer())){
      const frame=session.add(0x3d0).readPointer().add(0x10).readPointer().add(0x10).readPointer();
      if(!frame.isNull()&&frame.add(0x28).readS32()>=lastTick+120){
        if(!predictionStalled){predictionStalled=true;send({event:'receiver-stalled',tick:frame.add(0x28).readS32(),lastTick});}
        return;
      }
    }
    updateOriginal(self,method);
  },'void',['pointer','pointer']);
  keep.push(update);Interceptor.replace(mod.base.add(0x8718590),update);
  Interceptor.attach(mod.base.add(0x5bf9a10),{
    onEnter(args){this.out=args[0];this.slot=args[3].toInt32();this.ours=!session.isNull()&&args[1].equals(session.add(0x3d0).readPointer());},
    onLeave(){
      if(!this.ours)return;
      if(this.slot!==ownedSlot){
        const remote=remoteInputs.get(this.slot);
        if(remote)this.out.writeByteArray(remote);
        else this.out.writeByteArray(new Uint8Array(0x110));
        this.out.add(0x1c).writeS32(1);this.out.add(0x110).writeU8(1);
        return;
      }
      if(!viewReady||probeConfig.syntheticMovement===false)return;
      const direction=Memory.alloc(16);
      direction.writeS64(0);direction.add(8).writeS64(0);
      // A reproducible walk around the spawn point, generated in the client.
      const phase=Math.floor((lastTick-boundAt)/120)%4;
      if(phase===0)direction.writeS64(65536);
      else if(phase===1)direction.add(8).writeS64(65536);
      else if(phase===2)direction.writeS64(-65536);
      else direction.add(8).writeS64(-65536);
      this.out.add(8).writeU32(encodeDirection(direction,ptr(0)));
      this.out.add(0x1c).writeS32(1);this.out.add(0x110).writeU8(1);
    }
  });
  Interceptor.attach(mod.base.add(0x5bf4650),{
    onEnter(args){this.out=args[0];this.ours=!session.isNull()&&args[1].equals(session.add(0x3d0).readPointer())&&args[3].toInt32()===ownedSlot;},
    onLeave(){
      if(!this.ours||!viewReady)return;
      const length=this.out.add(0x18).readS32(),data=this.out.add(0x10).readPointer();
      if(length!==47||data.isNull()){
        send({event:'authority-probe-error',error:'Unexpected native input wire size: '+length});return;
      }
      send({event:'receiver-input',sequence:sequence++,tick:this.out.readS32(),bytes:length},data.add(0x20).readByteArray(length));
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
          stage='creating';rt.setFrameRate(60,ptr(0));
          const definition=rt.createDefinition(vf,'AuthorityStreamProbe',true,probeConfig.sharedDefinition);
          session=invoke('frameworks.session','SessionHandlerAPI','StartSession',vf.add(0x328).readPointer(),[unbox(definition)]);
          stage='running';send({event:'receiver-created'});return;
        }
        if(stage!=='running'||session.add(0x10).readS32()!==4)return;
        const quantum=session.add(0x3d8).readPointer(),game=session.add(0x3d0).readPointer();
        const current=invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',quantum,[]);
        if(current.add(0x28).readS32()<150)return;
        const playerMap=quantum.add(0x10).readPointer();
        if(pending&&pending.meta.tick>lastTick){
          const incoming=pending;pending=null;
          const bytes=pin(arrayNew(klass('System','Byte'),incoming.data.byteLength));
          bytes.add(0x20).writeByteArray(incoming.data);
          const snapshot=pin(objectNew(klass('Moon.Forsaken','GameStateSnapshot')));
          invoke('Moon.Forsaken','GameStateSnapshot','.ctor',snapshot,[]);snapshot.add(0x28).writePointer(bytes);
          const view=Memory.alloc(8);view.writePointer(game.add(0x410).readPointer());
          rt.enterLockstep(view,ptr(0));
          try{
            desiredRestore={game,tick:incoming.meta.tick};
            invoke('Moon.Forsaken','SnapshotViewAPI','RestoreSnapshot',ptr(0),[snapshot,session]);
            rt.clearInputHistory(quantum);
            const frame=invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',quantum,[]);
            const actual=unbox(invoke('Quantum','Frame','CalculateChecksum',frame,[])).readU64().toString(16);
            if(actual!==incoming.meta.checksum||frame.add(0x28).readS32()!==incoming.meta.tick)
              throw new Error('Independent receiver checksum differs at '+incoming.meta.tick+': '+actual+' vs '+incoming.meta.checksum);
            const recovered=predictionStalled;predictionStalled=false;
            lastTick=incoming.meta.tick;applied++;
            if(!remapped){
              const slot=ownedSlot;
              const add=new NativeFunction(mod.base.add(0x70dad70),'bool',['pointer','int','int','pointer']);
              if(!add(playerMap,slot,slot+1,ptr(0)))throw new Error('Cannot bind the assigned authoritative player');
              remapped=true;
              send({event:'receiver-player-assigned',slot,player:slot+1});
            }
            const provider=quantum.add(0x58).readPointer();
            let mask=0;
            for(let slot=0;slot<4;slot++){
              const player=Memory.alloc(4);player.writeS32(slot+1);
              if(invoke('Quantum','Frame','GetPlayerData',frame,[player]).isNull())continue;
              mask|=1<<slot;
              if(slot!==ownedSlot){
                const input=getPlayerInput(frame,slot+1,ptr(0));
                if(!input.isNull())remoteInputs.set(slot,input.readByteArray(0x110));
              }
            }
            // Remote players stay present while predicted from their latest state.
            provider.add(0x20).writeU64(mask);provider.add(0x28).writeU64(0);
            send({event:'receiver-state-verified',tick:lastTick,checksum:actual,recovered,player:rt.playerState(frame,ownedSlot+1)});
          }finally{desiredRestore=null;rt.exitLockstep(view,ptr(0));}
          if(!bound){
            bound=true;invoke('Moon.GameSession','ClientFlowAPI','ProceedToGame',vf.add(0x4e0).readPointer(),[session]);
          }
        }
        if(Date.now()-lastProgress>=1000){
          lastProgress=Date.now();
          const view=invoke('frameworks.session','SessionViewBinding','get_Instance',ptr(0),[]);
          const viewState=unbox(invoke('frameworks.session','SessionViewBinding','get_State',view,[])).readS32();
          const flow=vf.add(0x4d8).readPointer().add(0x10).readS32();
          if(!viewReady&&viewState===11&&flow===4){
            const hero=Memory.alloc(8);hero.writePointer(ptr(0));
            const exists=unbox(invoke('Moon.ViewSynchronization.Player','PlayerViewsAPI','TryGetBoundHeroView',vf.add(0x70).readPointer(),[hero])).readU8()!==0;
            if(exists&&!hero.readPointer().isNull()){
              viewReady=true;boundAt=lastTick;send({event:'receiver-view-ready',tick:lastTick,boundHero:true,clientFlow:flow});
            }
          }
          send({event:'receiver-progress',tick:current.add(0x28).readS32(),lastTick,applied,viewState,clientFlow:flow,inputs:sequence});
        }
      });}catch(e){stage='failed';send({event:'authority-probe-error',error:String(e),stack:e.stack});}
      finally{busy=false;}
    }
  });
}});
