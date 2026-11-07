// Rehydrate a durable authority checkpoint through the engine's initial-frame API.
'use strict';
const restartObserver=Process.attachModuleObserver({onAdded(mod){
  if(mod.name.toLowerCase()!=='gameassembly.dll')return;
  const rt=createAuthorityRuntime(mod);
  const {invoke,unbox,scoped,pin,klass,api}=rt;
  const arrayNew=api('il2cpp_array_new','pointer',['pointer','uint64']);
  const buffers={};
  for(const kind of ['frame','runtime','session'])recv('restore-'+kind,(_message,data)=>{buffers[kind]=data;});
  let session=ptr(0),stage='waiting',injected=false,verified=false,lastProgress=0;
  function managed(data){const result=pin(arrayNew(klass('System','Byte'),data.byteLength));result.add(0x20).writeByteArray(data);return result;}
  function bindRestoredPlayers(frame){
    const quantum=session.add(0x3d8).readPointer(),map=quantum.add(0x10).readPointer();
    const add=new NativeFunction(mod.base.add(0x70dad70),'bool',['pointer','int','int','pointer']);
    const has=new NativeFunction(mod.base.add(0x70dab40),'bool',['pointer','int','pointer']);
    if(!has(map,0,ptr(0))&&!add(map,0,1,ptr(0)))throw new Error('Cannot bind resumed authority player');
    let mask=0;
    for(let slot=0;slot<4;slot++){
      const player=Memory.alloc(4);player.writeS32(slot+1);
      if(!invoke('Quantum','Frame','GetPlayerData',frame,[player]).isNull())mask|=1<<slot;
    }
    const provider=quantum.add(0x58).readPointer();
    provider.add(0x20).writeU64(mask);provider.add(0x28).writeU64(0);
    rt.clearInputHistory(quantum);
    stage='running';send({event:'restart-authority-running',tick:frame.add(0x28).readS32(),playerMask:mask});
  }
  Interceptor.attach(mod.base.add(0x70dbe40),{onEnter(args){
    if(stage!=='creating')return;
    try{
      args[1].add(0x30).writeS32(probeConfig.checkpointTick);
      args[1].add(0x38).writePointer(managed(buffers.frame));
      args[1].add(0x40).writePointer(managed(buffers.runtime));
      args[1].add(8).writePointer(invoke('Photon.Deterministic','DeterministicSessionConfig','FromByteArray',ptr(0),[managed(buffers.session)]));
      injected=true;send({event:'restart-initial-frame-installed',tick:probeConfig.checkpointTick});
    }catch(error){stage='failed';send({event:'authority-probe-error',error:String(error)});}
  }});
  Interceptor.attach(mod.base.add(0x596bcb0),{
    onEnter(args){this.frame=args[0];},
    onLeave(){
      if(!injected||verified||stage==='failed')return;
      try{scoped(()=>{
        const tick=this.frame.add(0x28).readS32();
        if(tick!==probeConfig.checkpointTick)return;
        const checksum=unbox(invoke('Quantum','Frame','CalculateChecksum',this.frame,[])).readU64().toString(16);
        if(checksum!==probeConfig.checkpointChecksum)throw new Error('Restart deserialized checksum differs: '+checksum);
        bindRestoredPlayers(this.frame);
        verified=true;send({event:'restart-checksum-verified',tick,checksum,players:[rt.playerState(this.frame),rt.playerState(this.frame,2)]});
      });}catch(error){stage='failed';send({event:'authority-probe-error',error:String(error)});}
    }
  });
  const insertOriginal=new NativeFunction(mod.base.add(0x871bd10),'void',['pointer','pointer'],{exceptions:'propagate'});
  const insert=new NativeCallback((self,method)=>{
    if(self.equals(session)){self.add(0x3a8).writeU8(1);return;}
    insertOriginal(self,method);
  },'void',['pointer','pointer']);
  keep.push(insert);Interceptor.replace(mod.base.add(0x871bd10),insert);
  Interceptor.attach(mod.base.add(0x8719090),{
    onEnter(args){this.ours=args[0].equals(session);},
    onLeave(){
      if(!this.ours||stage==='failed')return;
      send({event:'restart-waiting-for-initial-frame'});
    }
  });
  Interceptor.attach(mod.base.add(0x5bf9a10),{
    onEnter(args){this.out=args[0];this.ours=!session.isNull()&&args[1].equals(session.add(0x3d0).readPointer());},
    onLeave(){if(this.ours){this.out.writeByteArray(new Uint8Array(0x110));this.out.add(0x1c).writeS32(1);this.out.add(0x110).writeU8(1);}}
  });
  let busy=false;
  Interceptor.attach(mod.base.add(0x87255e0),{
    onEnter(args){this.vf=args[0];},
    onLeave(){
      if(busy)return;busy=true;
      try{scoped(()=>{
        if(stage!=='waiting'||!buffers.frame||!buffers.runtime||!buffers.session)return;
        if(!this.vf.add(0x5c).readU8()||this.vf.add(0x568).readPointer().isNull())return;
        rt.setFrameRate(60,ptr(0));
        const definition=rt.createDefinition(this.vf,'AuthorityStreamProbe',true,probeConfig.sharedDefinition);
        stage='creating';session=invoke('frameworks.session','SessionHandlerAPI','StartSession',this.vf.add(0x328).readPointer(),[unbox(definition)]);
      });}catch(error){stage='failed';send({event:'authority-probe-error',error:String(error)});}
      finally{busy=false;}
    }
  });
  Interceptor.attach(mod.base.add(0x87191a0),{
    onEnter(args){this.ours=args[0].equals(session);},
    onLeave(){
      if(!this.ours||stage!=='running'||Date.now()-lastProgress<1000)return;
      try{scoped(()=>{
        const frame=invoke('Photon.Deterministic','DeterministicSession','get_FrameVerified',session.add(0x3d8).readPointer(),[]);
        const tick=frame.add(0x28).readS32();lastProgress=Date.now();
        const active=unbox(invoke('Quantum','Frame','get_ActivePlayerCount',frame,[])).readS32();
        send({event:'restart-progress',tick,activePlayers:active,players:[rt.playerState(frame),rt.playerState(frame,2)]});
        if(verified&&tick>=probeConfig.checkpointTick+600){
          const mode=Memory.alloc(4);mode.writeS32(0);
          const data=invoke('Quantum','Frame','Serialize',frame,[mode]);
          const bytes=data.add(0x18).readU64().toNumber();
          const checksum=unbox(invoke('Quantum','Frame','CalculateChecksum',frame,[])).readU64().toString(16);
          send({event:'restart-checkpoint',tick,checksum,bytes},data.add(0x20).readByteArray(bytes));
          stage='complete';send({event:'restart-complete',tick});
        }
      });}catch(error){stage='failed';send({event:'authority-probe-error',error:String(error)});}
    }
  });
}});
