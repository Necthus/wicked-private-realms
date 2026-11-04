using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;

namespace Wicked.Native {
    internal static class Steam {
        internal const string GseHash="723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88";
        internal static string Validate(string text){ulong id;if(text==null||!Regex.IsMatch(text,@"^[0-9]{17}$")||!ulong.TryParse(text,out id)||id>>56!=1||((id>>52)&15)!=1||((id>>32)&0xfffff)!=1||(id&0xffffffff)==0)throw new Exception("Enter a public individual SteamID64 (17 digits)");return text;}
        internal static string Plugin(string game){return Path.Combine(game,"NoRestForTheWicked_Data","Plugins","x86_64");}
        internal static void Install(string game,string root,string id){
            id=Validate(id);string source=Path.Combine(root,"plugin","gse");string dll=Path.Combine(source,"steam_api64.dll");
            if(!File.Exists(dll)||Crypto.ShaFile(dll)!=GseHash)throw new Exception("The bundled GSE DLL does not match the approved build");
            var manifest=Program.Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(source,"manifest.json")));
            var files=(Dictionary<string,object>)manifest["files"];
            foreach(var entry in files){string relative=entry.Key;if(Path.IsPathRooted(relative)||relative.Contains("..")||relative.Contains("\\"))throw new Exception("Invalid GSE manifest path");
                string file=Path.Combine(source,relative.Replace('/',Path.DirectorySeparatorChar));if(!File.Exists(file)||Crypto.ShaFile(file)!=Convert.ToString(entry.Value))throw new Exception("GSE bundle checksum failed: "+relative);}
            string target=Plugin(game);Directory.CreateDirectory(target);string current=Path.Combine(target,"steam_api64.dll");
            bool same=File.Exists(current)&&Crypto.ShaFile(current)==GseHash;
            var copies=new Dictionary<string,string>();if(!same)copies[dll]=current;
            string settings=Path.Combine(source,"steam_settings");
            foreach(string file in Directory.GetFiles(settings,"*",SearchOption.AllDirectories)){
                string dest=Path.Combine(target,"steam_settings",file.Substring(settings.Length).TrimStart('\\','/'));
                if(!same||!File.Exists(dest))copies[file]=dest;
            }
            if(copies.Count>0){
                string backup=Path.Combine(root,"data","gse-backups",DateTime.UtcNow.ToString("yyyyMMddTHHmmssfffffffZ"));Directory.CreateDirectory(backup);
                if(File.Exists(current))File.Copy(current,Path.Combine(backup,"steam_api64.dll"));
                if(Directory.Exists(Path.Combine(target,"steam_settings")))CopyTree(Path.Combine(target,"steam_settings"),Path.Combine(backup,"steam_settings"));
                var original=new Dictionary<string,byte[]>();var changed=new List<string>();
                foreach(string dest in copies.Values)original[dest]=File.Exists(dest)?File.ReadAllBytes(dest):null;
                try{foreach(var entry in copies){Directory.CreateDirectory(Path.GetDirectoryName(entry.Value));File.Copy(entry.Key,entry.Value,true);changed.Add(entry.Value);}}
                catch{foreach(string dest in changed){if(original[dest]==null)File.Delete(dest);else File.WriteAllBytes(dest,original[dest]);}throw;}
                Console.WriteLine("GSE installed; previous files backed up to "+backup);
            }else Console.WriteLine("GSE compatibility: approved build verified");
            BindIni(Path.Combine(target,"steam_settings","configs.user.ini"),id,Path.Combine(root,"data","steam-config-backups"));
        }
        static void CopyTree(string source,string target){Directory.CreateDirectory(target);foreach(string file in Directory.GetFiles(source,"*",SearchOption.AllDirectories)){string dest=Path.Combine(target,file.Substring(source.Length).TrimStart('\\'));Directory.CreateDirectory(Path.GetDirectoryName(dest));File.Copy(file,dest);}}
        internal static void BindIni(string file,string id,string backups){
            id=Validate(id);byte[] original=File.ReadAllBytes(file);bool bom=original.Length>=3&&original[0]==239&&original[1]==187&&original[2]==191;
            string text=new UTF8Encoding(false,true).GetString(original,bom?3:0,original.Length-(bom?3:0));
            var section=Regex.Match(text,@"(?ms)^\[user::general\][^\r\n]*\r?\n(.*?)(?=^\[|\z)");
            if(!section.Success)throw new Exception("GSE configuration is missing [user::general]");
            string body=section.Groups[1].Value;var key=new Regex(@"(?m)^([ \t]*account_steamid[ \t]*=[ \t]*)[^\r\n]*");
            body=key.IsMatch(body)?key.Replace(body,m=>m.Groups[1].Value+id):body+"account_steamid="+id+(text.Contains("\r\n")?"\r\n":"\n");
            string updated=text.Substring(0,section.Groups[1].Index)+body+text.Substring(section.Groups[1].Index+section.Groups[1].Length);
            if(updated==text)return;Directory.CreateDirectory(backups);File.Copy(file,Path.Combine(backups,"configs.user-"+DateTime.UtcNow.ToString("yyyyMMddTHHmmssfffffffZ")+".ini"));
            byte[] contents=new UTF8Encoding(bom).GetBytes(updated);if(bom){byte[] preamble=new byte[]{239,187,191};byte[] all=new byte[preamble.Length+contents.Length];preamble.CopyTo(all,0);contents.CopyTo(all,3);contents=all;}
            string temporary=file+".native.tmp";try{File.WriteAllBytes(temporary,contents);File.Replace(temporary,file,null);}finally{if(File.Exists(temporary))File.Delete(temporary);}
        }
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr LoadLibraryEx(string path,IntPtr file,uint flags);
        [DllImport("kernel32.dll")] static extern IntPtr GetProcAddress(IntPtr module,string name);
        [DllImport("kernel32.dll")] static extern bool FreeLibrary(IntPtr module);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] [return:MarshalAs(UnmanagedType.I1)] delegate bool Init();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void Shutdown();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate IntPtr Interface();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate ulong SteamId(IntPtr user);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate IntPtr Persona(IntPtr friends);
        static T Function<T>(IntPtr module,string name)where T:class{IntPtr address=GetProcAddress(module,name);if(address==IntPtr.Zero)throw new Exception("Missing Steam API: "+name);return Marshal.GetDelegateForFunctionPointer(address,typeof(T)) as T;}
        static IntPtr GetInterface(IntPtr module,string prefix,int newest,int oldest){for(int version=newest;version>=oldest;version--){IntPtr p=GetProcAddress(module,prefix+version.ToString("D3"));if(p!=IntPtr.Zero){var f=(Interface)Marshal.GetDelegateForFunctionPointer(p,typeof(Interface));IntPtr result=f();if(result!=IntPtr.Zero)return result;}}throw new Exception("Unsupported Steam interface version");}
        internal static Dictionary<string,object> ReadNative(string game){
            game=Path.GetFullPath(game);Directory.SetCurrentDirectory(game);Environment.SetEnvironmentVariable("SteamAppId","1371980");Environment.SetEnvironmentVariable("SteamGameId","1371980");
            IntPtr module=LoadLibraryEx(Path.Combine(Plugin(game),"steam_api64.dll"),IntPtr.Zero,8);
            if(module==IntPtr.Zero)throw new Win32Exception(Marshal.GetLastWin32Error());
            bool initialized=false;
            try{initialized=Function<Init>(module,"SteamAPI_InitSafe")();if(!initialized)throw new Exception("Steam initialization failed; sign into Steam first");
                IntPtr user=GetInterface(module,"SteamAPI_SteamUser_v",23,20);string id=Validate(Function<SteamId>(module,"SteamAPI_ISteamUser_GetSteamID")(user).ToString());
                IntPtr friends=GetInterface(module,"SteamAPI_SteamFriends_v",18,17);IntPtr raw=Function<Persona>(module,"SteamAPI_ISteamFriends_GetPersonaName")(friends);
                var bytes=new List<byte>();if(raw!=IntPtr.Zero)for(int i=0;i<4096;i++){byte value=Marshal.ReadByte(raw,i);if(value==0)break;bytes.Add(value);}
                return new Dictionary<string,object>{{"steam_id",id},{"display_name",Encoding.UTF8.GetString(bytes.ToArray())}};
            }finally{if(initialized)Function<Shutdown>(module,"SteamAPI_Shutdown")();FreeLibrary(module);}
        }
        internal static Dictionary<string,object> ReadHelper(string game){
            var info=new ProcessStartInfo(Path.Combine(Program.Root,"WickedNativeHost.exe"),"--steam-identity \""+game+"\""){
                WorkingDirectory=game,UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
            using(var process=Process.Start(info)){
                var stdout=process.StandardOutput.ReadToEndAsync();var stderr=process.StandardError.ReadToEndAsync();
                if(!process.WaitForExit(15000)){process.Kill();process.WaitForExit();throw new Exception("Steam identity helper timed out");}
                string output=stdout.Result;
                foreach(string line in output.Split('\n'))if(line.Trim().StartsWith("WICKED_STEAM_IDENTITY=")&&process.ExitCode==0)return Program.Json.Deserialize<Dictionary<string,object>>(line.Trim().Substring("WICKED_STEAM_IDENTITY=".Length));
                throw new Exception("Cannot read game Steam identity: "+stderr.Result.Trim());
            }
        }
    }
}
