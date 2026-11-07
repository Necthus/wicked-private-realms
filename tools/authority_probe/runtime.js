// Shared development-only IL2CPP invocation helpers.
function createAuthorityRuntime(mod) {
  const api = (name, ret, args) => new NativeFunction(mod.getExportByName(name), ret, args);
  const domainGet = api('il2cpp_domain_get','pointer',[]);
  const getAssemblies = api('il2cpp_domain_get_assemblies','pointer',['pointer','pointer']);
  const getImage = api('il2cpp_assembly_get_image','pointer',['pointer']);
  const getClass = api('il2cpp_class_from_name','pointer',['pointer','pointer','pointer']);
  const getMethod = api('il2cpp_class_get_method_from_name','pointer',['pointer','pointer','int']);
  const runtimeInvoke = api('il2cpp_runtime_invoke','pointer',['pointer','pointer','pointer','pointer']);
  const unbox = api('il2cpp_object_unbox','pointer',['pointer']);
  const stringNew = api('il2cpp_string_new','pointer',['pointer']);
  const newHandle = api('il2cpp_gchandle_new','pointer',['pointer','bool']);
  const objectNew = api('il2cpp_object_new','pointer',['pointer']);
  const enterLockstep = new NativeFunction(mod.base.add(0x5fd1930),'void',['pointer','pointer']);
  const exitLockstep = new NativeFunction(mod.base.add(0x5fd1ab0),'void',['pointer','pointer']);
  const setFrameRate = new NativeFunction(mod.base.add(0x7e85450),'void',['int','pointer']);
  const handles = [];
  const scopes = new Map();
  const gcFree = api('il2cpp_gchandle_free','void',['pointer']);
  const classes = new Map();
  const methods = new Map();
  const pin = obj => {
    if(!obj.isNull()) {
      const handle=newHandle(obj,0);
      const stack=scopes.get(Process.getCurrentThreadId());
      (stack&&stack.length?stack[stack.length-1]:handles).push(handle);
    }
    return obj;
  };
  function scoped(fn) {
    const id=Process.getCurrentThreadId();
    let stack=scopes.get(id);
    if(!stack){stack=[];scopes.set(id,stack);}
    const roots=[];stack.push(roots);
    try{return fn();}finally{
      stack.pop();if(!stack.length)scopes.delete(id);
      for(const root of roots)gcFree(root);
    }
  }
  const text = str => pin(stringNew(Memory.allocUtf8String(str)));
  function klass(ns, name) {
    const key=ns+':'+name;
    if(classes.has(key)) return classes.get(key);
    const count=Memory.alloc(8); count.writeU64(0);
    const array=getAssemblies(domainGet(), count), len=count.readU64().toNumber();
    for(let i=0;i<len;i++) {
      const c=getClass(getImage(array.add(i*8).readPointer()),Memory.allocUtf8String(ns),Memory.allocUtf8String(name));
      if(!c.isNull()){classes.set(key,c);return c;}
    }
    throw new Error('Class unavailable: '+key);
  }
  function method(ns, cls, name, count) {
    const key=ns+':'+cls+':'+name+':'+count;
    if(!methods.has(key)) {
      const m=getMethod(klass(ns,cls),Memory.allocUtf8String(name),count);
      if(m.isNull())throw new Error('Method unavailable: '+key);
      methods.set(key,m);
    }
    return methods.get(key);
  }
  function invoke(ns, cls, name, self, args) {
    const params=args.length ? Memory.alloc(args.length*8) : ptr(0);
    for(let i=0;i<args.length;i++)params.add(i*8).writePointer(args[i]);
    const ex=Memory.alloc(8);ex.writePointer(ptr(0));
    const result=runtimeInvoke(method(ns,cls,name,args.length),self,params,ex);
    const exception=ex.readPointer();
    if(!exception.isNull()) {
      throw new Error(cls+'.'+name+': '+readString(exception.add(0x18).readPointer()));
    }
    return pin(result);
  }
  function guid(value) {
    const memory=Memory.alloc(16);
    memory.writeByteArray(value.match(/../g).map(x=>parseInt(x,16)));
    return memory;
  }
  function createDefinition(vf, name, player=false, shared=null) {
    const manager=vf.add(0x568).readPointer();
    const account=shared?text(shared.account):invoke('Moon.Forsaken','SaveDataManager','get_LocalAccountId',manager,[]);
    const realmId=shared?guid(shared.realm):unbox(invoke('System','Guid','NewGuid',ptr(0),[]));
    const characterId=shared?guid(shared.character):unbox(invoke('System','Guid','NewGuid',ptr(0),[]));
    const realm=invoke('Quantum','SavesAPI','CreateNewLocalRealm',ptr(0),[account,realmId]);
    const characterSave=invoke('Quantum','SavesAPI','CreateNewLocalCharacter',ptr(0),[account,characterId,text(name)]);
    const realmContents=unbox(realm).add(0x60).readPointer();
    let character=unbox(characterSave).add(0x120).readPointer();
    if(player){
      const ctx=invoke('Moon.Forsaken','Core','get_ContextualAssetCtx',ptr(0),[]);
      character=invoke('frameworks.session','SessionDefinition','ResolveCharacterContents',ptr(0),[
        ptr(0),ptr(0),ctx,text(name)]);
    }
    const zero=Memory.alloc(8);zero.writeU64(0);
    const definition=invoke('frameworks.session','SessionDefinition','CreateQuickplay',ptr(0),[realmContents,character,zero]);
    const raw=unbox(definition);
    if(shared){
      const mappedRealm=invoke('Quantum','SavesAPI','ProduceRemappedLocalRealmSave',ptr(0),[realmContents,account,realmId,characterId]);
      const mappedCharacter=invoke('Quantum','SavesAPI','ProduceRemappedLocalCharacterSave',ptr(0),[character,account,realmId,characterId]);
      Memory.copy(raw.add(8),unbox(mappedRealm),0x68);
      Memory.copy(raw.add(0x70),unbox(mappedCharacter),0x128);
      raw.add(0x1b8).writePointer(account);
      Memory.copy(raw.add(0x1c0),realmId,16);
      Memory.copy(raw.add(0x1d0),characterId,16);
    }
    raw.add(0x278).writeU8(1);
    if(player)raw.add(0x238).writeU8(1);
    return definition;
  }
  function describeDefinition(definition) {
    const raw=unbox(definition);
    const hex=address=>Array.from(new Uint8Array(address.readByteArray(16))).map(x=>x.toString(16).padStart(2,'0')).join('');
    return {account:readString(raw.add(0x1b8).readPointer()),realm:hex(raw.add(0x1c0)),character:hex(raw.add(0x1d0))};
  }
  function clearInputHistory(quantum) {
    const provider=quantum.add(0x58).readPointer();
    if(provider.isNull()||!api('il2cpp_object_get_class','pointer',['pointer'])(provider).equals(klass('Photon.Deterministic','LocalInputProvider')))
      throw new Error('Only offline local-input sessions are supported by this probe');
    invoke('Photon.Deterministic','RawInputCache','Clear',provider.add(0x58).readPointer(),[]);
  }
  function playerState(frame, index=1) {
    const player=Memory.alloc(4);player.writeS32(index);
    const hero=unbox(invoke('Quantum','Frame','GetPlayerHero',frame,[player]));
    const state={hero:hero.readU64().toString(16),position:null};
    if(state.hero!=='0'){
      const position=unbox(invoke('Quantum','AiUtils','GetPosition',ptr(0),[frame,hero]));
      state.position=[0,8,16].map(offset=>position.add(offset).readS64().toString());
    }
    return state;
  }
  function createPlayerData(definition, immediate=false) {
    const raw=unbox(definition);
    const integration=pin(objectNew(klass('Quantum','CharacterIntegrationData')));
    invoke('Quantum','CharacterIntegrationData','.ctor',integration,[]);
    Memory.copy(integration.add(0x48),raw.add(0x1c0),16);
    Memory.copy(integration.add(0x58),raw.add(0x70),0x128);
    integration.add(0x188).writeS32(0);
    integration.add(0x18c).writeS32(immediate?2:1);
    const data=pin(objectNew(klass('Quantum','RuntimePlayer')));
    invoke('Quantum','RuntimePlayer','.ctor',data,[]);
    data.add(0x10).writePointer(integration);
    return data;
  }
  function configurePresence(quantum, frame, ownedSlot=0) {
    const map=quantum.add(0x10).readPointer();
    const add=new NativeFunction(mod.base.add(0x70dad70),'bool',['pointer','int','int','pointer']);
    const has=new NativeFunction(mod.base.add(0x70dab40),'bool',['pointer','int','pointer']);
    if(!has(map,ownedSlot,ptr(0))&&!add(map,ownedSlot,ownedSlot+1,ptr(0)))throw new Error('Cannot bind authoritative player');
    let mask=0;
    for(let slot=0;slot<4;slot++){
      const player=Memory.alloc(4);player.writeS32(slot+1);
      if(!invoke('Quantum','Frame','GetPlayerData',frame,[player]).isNull())mask|=1<<slot;
    }
    const provider=quantum.add(0x58).readPointer();
    provider.add(0x20).writeU64(mask);provider.add(0x28).writeU64(0);
    clearInputHistory(quantum);
    return mask;
  }
  function installCheckpoint(getSession, onRestored) {
    const buffers={};
    const arrayNew=api('il2cpp_array_new','pointer',['pointer','uint64']);
    for(const kind of ['frame','runtime','session'])recv('restore-'+kind,(_m,data)=>{buffers[kind]=data;});
    function bytes(data){
      if(!data)throw new Error('Missing initial checkpoint data');
      const result=pin(arrayNew(klass('System','Byte'),data.byteLength));
      result.add(0x20).writeByteArray(data);return result;
    }
    let installed=false,restored=false;
    Interceptor.attach(mod.base.add(0x70dbe40),{onEnter(args){
      if(installed||getSession().isNull())return;
      try{
        args[1].add(0x30).writeS32(probeConfig.checkpointTick);
        args[1].add(0x38).writePointer(bytes(buffers.frame));
        args[1].add(0x40).writePointer(bytes(buffers.runtime));
        args[1].add(8).writePointer(invoke('Photon.Deterministic','DeterministicSessionConfig','FromByteArray',ptr(0),[bytes(buffers.session)]));
        installed=true;
      }catch(error){send({event:'authority-probe-error',error:String(error)});}
    }});
    Interceptor.attach(mod.base.add(0x596bcb0),{
      onEnter(args){this.frame=args[0];},
      onLeave(){
        if(!installed||restored||this.frame.add(0x28).readS32()!==probeConfig.checkpointTick)return;
        try{scoped(()=>{
          const checksum=unbox(invoke('Quantum','Frame','CalculateChecksum',this.frame,[])).readU64().toString(16);
          if(checksum!==probeConfig.checkpointChecksum)throw new Error('Initial checkpoint checksum differs');
          onRestored(this.frame);restored=true;
          send({event:'authority-checkpoint-restored',tick:probeConfig.checkpointTick,checksum});
        });}catch(error){send({event:'authority-probe-error',error:String(error)});}
      }
    });
  }
  return {invoke,pin,text,klass,unbox,objectNew,enterLockstep,exitLockstep,setFrameRate,api,scoped,
    createDefinition,describeDefinition,clearInputHistory,playerState,createPlayerData,configurePresence,installCheckpoint};
}
