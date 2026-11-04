// Build 29466 research adapter. Changes process memory only; never edits game DLLs.
// The launcher validates the exact GameAssembly hash before loading this script.
'use strict';
const endpoint = __ENDPOINT__;
const privateToken = __PRIVATE_TOKEN__;
const accountName = __ACCOUNT_NAME__;
const saveCache = __SAVE_CACHE__;
const expectedSteamId = __STEAM_ID__;
const skipPrologueForEntryTest = __SKIP_PROLOGUE__;
const transportProbe = __TRANSPORT_PROBE__;
const keep = [];
let installed = false;
const nativePersona = accountName ? Memory.allocUtf8String(accountName) : null;
const steamNameHooks = new Set();
function installSteamNames(mod){
  if(!nativePersona || !/^steam_api(64)?\.dll$/i.test(mod.name))return;
  for(const symbol of mod.enumerateExports()){
    if(symbol.type!=='function' || !/^SteamAPI_ISteamFriends_GetPersonaName$/.test(symbol.name))continue;
    const key=symbol.address.toString();
    if(steamNameHooks.has(key))continue;
    steamNameHooks.add(key);
    keep.push(Interceptor.attach(symbol.address,{onLeave(retval){retval.replace(nativePersona);}}));
    send({event:'private-native-persona-installed',module:mod.name});
  }
}
function readString(p) {
  if (p.isNull()) return null;
  const len = p.add(16).readS32();
  if (len < 0 || len > 16 * 1024 * 1024) return '<invalid-string>';
  return p.add(20).readUtf16String(len);
}
function install(mod) {
  if (installed || mod.name.toLowerCase() !== 'gameassembly.dll') return;
  installed = true;
  const newString = new NativeFunction(mod.getExportByName('il2cpp_string_new'), 'pointer', ['pointer']);
  const gcNew = new NativeFunction(mod.getExportByName('il2cpp_gchandle_new'), 'pointer', ['pointer', 'bool']);
  const gcFree = new NativeFunction(mod.getExportByName('il2cpp_gchandle_free'), 'void', ['pointer']);
  const roots = new Map();
  function managed(text) {
    if (!roots.has(text)) {
      const p = newString(Memory.allocUtf8String(text));
      roots.set(text, {p, handle:gcNew(p,0)});
    }
    return roots.get(text).p;
  }
  function patchSdk(sdk) {
    if (sdk.isNull()) return;
    for (const [offset,suffix] of [[0x30,'/auth'],[0x40,'/rest'],[0x48,'/gatekeeper'],[0x50,'/queue']]) {
      sdk.add(offset).writePointer(managed(endpoint+suffix));
    }
  }
  // Redirect only the game's save datastore initialization, never Unity's
  // persistentDataPath/PlayerPrefs: graphics preferences remain machine-wide.
  keep.push(Interceptor.attach(mod.base.add(0x8abc680),{
    onEnter(args){
      const actual=readString(args[3]);
      if(expectedSteamId && actual!==expectedSteamId){send({event:'steam-identity-mismatch',expected:expectedSteamId,actual});return;}
      if(saveCache){
        args[4]=managed(saveCache);
        send({event:'private-save-cache',path:saveCache,localAccountId:actual});
      }else send({event:'native-steam-save-path',localAccountId:actual});
    }
  }));
  if(accountName){
    // Display identity only. Steam IDs, character IDs and saved names stay intact.
    keep.push(Interceptor.attach(mod.base.add(0xb1b2f0),{
      onLeave(retval){retval.replace(managed(accountName));}
    }));
    // Calls to GetPersonaName can be inlined by IL2CPP. Cover the native
    // string interop entry and the game's cached session getter as well.
    keep.push(Interceptor.attach(mod.base.add(0xb62fc0),{
      onLeave(retval){retval.replace(nativePersona);}
    }));
    keep.push(Interceptor.attach(mod.base.add(0x8abb920),{
      onLeave(retval){retval.replace(managed(accountName));}
    }));
    keep.push(Interceptor.attach(mod.base.add(0x70470e0),{
      onEnter(args){if(args[0].add(0x1c).readU8())args[1]=managed(accountName);}
    }));
    const setNickname=new NativeFunction(mod.base.add(0x70470e0),'void',['pointer','pointer','pointer']);
    keep.push(Interceptor.attach(mod.base.add(0x8728b50),{
      onEnter(args){this.self=args[0];},
      onLeave(){
        const client=this.self.add(0x28).readPointer();
        if(!client.isNull()){
          const local=client.add(0xf8).readPointer();
          if(!local.isNull())setNickname(local,managed(accountName),ptr(0));
        }
      }
    }));
    send({event:'private-account-display',name:accountName});
  }
  if(transportProbe){
    // The retail client treats a remote realm with zero other citizens as solo.
    // Private backend realms remain shared even when their only member is the
    // owner. Preserve offline-only realms and use the normal online flow for
    // real remote entries; never fabricate a second citizen/account.
    const getRealmEntry=new NativeFunction(mod.base.add(0x8aebf10),'pointer',['pointer','pointer','pointer']);
    const sharedRealmMessages=new Set();
    keep.push(Interceptor.attach(mod.base.add(0x8aee5a0),{
      onEnter(args){
        this.shared=false;
        const realm=args[0],owner=realm.readPointer();
        if(owner.isNull())return;
        const entry=getRealmEntry(owner.add(0x118),realm.add(8),ptr(0));
        this.shared=entry.add(0x10).readS32()===-1 && entry.add(0x18).readS32()>=0;
        this.localKey=realm.add(8).readByteArray(16);
      },
      onLeave(retval){
        if(this.shared && retval.toInt32()!==0){
          retval.replace(ptr(0));
          const key=Array.from(new Uint8Array(this.localKey),b=>b.toString(16).padStart(2,'0')).join('');
          if(!sharedRealmMessages.has(key)){
            sharedRealmMessages.add(key);
            send({event:'private-realm-kept-online',reason:'RemoteRealmHasNoOtherCitizens',localKey:key});
          }
        }
      }
    }));
    keep.push(Interceptor.attach(mod.base.add(0x5bf6470),{
      onEnter(args){send({event:'native-player-add-confirmed',frame:args[1].add(0x28).readS32(),localSlot:args[2].toInt32(),playerRef:args[3].toInt32()});}
    }));
    const getVerified=new NativeFunction(mod.base.add(0x70db6d0),'pointer',['pointer','pointer']);
    keep.push(Interceptor.attach(mod.base.add(0x8719090),{
      onEnter(args){
        const q=args[0].add(0x3d8).readPointer();
        if(!q.isNull()){
          const f=getVerified(q,ptr(0));
          send({event:'game-start-frame',verifiedFrame:f.isNull()?null:f.add(0x28).readS32(),initialTick:q.add(0x158).readS32()});
        }
      }
    }));
    keep.push(Interceptor.attach(mod.base.add(0x70496f0),{
      onEnter(args){
        const settings=args[1];
        settings.add(0x40).writeU8(0); // UseNameServer=false: never contact Photon Cloud.
        settings.add(0x58).writePointer(managed('127.0.0.1'));
        settings.add(0x60).writeU16(18769);
        settings.add(0x70).writeU8(1); // TCP, carried over SSH when remote.
        settings.add(0x71).writeU8(0); // No fallback to cloud endpoints.
        settings.add(0x74).writeS32(0);
        send({event:'transport-probe-connect',target:'127.0.0.1:18769'});
      }
    }));
  }
  const sharedReadyAddress=mod.base.add(0x890f060);
  const sharedReadyOriginal=new NativeFunction(sharedReadyAddress,'void',['pointer','pointer']);
  const disposeRequest=new NativeFunction(mod.base.add(0x890f5e0),'void',['pointer','pointer']);
  const mainMenu=new NativeFunction(mod.base.add(0x890bcb0),'void',['pointer','pointer']);
  const sharedReadyCallback=new NativeCallback(function(self,method){
    const frame=self.add(0x10).readPointer();
    if(!frame.isNull()){
      const flow=frame.add(0x4d8).readPointer();
      if(!flow.isNull()){
        const request=flow.add(0x48).readPointer();
        if(!request.isNull()){
          const task=request.add(0x18).readPointer();
          if(!task.isNull() && (task.add(0x38).readU32() & 0x1600000)===0x1000000 && task.add(0x50).readS32()===1001){
            send({event:'join-rejected',reason:'PrivateTransportUnavailable',retry:false});
            disposeRequest(self,ptr(0));
            mainMenu(self,ptr(0));
            return;
          }
        }
      }
    }
    sharedReadyOriginal(self,method);
  },'void',['pointer','pointer']);
  keep.push(sharedReadyCallback);
  Interceptor.replace(sharedReadyAddress,sharedReadyCallback);
  keep.push(Interceptor.attach(mod.base.add(0x890d2c0), {
    onEnter(args) {
      send({event:'session-join-request',playPrologue:args[3].toInt32() !== 0,
        diagnosticSkipPrologue:skipPrologueForEntryTest});
      if (skipPrologueForEntryTest) args[3] = ptr(0);
    }
  }));
  let lastSessionObservation = '';
  keep.push(Interceptor.attach(mod.base.add(0x871c5d0), {
    onEnter(args) {
      const p=args[0], q=p.add(0x3d8).readPointer();
      const observation={event:'session-state',state:p.add(0x10).readS32(),
        onlineHandlerPresent:!p.add(0x380).readPointer().isNull(),
        quantumMode:q.isNull()?null:q.add(0x90).readS32()};
      const key=JSON.stringify(observation);
      if(key!==lastSessionObservation){lastSessionObservation=key;send(observation);}
    }
  }));
  keep.push(Interceptor.attach(mod.base.add(0x58239e0), {
    onEnter(args) {
      const backend = args[0].add(0x20).readPointer();
      if (args[0].readS32() === -1 && privateToken) args[0].add(0x30).writePointer(managed(privateToken));
      if (!backend.isNull()) { patchSdk(backend.add(0x10).readPointer()); send({event:'login-step',state:args[0].readS32()}); }
    }
  }));
  keep.push(Interceptor.attach(mod.base.add(0x5845e00), {
    onEnter(args) {
      const method = args[0].add(0x28).readPointer();
      if (!method.isNull()) { patchSdk(method.add(0x10).readPointer()); send({event:'sdk-request-step',state:args[0].readS32()}); }
    }
  }));
  for (const rva of [0x770d620, 0x770d730, 0x771bc50]) {
    keep.push(Interceptor.attach(mod.base.add(rva), {
      onEnter(args) {
        this.handle = null;
        const text = readString(args[1]);
        if (!text) return;
        const match = /^(https?):\/\/([^/]+)(\/.*)?$/i.exec(text);
        if (!match) return;
        const host = match[2].toLowerCase();
        if (!host.endsWith('.moonbackend.net') && !host.endsWith('.norestforthewickedbackend.net')) return;
        const suffix = (host.includes('gatekeeper') ? '/gatekeeper' : '') + (match[3] || '/');
        const value = newString(Memory.allocUtf8String(endpoint + suffix));
        this.handle = gcNew(value, 0);
        args[1] = value;
        send({event:'redirect', host, path:suffix.split('?')[0]});
      },
      onLeave() { if (this.handle !== null) gcFree(this.handle); }
    }));
  }
  keep.push(Interceptor.attach(mod.base.add(0x581c8e0), {
    onEnter(args) {
      send({event:'save-api-call', endpoint:readString(args[1]),
        bodyLength:args[2].isNull() ? 0 : args[2].add(16).readS32()});
    }
  }));
  keep.push(Interceptor.attach(mod.base.add(0x581c610), {
    onEnter(args) { send({event:'backend-state', state:args[0].add(0x48).readS32()}); }
  }));
  send({event:'installed', module:mod.name, base:mod.base.toString()});
}
const observer = Process.attachModuleObserver({onAdded(mod){installSteamNames(mod);install(mod);}});
rpc.exports = {status() {return {installed, endpoint};}};
