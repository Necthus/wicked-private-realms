// Development-only input ingress. It never waits for a remote player.
function createAuthorityInput(mod,rt) {
  const players=new Map();
  const commands=[];
  const commandSequences=new Map();
  const appliedCommands=new Map();
  const arrayNew=rt.api('il2cpp_array_new','pointer',['pointer','uint64']);
  const decode=new NativeFunction(mod.base.add(0x5bf6d30),'void',
    ['pointer','int','pointer','pointer','bool','pointer'],{exceptions:'propagate'});
  function listen(){recv('authority-input',(message,data)=>{
    const meta=message.payload;
    const slot=meta.slot||0,old=players.get(slot);
    if(data===null||data.byteLength!==47||!Number.isInteger(slot)||slot<0||slot>3||
        !Number.isInteger(meta.sequence)||(old&&meta.sequence<=old.sequence)){
      send({event:'authority-probe-error',error:'Invalid or reordered authority input'});return;
    }
    const state=old||{applied:0,neutral:0,appliedSequence:-1};
    state.latest=data;state.sequence=meta.sequence;state.receivedAt=Date.now();players.set(slot,state);listen();
  });}
  listen();
  function listenCommands(){recv('authority-command',(message,data)=>{
    const meta=message.payload,slot=meta.slot||0;
    if(data===null||data.byteLength<=0||data.byteLength>1024*1024||!Number.isInteger(slot)||slot<0||slot>3||
        !Number.isInteger(meta.sequence)||meta.sequence<=(commandSequences.get(slot)??-1)||commands.length>=256){
      send({event:'authority-probe-error',error:'Invalid, reordered or excessive authoritative command'});return;
    }
    commandSequences.set(slot,meta.sequence);commands.push({slot,sequence:meta.sequence,data});listenCommands();
  });}
  listenCommands();
  const addRpc=new NativeFunction(mod.base.add(0x70e9ec0),'void',['pointer','int','pointer','bool','pointer'],{exceptions:'propagate'});
  return {
    hasCommands(){return commands.length>0;},
    drain(quantum){
      const provider=quantum.add(0x58).readPointer();
      for(let processed=0;commands.length&&processed<16;processed++){
        const command=commands.shift();
        const data=rt.pin(arrayNew(rt.klass('System','Byte'),command.data.byteLength));
        data.add(0x20).writeByteArray(command.data);
        addRpc(provider,command.slot,data,1,ptr(0));appliedCommands.set(command.slot,command.sequence);
      }
    },
    apply(output,game,slot=0){rt.scoped(()=>{
      let state=players.get(slot);
      if(!state){state={latest:null,sequence:-1,receivedAt:0,applied:0,neutral:0,appliedSequence:-1};players.set(slot,state);}
      output.writeByteArray(new Uint8Array(0x110));
      if(state.latest!==null&&Date.now()-state.receivedAt<=250){
        const sequence=state.sequence,latest=state.latest;
        const data=rt.pin(arrayNew(rt.klass('System','Byte'),47));
        data.add(0x20).writeByteArray(latest);
        decode(game,slot,data,output,0,ptr(0));state.applied++;
        state.appliedSequence=sequence;
      }else state.neutral++;
      output.add(0x1c).writeS32(1);output.add(0x110).writeU8(1);
    });},
    stats(slot=0){const state=players.get(slot);return {sequence:state?.sequence??-1,appliedSequence:state?.appliedSequence??-1,
      applied:state?.applied??0,neutral:state?.neutral??0,commandSequence:appliedCommands.get(slot)??-1};}
  };
}
