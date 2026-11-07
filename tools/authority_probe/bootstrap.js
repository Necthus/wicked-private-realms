// Development-only, build-checked in probe_authority_runtime.py before spawn.
// These hooks are not included in the distributed native client.
'use strict';
const keep = [];
const bootstrapObserver = Process.attachModuleObserver({onAdded(mod) {
  if (mod.name.toLowerCase() !== 'gameassembly.dll') return;
  const stringNew = new NativeFunction(mod.getExportByName('il2cpp_string_new'), 'pointer', ['pointer']);
  const gcNew = new NativeFunction(mod.getExportByName('il2cpp_gchandle_new'), 'pointer', ['pointer', 'bool']);
  const gcFree = new NativeFunction(mod.getExportByName('il2cpp_gchandle_free'), 'void', ['pointer']);
  const strings = new Map();
  Interceptor.attach(mod.base.add(0x5916c80),{onEnter(args){
    if(!args[0].isNull())send({event:'authority-probe-error',error:'Game simulation exception: '+readString(args[0].add(0x18).readPointer())});
  }});
  function managed(value) {
    if (!strings.has(value)) {
      const obj = stringNew(Memory.allocUtf8String(value));
      strings.set(value, obj);
      keep.push(gcNew(obj, 0));
    }
    return strings.get(value);
  }
  function patchSdk(sdk) {
    if (sdk.isNull()) return;
    for (const [offset, suffix] of [[0x30,'/auth'], [0x40,'/rest'], [0x48,'/gatekeeper'], [0x50,'/queue']]) {
      sdk.add(offset).writePointer(managed(probeConfig.endpoint + suffix));
    }
  }
  Interceptor.attach(mod.base.add(0x8abc680), {onEnter(args) {
    args[4] = managed(probeConfig.saveCache);
    send({event:'save-isolation-installed'});
  }});
  Interceptor.attach(mod.base.add(0x58239e0), {onEnter(args) {
    const backend = args[0].add(0x20).readPointer();
    if (args[0].readS32() === -1) args[0].add(0x30).writePointer(managed(probeConfig.token));
    if (!backend.isNull()) patchSdk(backend.add(0x10).readPointer());
  }});
  Interceptor.attach(mod.base.add(0x5845e00), {onEnter(args) {
    const request = args[0].add(0x28).readPointer();
    if (!request.isNull()) patchSdk(request.add(0x10).readPointer());
  }});
  for (const rva of [0x770d620, 0x770d730, 0x771bc50]) {
    Interceptor.attach(mod.base.add(rva), {
      onEnter(args) {
        this.handle = null;
        const url = readString(args[1]);
        if (!url) return;
        const match = /^https?:\/\/([^/]+)(\/.*)?$/i.exec(url);
        if (!match) return;
        const host = match[1].toLowerCase();
        if (!host.endsWith('.moonbackend.net') && !host.endsWith('.norestforthewickedbackend.net')) return;
        const suffix = (host.includes('gatekeeper') ? '/gatekeeper' : '') + (match[2] || '/');
        const value = stringNew(Memory.allocUtf8String(probeConfig.endpoint + suffix));
        this.handle = gcNew(value, 0);
        args[1] = value;
      },
      onLeave() { if (this.handle !== null) gcFree(this.handle); }
    });
  }
  // Keep Unity's native batch execution, while loading the retail resources that
  // managed initializers would otherwise skip. This still requires a GPU.
  const interactive = new NativeCallback(() => 0, 'bool', ['pointer']);
  keep.push(interactive);
  Interceptor.replace(mod.base.add(0x7e84700), interactive);

  // This local prototype has no real-time network participants.
  const noPhoton = new NativeCallback(() => 0, 'bool', ['pointer','pointer','pointer']);
  keep.push(noPhoton);
  Interceptor.replace(mod.base.add(0x70496f0), noPhoton);
  Interceptor.attach(mod.base.add(0x5bf6470), {onEnter(args) {
    send({event:'local-player-inserted', game:args[0].toString(), tick:args[1].add(0x28).readS32(),
      slot:args[2].toInt32(),player:args[3].toInt32()});
  }});
  Interceptor.attach(mod.base.add(0x8916ae0), {onEnter(args) {
    let detail='';
    if(!args[0].isNull()){
      try{
        const format=new NativeFunction(mod.getExportByName('il2cpp_format_exception'),'void',['pointer','pointer','int']);
        const buffer=Memory.alloc(8192);buffer.writeU8(0);
        format(args[0],buffer,8192);detail=buffer.readUtf8String()||'';
      }catch(_error){detail=readString(args[0].add(0x18).readPointer())||'';}
    }
    send({event:'authority-probe-error', error:'Game core initialization failed'+(detail?': '+detail:'; inspect game.log')});
  }});
  Interceptor.attach(mod.base.add(0x8a00500), {onEnter(args) {
    send({event:'authority-probe-error', error:'Game requested exit', code:args[1].toInt32()});
  }});
  send({event:'probe-hooks-installed'});
}});

function readString(obj) {
  if (obj.isNull()) return null;
  const length = obj.add(16).readS32();
  if (length < 0 || length > 16384) return null;
  return obj.add(20).readUtf16String(length);
}
