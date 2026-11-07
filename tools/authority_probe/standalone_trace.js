// Optional native exception diagnostics for the disposable standalone child.
Process.setExceptionHandler(details=>{
  const mod=Process.findModuleByName('GameAssembly.dll');
  send({event:'native-fault',type:details.type,address:details.address.toString(),
    rva:mod?details.address.sub(mod.base).toString():null,memory:details.memory,
    stack:Thread.backtrace(details.context,Backtracer.ACCURATE).slice(0,20).map(String)});
  return false;
});
const observer=Process.attachModuleObserver({onAdded(mod){
  if(mod.name.toLowerCase()!=='gameassembly.dll')return;
  let count=0;
  Interceptor.attach(mod.base.add(0x70d20d0),{
    onEnter(args){this.size=args[1].toInt32();this.alignment=args[2].toInt32();},
    onLeave(result){if(count++<10||result.isNull())send({event:'native-allocation',bytes:this.size,alignment:this.alignment,result:result.toString()});}
  });
}});
Interceptor.attach(Process.getModuleByName('KernelBase.dll').getExportByName('RaiseException'), {
  onEnter(args) {
    if(args[0].toUInt32()!==0xe06d7363)return;
    const mod=Process.findModuleByName('GameAssembly.dll');
    if(!mod)return;
    let exception=null;
    try {
      const ex=args[3].add(8).readPointer().readPointer(),cls=ex.readPointer();
      const name=new NativeFunction(mod.getExportByName('il2cpp_class_get_name'),'pointer',['pointer']);
      const ns=new NativeFunction(mod.getExportByName('il2cpp_class_get_namespace'),'pointer',['pointer']);
      const message=ex.add(0x18).readPointer();
      exception={type:ns(cls).readCString()+'.'+name(cls).readCString(),
        message:message.isNull()?null:message.add(0x14).readUtf16String(message.add(0x10).readS32())};
    } catch(error) { exception={readError:String(error)}; }
    send({event:'native-managed-exception',exception,
      stack:Thread.backtrace(this.context,Backtracer.ACCURATE).slice(0,24)
        .filter(address=>address.compare(mod.base)>=0&&address.compare(mod.base.add(mod.size))<0)
        .map(address=>address.sub(mod.base).toString())});
  }
});
