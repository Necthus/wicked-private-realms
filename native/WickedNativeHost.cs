using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Collections;
using System.ComponentModel;

internal sealed class NativeConfig {
    public string magic="WRNAT01"; public uint version=1,size=6340;
    public int status,error; public uint image_size,image_timestamp;
    public string steam_id,endpoint,token; public string log_path; public uint flags;
}
internal sealed class PrivateHost {
    const uint CREATE_SUSPENDED=4,CREATE_UNICODE_ENVIRONMENT=0x400,INFINITE=0xffffffff;
    const uint MEM_COMMIT=0x1000,MEM_RESERVE=0x2000,PAGE_READWRITE=0x04;
    const uint FILE_MAP_ALL_ACCESS=0xf001f;
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr CreateFileMapping(IntPtr file,IntPtr attr,uint protect,uint maxHigh,uint maxLow,string name);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr MapViewOfFile(IntPtr map,uint access,uint high,uint low,UIntPtr size);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool UnmapViewOfFile(IntPtr p); [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool CreateProcess(string app,string cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref STARTUPINFO si,out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr VirtualAllocEx(IntPtr p,IntPtr addr,UIntPtr size,uint type,uint protect);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool WriteProcessMemory(IntPtr p,IntPtr addr,byte[] data,UIntPtr size,out UIntPtr written);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr CreateRemoteThread(IntPtr p,IntPtr attr,UIntPtr stack,IntPtr start,IntPtr arg,uint flags,IntPtr id);
    [DllImport("kernel32.dll")] static extern uint ResumeThread(IntPtr thread); [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint ms);
    [DllImport("kernel32.dll")] static extern IntPtr GetProcAddress(IntPtr module,string name); [DllImport("kernel32.dll")] static extern IntPtr GetModuleHandle(string name);
    [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr p,uint code);
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct STARTUPINFO { public int cb; public string reserved,desktop,title; public int x,y,xSize,ySize,xCount,yCount,fill,flags; public short show,reserved2; public IntPtr reserved3,stdin,stdout,stderr; }
    [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION { public IntPtr process,thread; public uint pid,tid; }
    readonly JavaScriptSerializer json=new JavaScriptSerializer(); readonly string root; readonly string server; readonly string password; readonly string steamId; readonly bool checkOnly;
    string token; string fingerprint; IntPtr map,view; HttpListener apiListener; TcpListener gameListener; Thread apiThread,gameThread; volatile bool stopping;
    public PrivateHost(string root,Dictionary<string,object> input,bool check) { this.root=root;server=Convert.ToString(input["server"]);password=Convert.ToString(input["password"]);steamId=Convert.ToString(input["steam_id"]);checkOnly=check; }
    static string Sha(byte[] bytes){using(var sha=SHA256.Create()){var b=sha.ComputeHash(bytes);var s=new StringBuilder();foreach(var x in b)s.Append(x.ToString("x2"));return s.ToString();}}
    void NativeLog(string eventName,int value){try{Directory.CreateDirectory(Path.Combine(root,"research"));File.AppendAllText(Path.Combine(root,"research","native-host.jsonl"),DateTime.UtcNow.ToString("o")+" "+eventName+" "+value+Environment.NewLine);}catch{}}
    Uri ServerUri(string path){var value=server.Contains("://")?server:"https://"+server;var uri=new Uri(value);return new Uri(uri,"/"+path.TrimStart('/'));}
    string Request(string method,string path,string body=null){var uri=ServerUri(path);var request=(HttpWebRequest)WebRequest.Create(uri);request.Method=method;request.Timeout=15000;request.ServerCertificateValidationCallback=ValidateCert;request.ContentType="application/json";if(body!=null){var bytes=Encoding.UTF8.GetBytes(body);request.ContentLength=bytes.Length;using(var stream=request.GetRequestStream())stream.Write(bytes,0,bytes.Length);}using(var response=(HttpWebResponse)request.GetResponse())using(var reader=new StreamReader(response.GetResponseStream()))return reader.ReadToEnd();}
    bool ValidateCert(object sender,X509Certificate cert,X509Chain chain,SslPolicyErrors errors){var value=Sha(cert.GetRawCertData());var trust=Path.Combine(root,"data/server-trust");Directory.CreateDirectory(trust);var file=Path.Combine(trust,Sha(Encoding.UTF8.GetBytes(server.ToLowerInvariant()))+".txt");if(File.Exists(file))return File.ReadAllText(file).Trim().Equals(value,StringComparison.OrdinalIgnoreCase);File.WriteAllText(file,value);fingerprint=value;return true;}
    Dictionary<string,object> Login(){var result=Json(Request("POST","/game/launcher/login",json.Serialize(new {steam_id=steamId,password=password})));token=Convert.ToString(result["token"]);Console.WriteLine((Convert.ToBoolean(result["created"])?"Created account: ":"Signed in: ")+steamId);return result;}
    Dictionary<string,object> Json(string value){return json.Deserialize<Dictionary<string,object>>(value);}
    public int Run(){ServicePointManager.SecurityProtocol=SecurityProtocolType.Tls12;ServicePointManager.ServerCertificateValidationCallback=(sender,cert,chain,errors)=>true;if(string.IsNullOrWhiteSpace(server)||string.IsNullOrWhiteSpace(password)||steamId.Length!=17)throw new Exception("Server, password and 17-digit Steam ID are required");Login();if(checkOnly){Console.WriteLine("Direct TLS login verified; no game was launched.");return 0;} StartBridge(); StartGame();return 0;}
    void StartBridge(){
        apiListener=new HttpListener();apiListener.Prefixes.Add("http://127.0.0.1:18770/");apiListener.Start();
        apiThread=new Thread(ApiLoop);apiThread.IsBackground=true;apiThread.Start();
        gameListener=new TcpListener(System.Net.IPAddress.Loopback,18769);gameListener.Start();
        gameThread=new Thread(GameLoop);gameThread.IsBackground=true;gameThread.Start();
    }
    void ApiLoop(){while(!stopping){try{var context=apiListener.GetContext();ThreadPool.QueueUserWorkItem(_=>ProxyApi(context));}catch{if(!stopping)Thread.Sleep(50);}}}
    void ProxyApi(HttpListenerContext context){try{
        if(context.Request.HttpMethod=="GET" && context.Request.Url.AbsolutePath=="/health"){byte[] health=Encoding.UTF8.GetBytes("{\"status\":\"ok\",\"service\":\"wicked-private-realms\",\"transport\":\"native\"}");context.Response.ContentLength64=health.Length;context.Response.OutputStream.Write(health,0,health.Length);context.Response.Close();return;}
        if(!context.Request.Url.AbsolutePath.StartsWith("/game/")){context.Response.StatusCode=404;context.Response.Close();return;}
        byte[] body=null;if(context.Request.HasEntityBody){using(var input=context.Request.InputStream){using(var memory=new MemoryStream()){input.CopyTo(memory);body=memory.ToArray();}}}
        var request=(HttpWebRequest)WebRequest.Create(ServerUri(context.Request.HttpMethod=="GET"?context.Request.Url.AbsolutePath:context.Request.Url.AbsolutePath));request.Method=context.Request.HttpMethod;request.Timeout=30000;request.ServerCertificateValidationCallback=ValidateCert;request.ContentType=context.Request.ContentType;
        string authorization=context.Request.Headers["Authorization"];if(!string.IsNullOrEmpty(authorization))request.Headers["Authorization"]=authorization; if(body!=null){request.ContentLength=body.Length;using(var stream=request.GetRequestStream())stream.Write(body,0,body.Length);}
        using(var response=(HttpWebResponse)request.GetResponse()){context.Response.StatusCode=(int)response.StatusCode;using(var input=response.GetResponseStream()){input.CopyTo(context.Response.OutputStream);}}
        context.Response.Close();
    }catch(WebException ex){context.Response.StatusCode=502;byte[] message=Encoding.UTF8.GetBytes("{\"error\":\"native API proxy failed\"}");context.Response.ContentLength64=message.Length;try{context.Response.OutputStream.Write(message,0,message.Length);context.Response.Close();}catch{ } NativeLog("native-api-proxy-failed",ex.HResult);}
    catch(Exception ex){try{context.Response.StatusCode=500;context.Response.Close();}catch{}NativeLog("native-api-proxy-exception",ex.HResult);}}
    void GameLoop(){while(!stopping){try{TcpClient local=gameListener.AcceptTcpClient();ThreadPool.QueueUserWorkItem(_=>ProxyGame(local));}catch{if(!stopping)Thread.Sleep(50);}}}
    void ProxyGame(TcpClient local){
        try {
            using(local) using(var remote=new TcpClient()) {
                Uri uri=ServerUri("transport"); remote.Connect(uri.Host,uri.Port);
                using(var ssl=new SslStream(remote.GetStream(),false,(sender,certificate,chain,errors)=>ValidateCert(null,certificate,chain,errors))) {
                    ssl.AuthenticateAsClient(uri.Host);
                    string request="CONNECT /transport HTTP/1.1\r\nHost: "+uri.Host+"\r\nAuthorization: Bearer "+token+"\r\n\r\n";
                    byte[] header=Encoding.ASCII.GetBytes(request);ssl.Write(header,0,header.Length);ssl.Flush();
                    string response=ReadHeaders(ssl);if(response.IndexOf(" 200 ",StringComparison.Ordinal)<0)throw new Exception("Native TLS transport rejected");
                    CopyBoth(local.GetStream(),ssl);
                }
            }
        } catch(Exception ex) { NativeLog("native-game-proxy-failed",ex.HResult); }
    }
    string ReadHeaders(Stream stream){var bytes=new List<byte>();while(bytes.Count<16384){int value=stream.ReadByte();if(value<0)break;bytes.Add((byte)value);int count=bytes.Count;if(count>=4&&bytes[count-4]==13&&bytes[count-3]==10&&bytes[count-2]==13&&bytes[count-1]==10)break;}return Encoding.ASCII.GetString(bytes.ToArray());}
    void CopyBoth(Stream a,Stream b){var finished=new ManualResetEvent(false);Thread first=new Thread(()=>Copy(a,b,finished));Thread second=new Thread(()=>Copy(b,a,finished));first.Start();second.Start();finished.WaitOne();try{a.Close();b.Close();}catch{}first.Join(1000);second.Join(1000);}
    void Copy(Stream input,Stream output,ManualResetEvent finished){try{byte[] buffer=new byte[65536];while(!stopping){int count=input.Read(buffer,0,buffer.Length);if(count<=0)break;output.Write(buffer,0,count);output.Flush();}}catch{}finally{finished.Set();}}
    void InstallGse(){if(!File.Exists(Path.Combine(root,"plugin/gse/steam_api64.dll")))return;string game=Directory.GetParent(root.TrimEnd('\\')).FullName;string target=Path.Combine(game,"NoRestForTheWicked_Data","Plugins","x86_64");Directory.CreateDirectory(target);string current=Path.Combine(target,"steam_api64.dll");string source=Path.Combine(root,"plugin/gse","steam_api64.dll");if(!File.Exists(current)||ShaFile(current)!=ShaFile(source)){string backup=Path.Combine(root,"data","native-gse-backups",DateTime.UtcNow.ToString("yyyyMMddTHHmmssZ"));Directory.CreateDirectory(backup);if(File.Exists(current))File.Copy(current,Path.Combine(backup,"steam_api64.dll"));File.Copy(source,current,true);}string settings=Path.Combine(root,"plugin/gse","steam_settings");string destination=Path.Combine(target,"steam_settings");Directory.CreateDirectory(destination);foreach(string file in Directory.GetFiles(settings,"*",SearchOption.AllDirectories)){string relative=file.Substring(settings.Length).TrimStart('\\','/');string dest=Path.Combine(destination,relative);Directory.CreateDirectory(Path.GetDirectoryName(dest));File.Copy(file,dest,true);}string config=Path.Combine(destination,"configs.user.ini");if(File.Exists(config)){string text=File.ReadAllText(config);text=System.Text.RegularExpressions.Regex.Replace(text,"(?m)^\\s*account_steamid\\s*=.*$","account_steamid="+steamId);File.WriteAllText(config,text,new UTF8Encoding(false));}}
    string ShaFile(string path){using(var sha=SHA256.Create())using(var stream=File.OpenRead(path)){var bytes=sha.ComputeHash(stream);var text=new StringBuilder();foreach(var b in bytes)text.Append(b.ToString("x2"));return text.ToString();}}
    void StartGame(){InstallGse();string game=Directory.GetParent(root.TrimEnd('\\')).FullName;string exe=Path.Combine(game,"NoRestForTheWicked.exe");string mappingName="Local\\WickedNative-"+Guid.NewGuid().ToString("N");map=CreateFileMapping(new IntPtr(-1),IntPtr.Zero,4,0,8192,mappingName);if(map==IntPtr.Zero)throw new Win32Exception(Marshal.GetLastWin32Error());view=MapViewOfFile(map,FILE_MAP_ALL_ACCESS,0,0,new UIntPtr(8192));if(view==IntPtr.Zero)throw new Win32Exception(Marshal.GetLastWin32Error());var config=new byte[8192];WriteNativeConfig(config,game);Marshal.Copy(config,0,view,config.Length);string old=Environment.GetEnvironmentVariable("WICKED_REALM_MAPPING");Environment.SetEnvironmentVariable("WICKED_REALM_MAPPING",mappingName);try{STARTUPINFO startup=new STARTUPINFO();startup.cb=Marshal.SizeOf(startup);PROCESS_INFORMATION info;string command="\""+exe+"\"";if(!CreateProcess(exe,command,IntPtr.Zero,IntPtr.Zero,false,CREATE_SUSPENDED|CREATE_UNICODE_ENVIRONMENT,IntPtr.Zero,game,ref startup,out info))throw new Win32Exception(Marshal.GetLastWin32Error());try{string adapter=Path.Combine(root,"WickedAdapter.dll");if(!File.Exists(adapter))adapter=Path.Combine(root,"native","dist","WickedAdapter.dll");Inject(info.process,adapter);ResumeThread(info.thread);WaitForSingleObject(info.process,INFINITE);}finally{CloseHandle(info.thread);CloseHandle(info.process);}}finally{Environment.SetEnvironmentVariable("WICKED_REALM_MAPPING",old);}}
    void WriteNativeConfig(byte[] buffer,string game){Encoding.ASCII.GetBytes("WRNAT01").CopyTo(buffer,0);BitConverter.GetBytes((uint)1).CopyTo(buffer,8);BitConverter.GetBytes((uint)6340).CopyTo(buffer,12);var pe=File.ReadAllBytes(Path.Combine(game,"GameAssembly.dll"));int offset=BitConverter.ToInt32(pe,0x3c);BitConverter.GetBytes(BitConverter.ToUInt32(pe,offset+0x50)).CopyTo(buffer,24);BitConverter.GetBytes(BitConverter.ToUInt32(pe,offset+8)).CopyTo(buffer,28);Encoding.ASCII.GetBytes(steamId+"\0").CopyTo(buffer,32);Encoding.ASCII.GetBytes("http://127.0.0.1:18770/game\0").CopyTo(buffer,64);Encoding.ASCII.GetBytes(token+"\0").CopyTo(buffer,192);string log=Path.Combine(root,"research","native-adapter.jsonl");Encoding.Unicode.GetBytes(log+"\0").CopyTo(buffer,4288);BitConverter.GetBytes((uint)1).CopyTo(buffer,6336);}
    void Inject(IntPtr process,string dll){byte[] path=Encoding.Unicode.GetBytes(dll+"\0");IntPtr address=VirtualAllocEx(process,IntPtr.Zero,new UIntPtr((uint)path.Length),MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);UIntPtr written;if(address==IntPtr.Zero||!WriteProcessMemory(process,address,path,new UIntPtr((uint)path.Length),out written))throw new Win32Exception(Marshal.GetLastWin32Error());IntPtr load=GetProcAddress(GetModuleHandle("kernel32.dll"),"LoadLibraryW");IntPtr thread=CreateRemoteThread(process,IntPtr.Zero,UIntPtr.Zero,load,address,0,IntPtr.Zero);if(thread==IntPtr.Zero)throw new Win32Exception(Marshal.GetLastWin32Error());WaitForSingleObject(thread,15000);CloseHandle(thread);}
    public static int Main(string[] args){try{var input=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(Console.In.ReadLine());var host=new PrivateHost(AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'),input,Array.IndexOf(args,"--check")>=0);return host.Run();}catch(Exception e){Console.Error.WriteLine("ERROR: "+e.Message);return 1;}}
}

