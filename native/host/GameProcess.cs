using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;

namespace Wicked.Native {
    internal sealed class GameProcess:IDisposable {
        const string AssemblyHash="5b00ee90833b1be2ea73e01cb83e710e09e86199d12ac769be3c0e82add8b4bb";
        readonly string game,root,id,token;IntPtr process,primary,job;uint pid;
        MemoryMappedFile mapping;MemoryMappedViewAccessor view;
        internal GameProcess(string game,string root,string id,string token){this.game=game;this.root=root;this.id=id;this.token=token;}
        internal static void ValidateBuild(string game){if(!File.Exists(Path.Combine(game,"NoRestForTheWicked.exe"))||Crypto.ShaFile(Path.Combine(game,"GameAssembly.dll"))!=AssemblyHash)throw new Exception("Unsupported GameAssembly.dll; native adapter only supports the verified v29466 build");}
        internal void Run(Func<bool> cancelled,int seconds){
            ValidateBuild(game);string dll=Path.Combine(root,"WickedAdapter.dll");if(!File.Exists(dll))throw new Exception("WickedAdapter.dll is missing");
            string evidence=Path.Combine(root,"research");Directory.CreateDirectory(evidence);
            string archive=Path.Combine(evidence,"runs",DateTime.UtcNow.ToString("yyyyMMddTHHmmssfffffffZ"));
            foreach(string filename in new[]{"native-adapter.jsonl","native-game.log"}){
                string previous=Path.Combine(evidence,filename);if(File.Exists(previous)){Directory.CreateDirectory(archive);File.Copy(previous,Path.Combine(archive,filename));}
            }
            File.WriteAllText(Path.Combine(evidence,"native-adapter.jsonl"),"",new UTF8Encoding(false));
            string name="Local\\WickedNative-"+Guid.NewGuid().ToString("N");
            var security=new MemoryMappedFileSecurity();security.SetAccessRuleProtection(true,false);
            security.AddAccessRule(new AccessRule<MemoryMappedFileRights>(WindowsIdentity.GetCurrent().User,MemoryMappedFileRights.FullControl,AccessControlType.Allow));
            security.AddAccessRule(new AccessRule<MemoryMappedFileRights>(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null),MemoryMappedFileRights.FullControl,AccessControlType.Allow));
            mapping=MemoryMappedFile.CreateNew(name,8192,MemoryMappedFileAccess.ReadWrite,MemoryMappedFileOptions.None,security,HandleInheritability.None);
            view=mapping.CreateViewAccessor(0,8192,MemoryMappedFileAccess.ReadWrite);byte[] config=ConfigBytes();view.WriteArray(0,config,0,config.Length);Array.Clear(config,0,config.Length);
            var variables=new SortedDictionary<string,string>(StringComparer.OrdinalIgnoreCase);
            foreach(DictionaryEntry pair in Environment.GetEnvironmentVariables())variables[Convert.ToString(pair.Key)]=Convert.ToString(pair.Value);
            variables["WICKED_REALM_MAPPING"]=name;variables["SteamAppId"]="1371980";variables["SteamGameId"]="1371980";
            var block=new StringBuilder();foreach(var pair in variables)block.Append(pair.Key+"="+pair.Value+'\0');block.Append('\0');
            IntPtr environment=Marshal.StringToHGlobalUni(block.ToString());
            try{
                STARTUPINFO startup=new STARTUPINFO();startup.cb=Marshal.SizeOf(startup);PROCESS_INFORMATION result;
                string exe=Path.Combine(game,"NoRestForTheWicked.exe");string log=Path.Combine(root,"research","native-game.log");
                var command=new StringBuilder("\""+exe+"\" -logFile \""+log+"\"");
                if(!CreateProcessW(exe,command,IntPtr.Zero,IntPtr.Zero,false,4|0x400,environment,game,ref startup,out result))ThrowWin32("CreateProcess");
                process=result.process;primary=result.thread;pid=result.pid;
            }finally{Marshal.FreeHGlobal(environment);}
            try{
                ConfigureJob();Inject(dll);
                if(ResumeThread(primary)==0xffffffff)ThrowWin32("ResumeThread");
                Console.WriteLine("{\"event\":\"launched\",\"mode\":\"native\",\"pid\":"+pid+"}");
                DateTime deadline=seconds>0?DateTime.UtcNow.AddSeconds(seconds):DateTime.MaxValue;
                bool installed=false;
                while(WaitForSingleObject(process,100)==0x102){
                    int status=view.ReadInt32(16);if(status<0)throw new Exception("Native adapter reported error "+view.ReadInt32(20));
                    if(!installed&&status==3){installed=true;Console.WriteLine("Native GameAssembly hooks installed and acknowledged.");}
                    if(cancelled()||DateTime.UtcNow>=deadline){TerminateProcess(process,0);WaitForSingleObject(process,5000);return;}
                }
                uint code;GetExitCodeProcess(process,out code);Console.WriteLine("Native game exited: 0x"+code.ToString("X8"));
                if(code!=0)throw new Exception("Game exited with code 0x"+code.ToString("X8")+"; see research/native-game.log and native-adapter.jsonl");
            }catch{if(process!=IntPtr.Zero){TerminateProcess(process,0xe0020001);WaitForSingleObject(process,5000);}throw;}
        }
        byte[] ConfigBytes(){
            byte[] buffer=new byte[8192];Encoding.ASCII.GetBytes("WRNAT01").CopyTo(buffer,0);BitConverter.GetBytes((uint)1).CopyTo(buffer,8);BitConverter.GetBytes((uint)6340).CopyTo(buffer,12);
            using(var input=File.OpenRead(Path.Combine(game,"GameAssembly.dll")))using(var reader=new BinaryReader(input)){
                input.Position=0x3c;int nt=reader.ReadInt32();input.Position=nt+8;uint timestamp=reader.ReadUInt32();input.Position=nt+0x50;uint size=reader.ReadUInt32();
                BitConverter.GetBytes(size).CopyTo(buffer,24);BitConverter.GetBytes(timestamp).CopyTo(buffer,28);
            }
            Fixed(buffer,32,32,id,false);Fixed(buffer,64,128,"http://127.0.0.1:18770/game",false);Fixed(buffer,192,4096,token,false);
            // Reserved v1 flags remain zero; the game decides whether to play the prologue.
            Fixed(buffer,4288,2048,Path.Combine(root,"research","native-adapter.jsonl"),true);return buffer;
        }
        static void Fixed(byte[] buffer,int offset,int length,string text,bool unicode){byte[] encoded=(unicode?Encoding.Unicode:Encoding.UTF8).GetBytes(text+'\0');if(encoded.Length>length)throw new Exception("Native session field exceeds its bound");encoded.CopyTo(buffer,offset);}
        void ConfigureJob(){
            job=CreateJobObject(IntPtr.Zero,null);if(job==IntPtr.Zero)ThrowWin32("CreateJobObject");
            var info=new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();info.BasicLimitInformation.LimitFlags=0x2000;
            int size=Marshal.SizeOf(info);IntPtr memory=Marshal.AllocHGlobal(size);
            try{Marshal.StructureToPtr(info,memory,false);if(!SetInformationJobObject(job,9,memory,(uint)size)||!AssignProcessToJobObject(job,process))ThrowWin32("Assign kill-on-close process job");}finally{Marshal.FreeHGlobal(memory);}
        }
        void Inject(string dll){
            IntPtr local=GetProcAddress(GetModuleHandle("kernel32.dll"),"LoadLibraryW");if(local==IntPtr.Zero)ThrowWin32("Resolve LoadLibraryW");
            IntPtr module;if(!GetModuleHandleEx(6,local,out module))ThrowWin32("Resolve loader module");
            var filename=new StringBuilder(1024);GetModuleFileName(module,filename,filename.Capacity);
            IntPtr remoteModule=RemoteModule(Path.GetFileName(filename.ToString()),false);
            IntPtr load=remoteModule!=IntPtr.Zero?new IntPtr(remoteModule.ToInt64()+local.ToInt64()-module.ToInt64()):local;
            byte[] path=Encoding.Unicode.GetBytes(Path.GetFullPath(dll)+'\0');IntPtr allocation=VirtualAllocEx(process,IntPtr.Zero,new UIntPtr((uint)path.Length),0x3000,4);
            if(allocation==IntPtr.Zero)ThrowWin32("Allocate DLL path");
            try{UIntPtr written;if(!WriteProcessMemory(process,allocation,path,new UIntPtr((uint)path.Length),out written)||written.ToUInt64()!=(ulong)path.Length)ThrowWin32("Write DLL path");RunRemote(load,allocation,"LoadLibraryW");}
            finally{VirtualFreeEx(process,allocation,UIntPtr.Zero,0x8000);}
            IntPtr adapter=RemoteModule("WickedAdapter.dll",true);if(adapter==IntPtr.Zero)throw new Exception("Adapter DLL did not load");
            uint export=ExportRva(dll,"WickedAdapterInitialize");uint result=RunRemote(new IntPtr(adapter.ToInt64()+export),IntPtr.Zero,"WickedAdapterInitialize");
            if(result!=0)throw new Exception("Native adapter initialization failed: "+result);
            int status=view.ReadInt32(16);if(status!=2&&status!=3)throw new Exception("Adapter initialization handshake was not acknowledged: "+status);
            Console.WriteLine("Native adapter initialized before game resume.");
        }
        uint RunRemote(IntPtr start,IntPtr argument,string operation){IntPtr thread=CreateRemoteThread(process,IntPtr.Zero,UIntPtr.Zero,start,argument,0,IntPtr.Zero);if(thread==IntPtr.Zero)ThrowWin32(operation);
            try{if(WaitForSingleObject(thread,20000)!=0)throw new Exception(operation+" timed out");uint result;if(!GetExitCodeThread(thread,out result))ThrowWin32(operation+" result");return result;}finally{CloseHandle(thread);}}
        IntPtr RemoteModule(string name,bool retry){
            for(int attempt=0;attempt<(retry?50:1);attempt++){
                IntPtr snapshot=CreateToolhelp32Snapshot(8|0x10,pid);if(snapshot!=new IntPtr(-1)){
                    try{MODULEENTRY32 entry=new MODULEENTRY32();entry.dwSize=(uint)Marshal.SizeOf(entry);
                        if(Module32FirstW(snapshot,ref entry))do{if(entry.szModule.Equals(name,StringComparison.OrdinalIgnoreCase))return entry.modBaseAddr;}while(Module32NextW(snapshot,ref entry));
                    }finally{CloseHandle(snapshot);}
                }
                if(retry)Thread.Sleep(100);
            }
            return IntPtr.Zero;
        }
        internal static uint ExportRva(string filename,string name){
            byte[] raw=File.ReadAllBytes(filename);int nt=BitConverter.ToInt32(raw,0x3c);int optional=nt+24;ushort sections=BitConverter.ToUInt16(raw,nt+6),optionalSize=BitConverter.ToUInt16(raw,nt+20);
            Func<uint,int> offset=rva=>{for(int i=0;i<sections;i++){int section=optional+optionalSize+i*40;uint start=BitConverter.ToUInt32(raw,section+12),size=BitConverter.ToUInt32(raw,section+16),pointer=BitConverter.ToUInt32(raw,section+20);if(rva>=start&&rva<start+size)return checked((int)(pointer+rva-start));}if(rva<BitConverter.ToUInt32(raw,optional+60))return (int)rva;throw new Exception("Invalid DLL RVA");};
            int export=offset(BitConverter.ToUInt32(raw,optional+112));uint count=BitConverter.ToUInt32(raw,export+24);
            int names=offset(BitConverter.ToUInt32(raw,export+32)),ordinals=offset(BitConverter.ToUInt32(raw,export+36)),functions=offset(BitConverter.ToUInt32(raw,export+28));
            for(int i=0;i<count;i++){int text=offset(BitConverter.ToUInt32(raw,names+i*4)),end=text;while(end<raw.Length&&raw[end]!=0)end++;if(Encoding.ASCII.GetString(raw,text,end-text)==name)return BitConverter.ToUInt32(raw,functions+BitConverter.ToUInt16(raw,ordinals+i*2)*4);}
            throw new Exception("Missing adapter export: "+name);
        }
        public void Dispose(){if(job!=IntPtr.Zero){CloseHandle(job);job=IntPtr.Zero;}if(primary!=IntPtr.Zero){CloseHandle(primary);primary=IntPtr.Zero;}if(process!=IntPtr.Zero){CloseHandle(process);process=IntPtr.Zero;}if(view!=null){view.Dispose();view=null;}if(mapping!=null){mapping.Dispose();mapping=null;}}
        static void ThrowWin32(string operation){throw new Win32Exception(Marshal.GetLastWin32Error(),operation);}
        [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct STARTUPINFO{public int cb;public string reserved,desktop,title;public int x,y,width,height,xChars,yChars,fill,flags;public short show,reserved2;public IntPtr reserved3,stdin,stdout,stderr;}
        [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION{public IntPtr process,thread;public uint pid,tid;}
        [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct MODULEENTRY32{public uint dwSize,th32ModuleID,th32ProcessID,GlblcntUsage,ProccntUsage;public IntPtr modBaseAddr;public uint modBaseSize;public IntPtr hModule;[MarshalAs(UnmanagedType.ByValTStr,SizeConst=256)]public string szModule;[MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)]public string szExePath;}
        [StructLayout(LayoutKind.Sequential)] struct JOBOBJECT_BASIC_LIMIT_INFORMATION{public long PerProcessUserTimeLimit,PerJobUserTimeLimit;public uint LimitFlags;public UIntPtr MinimumWorkingSetSize,MaximumWorkingSetSize;public uint ActiveProcessLimit;public UIntPtr Affinity;public uint PriorityClass,SchedulingClass;}
        [StructLayout(LayoutKind.Sequential)] struct IO_COUNTERS{public ulong ReadOperationCount,WriteOperationCount,OtherOperationCount,ReadTransferCount,WriteTransferCount,OtherTransferCount;}
        [StructLayout(LayoutKind.Sequential)] struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION{public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;public IO_COUNTERS IoInfo;public UIntPtr ProcessMemoryLimit,JobMemoryLimit,PeakProcessMemoryUsed,PeakJobMemoryUsed;}
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateProcessW(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref STARTUPINFO si,out PROCESS_INFORMATION pi);
        [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr VirtualAllocEx(IntPtr process,IntPtr address,UIntPtr size,uint type,uint protect);
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool VirtualFreeEx(IntPtr process,IntPtr address,UIntPtr size,uint type);
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool WriteProcessMemory(IntPtr process,IntPtr address,byte[] data,UIntPtr size,out UIntPtr written);
        [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr CreateRemoteThread(IntPtr process,IntPtr security,UIntPtr stack,IntPtr start,IntPtr arg,uint flags,IntPtr id);
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetExitCodeThread(IntPtr thread,out uint result);
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetExitCodeProcess(IntPtr process,out uint result);
        [DllImport("kernel32.dll",SetLastError=true)]static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll")]static extern uint WaitForSingleObject(IntPtr handle,uint ms);
        [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool TerminateProcess(IntPtr process,uint code);
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern IntPtr GetModuleHandle(string name);
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool GetModuleHandleEx(uint flags,IntPtr address,out IntPtr module);
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern uint GetModuleFileName(IntPtr module,StringBuilder filename,int size);
        [DllImport("kernel32.dll")]static extern IntPtr GetProcAddress(IntPtr module,string name);
        [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr CreateToolhelp32Snapshot(uint flags,uint pid);
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool Module32FirstW(IntPtr snapshot,ref MODULEENTRY32 entry);
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool Module32NextW(IntPtr snapshot,ref MODULEENTRY32 entry);
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern IntPtr CreateJobObject(IntPtr attributes,string name);
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetInformationJobObject(IntPtr job,int info,IntPtr data,uint length);
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    }
}
