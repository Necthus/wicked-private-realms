// Repair the observed timestamp overwrite in the test client's framed TCP ping.
// Restricted to TPeer send scopes whose destination is the private loopback port.
'use strict';
let pingRepairInstalled=false;
function installPingRepair(pingModule){
if(pingRepairInstalled)return;pingRepairInstalled=true;
const pingElapsed=new NativeFunction(pingModule.base.add(0x77e1850),'int64',['pointer','pointer']);
const pingScopes=new Map();let pingFixes=0;
function pingAddress(p){const s=p.add(0x30).readPointer();return s.isNull()?'':s.add(20).readUtf16String(s.add(16).readS32());}
for(const rva of [0x70a2940,0x70a3340,0x70a2e90]){
 Interceptor.attach(pingModule.base.add(rva),{
  onEnter(a){
   this.id=Process.getCurrentThreadId();this.previous=pingScopes.get(this.id);
   const p=a[0],address=pingAddress(p);
   if(address==='127.0.0.1:18769')pingScopes.set(this.id,p);
  },
  onLeave(){if(this.previous)pingScopes.set(this.id,this.previous);else pingScopes.delete(this.id);}
 });
}
Interceptor.attach(Process.getModuleByName('ws2_32.dll').getExportByName('send'),{
 onEnter(a){
  const peer=pingScopes.get(Process.getCurrentThreadId());
  if(!peer || a[2].toInt32()!==5 || a[1].readU8()!==240)return;
  const watch=peer.add(0xc0).readPointer();if(watch.isNull())return;
  const value=Number(pingElapsed(watch,ptr(0)).toString())>>>0;
  a[1].add(1).writeByteArray([(value>>>24)&255,(value>>>16)&255,(value>>>8)&255,value&255]);
  if(++pingFixes===1)send({event:'tcp-ping-timestamp-repaired',destination:pingAddress(peer)});
 }
});
send({event:'tcp-ping-repair-installed'});
}
Process.attachModuleObserver({onAdded(mod){if(mod.name.toLowerCase()==='gameassembly.dll')installPingRepair(mod);}});
