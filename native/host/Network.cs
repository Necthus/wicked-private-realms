using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;

namespace Wicked.Native {
    internal sealed class Address {
        internal string Host;internal int Port;
        internal static Address Parse(string value){
            value=value.Trim();bool scheme=value.Contains("://");
            Uri uri;if(!Uri.TryCreate(scheme?value:"https://"+value,UriKind.Absolute,out uri)||uri.Scheme!="https"||
                string.IsNullOrEmpty(uri.Host)||!string.IsNullOrEmpty(uri.UserInfo)||uri.AbsolutePath!="/"||
                !string.IsNullOrEmpty(uri.Query)||!string.IsNullOrEmpty(uri.Fragment))throw new Exception("Use a hostname or IP, optionally followed by :TLS-port");
            return new Address {Host=uri.Host.Trim('[',']'),Port=(!scheme&&uri.IsDefaultPort)?8443:uri.Port};
        }
        internal string Authority {get{return (Host.Contains(":")?"["+Host+"]":Host)+":"+Port;}}
    }
    internal static class Crypto {
        internal static string Sha(byte[] bytes){using(var sha=SHA256.Create())return Hex(sha.ComputeHash(bytes));}
        internal static string ShaFile(string path){using(var sha=SHA256.Create())using(var stream=File.OpenRead(path))return Hex(sha.ComputeHash(stream));}
        static string Hex(byte[] bytes){var text=new StringBuilder();foreach(byte value in bytes)text.Append(value.ToString("x2"));return text.ToString();}
    }
    internal sealed class HttpMessage {
        internal int Status;internal string Start;internal byte[] Body;
        internal Dictionary<string,string> Headers=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
        internal const int Limit=192*1024*1024;
        internal static HttpMessage ReadHead(Stream stream){
            var bytes=new List<byte>();
            while(bytes.Count<=16384){int value=stream.ReadByte();if(value<0)throw new IOException("Connection closed while reading HTTP header");bytes.Add((byte)value);
                int n=bytes.Count;if(n>=4&&bytes[n-4]==13&&bytes[n-3]==10&&bytes[n-2]==13&&bytes[n-1]==10)break;}
            if(bytes.Count>16384)throw new IOException("HTTP header limit exceeded");
            string[] lines=Encoding.ASCII.GetString(bytes.ToArray()).Split(new[]{"\r\n"},StringSplitOptions.None);
            var result=new HttpMessage {Start=lines[0]};string[] first=lines[0].Split(' ');
            if(first.Length>=2&&first[0].StartsWith("HTTP/"))result.Status=int.Parse(first[1]);
            for(int i=1;i<lines.Length;i++){int colon=lines[i].IndexOf(':');if(colon>0)result.Headers[lines[i].Substring(0,colon)]=lines[i].Substring(colon+1).Trim();}
            return result;
        }
        internal void ReadBody(Stream stream,bool untilClose){
            string value;if(Headers.TryGetValue("Transfer-Encoding",out value)){
                if(!value.Equals("chunked",StringComparison.OrdinalIgnoreCase))throw new IOException("Unsupported transfer encoding");
                using(var body=new MemoryStream()){
                    while(true){string line=Line(stream);int semi=line.IndexOf(';');if(semi>=0)line=line.Substring(0,semi);
                        long length=long.Parse(line,System.Globalization.NumberStyles.HexNumber);if(length<0||length>Limit-body.Length)throw new IOException("Body exceeds limit");
                        if(length==0){while(Line(stream).Length!=0){}break;}byte[] data=ReadExact(stream,(int)length);body.Write(data,0,data.Length);if(Line(stream)!="")throw new IOException("Invalid chunk terminator");}
                    Body=body.ToArray();return;
                }
            }
            if(Headers.TryGetValue("Content-Length",out value)){long length=long.Parse(value);if(length<0||length>Limit)throw new IOException("Body exceeds limit");Body=ReadExact(stream,(int)length);return;}
            if(!untilClose||Status==204||Status==304){Body=new byte[0];return;}
            using(var body=new MemoryStream()){byte[] buffer=new byte[65536];int count;while((count=stream.Read(buffer,0,buffer.Length))>0){if(body.Length+count>Limit)throw new IOException("Body exceeds limit");body.Write(buffer,0,count);}Body=body.ToArray();}
        }
        static string Line(Stream stream){var bytes=new List<byte>();while(bytes.Count<16384){int value=stream.ReadByte();if(value<0)throw new IOException("Truncated HTTP line");if(value==10){if(bytes.Count==0||bytes[bytes.Count-1]!=13)throw new IOException("Invalid HTTP line");bytes.RemoveAt(bytes.Count-1);return Encoding.ASCII.GetString(bytes.ToArray());}bytes.Add((byte)value);}throw new IOException("HTTP line too long");}
        internal static byte[] ReadExact(Stream stream,int size){var data=new byte[size];int offset=0;while(offset<size){int n=stream.Read(data,offset,size-offset);if(n==0)throw new IOException("Truncated HTTP body");offset+=n;}return data;}
        internal static void WriteResponse(Stream stream,int status,byte[] body,Dictionary<string,string> headers){
            var head=new StringBuilder("HTTP/1.1 "+status+" Response\r\nConnection: close\r\nContent-Length: "+body.Length+"\r\n");
            foreach(var pair in headers)if(pair.Key.Equals("Content-Type",StringComparison.OrdinalIgnoreCase)||pair.Key.Equals("Current-Epoch-Ms",StringComparison.OrdinalIgnoreCase))head.Append(pair.Key+": "+pair.Value+"\r\n");
            head.Append("\r\n");byte[] bytes=Encoding.ASCII.GetBytes(head.ToString());stream.Write(bytes,0,bytes.Length);stream.Write(body,0,body.Length);stream.Flush();
        }
    }
    internal sealed class TlsConnection:IDisposable {
        internal TcpClient Tcp;internal SslStream Stream;
        public void Dispose(){try{if(Stream!=null)Stream.Dispose();}finally{if(Tcp!=null)Tcp.Close();}}
    }
    internal sealed class PrivateTls {
        readonly Address address;readonly string directory;readonly object gate=new object();
        internal PrivateTls(Address address,string trust){this.address=address;directory=trust;}
        bool Certificate(object sender,X509Certificate certificate,X509Chain chain,SslPolicyErrors errors){
            if(certificate==null)return false;string fingerprint=Crypto.Sha(certificate.GetRawCertData());
            string identity=address.Host.ToLowerInvariant()+":"+address.Port;
            string file=Path.Combine(directory,Crypto.Sha(Encoding.UTF8.GetBytes(identity))+".json");
            lock(gate){Directory.CreateDirectory(directory);
                if(File.Exists(file)){var saved=Program.Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(file));return Program.Value(saved,"sha256")==fingerprint;}
                File.WriteAllText(file,Program.Json.Serialize(new {host=address.Host,port=address.Port,sha256=fingerprint}),new UTF8Encoding(false));
                Console.WriteLine("Server TLS certificate SHA-256: "+fingerprint);return true;
            }
        }
        internal TlsConnection Connect(){
            var result=new TlsConnection {Tcp=new TcpClient()};
            try{IAsyncResult connect=result.Tcp.BeginConnect(address.Host,address.Port,null,null);
                using(connect.AsyncWaitHandle){if(!connect.AsyncWaitHandle.WaitOne(15000))throw new IOException("Server TCP connection timed out");result.Tcp.EndConnect(connect);}
                result.Tcp.NoDelay=true;result.Tcp.ReceiveTimeout=15000;result.Tcp.SendTimeout=15000;
                result.Stream=new SslStream(result.Tcp.GetStream(),false,Certificate);
                result.Stream.AuthenticateAsClient(address.Host,null,SslProtocols.Tls12,false);return result;
            }catch{result.Dispose();throw;}
        }
        internal HttpMessage Request(string method,string path,byte[] body,Dictionary<string,string> headers){
            if(!path.StartsWith("/")||path.Contains("\r")||path.Contains("\n"))throw new IOException("Invalid request path");
            using(var connection=Connect()){
                var head=new StringBuilder(method+" "+path+" HTTP/1.1\r\nHost: "+address.Authority+"\r\nConnection: close\r\n");
                foreach(var pair in headers)if(pair.Key.Equals("Authorization",StringComparison.OrdinalIgnoreCase)||pair.Key.Equals("Content-Type",StringComparison.OrdinalIgnoreCase)){
                    if(pair.Value.Contains("\r")||pair.Value.Contains("\n"))throw new IOException("Invalid HTTP header");head.Append(pair.Key+": "+pair.Value+"\r\n");}
                if(body!=null)head.Append("Content-Length: "+body.Length+"\r\n");head.Append("\r\n");
                byte[] data=Encoding.ASCII.GetBytes(head.ToString());connection.Stream.Write(data,0,data.Length);if(body!=null)connection.Stream.Write(body,0,body.Length);connection.Stream.Flush();
                var result=HttpMessage.ReadHead(connection.Stream);result.ReadBody(connection.Stream,true);return result;
            }
        }
        internal TlsConnection Transport(string token){
            var connection=Connect();
            try{byte[] request=Encoding.ASCII.GetBytes("CONNECT /transport HTTP/1.1\r\nHost: "+address.Authority+"\r\nAuthorization: Bearer "+token+"\r\n\r\n");
                connection.Stream.Write(request,0,request.Length);connection.Stream.Flush();var response=HttpMessage.ReadHead(connection.Stream);
                if(response.Status!=200)throw new IOException("Transport gateway rejected the session (HTTP "+response.Status+")");
                connection.Tcp.ReceiveTimeout=0;connection.Tcp.SendTimeout=0;return connection;
            }catch{connection.Dispose();throw;}
        }
    }
    internal sealed class Bridge:IDisposable {
        readonly PrivateTls tls;readonly string token;readonly object gate=new object();readonly List<IDisposable> active=new List<IDisposable>();
        TcpListener api,game;Thread apiThread,gameThread;volatile bool stopping;
        internal Bridge(PrivateTls tls,string token){this.tls=tls;this.token=token;}
        internal void Start(){try{api=new TcpListener(IPAddress.Loopback,18770);api.Start();game=new TcpListener(IPAddress.Loopback,18769);game.Start();apiThread=StartThread(()=>Accept(api,false));gameThread=StartThread(()=>Accept(game,true));}catch{Dispose();throw;}}
        static Thread StartThread(ThreadStart action){var thread=new Thread(action){IsBackground=true};thread.Start();return thread;}
        void Add(IDisposable value){lock(gate){if(stopping){value.Dispose();throw new IOException("Bridge stopped");}active.Add(value);}}
        void Remove(IDisposable value){lock(gate)active.Remove(value);}
        void Accept(TcpListener listener,bool transport){while(!stopping){try{var client=listener.AcceptTcpClient();client.NoDelay=true;Add(client);StartThread(()=>Handle(client,transport));}catch{if(!stopping)Thread.Sleep(20);}}}
        void Handle(TcpClient client,bool transport){
            try{using(client){if(transport){using(var remote=tls.Transport(token)){Add(remote);try{Relay(client.GetStream(),remote.Stream,()=>{client.Close();remote.Dispose();});}finally{Remove(remote);}}}
                else{client.ReceiveTimeout=30000;var input=client.GetStream();var request=HttpMessage.ReadHead(input);string[] first=request.Start.Split(' ');
                    if(first.Length<3||(first[0]!="GET"&&first[0]!="POST")||!(first[1]=="/health"||first[1].StartsWith("/game/")))throw new IOException("Invalid local API request");
                    request.ReadBody(input,false);var response=tls.Request(first[0],first[1],first[0]=="POST"?request.Body:null,request.Headers);
                    HttpMessage.WriteResponse(input,response.Status,response.Body,response.Headers);
                }
            }}catch(Exception error){if(!stopping)Console.Error.WriteLine("Bridge: "+error.Message);}finally{Remove(client);}
        }
        static void Relay(Stream a,Stream b,Action close){
            var done=new ManualResetEvent(false);Thread first=StartThread(()=>Copy(a,b,done));Thread second=StartThread(()=>Copy(b,a,done));
            done.WaitOne();close();first.Join(3000);second.Join(3000);if(!first.IsAlive&&!second.IsAlive)done.Dispose();
        }
        static void Copy(Stream input,Stream output,ManualResetEvent done){try{byte[] buffer=new byte[65536];int count;while((count=input.Read(buffer,0,buffer.Length))>0){output.Write(buffer,0,count);output.Flush();}}catch{}finally{done.Set();}}
        internal void CheckApi(){
            var body=Encoding.UTF8.GetBytes(Program.Json.Serialize(new {entitlementTicket=token,entitlementPlatform="private"}));
            var response=tls.Request("POST","/game/auth/v3/token",body,new Dictionary<string,string>{{"Content-Type","application/json"}});
            if(response.Status!=200)throw new Exception("Game authentication failed");
            var result=Program.Json.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(response.Body));
            var data=(System.Collections.IList)result["data"];var session=(Dictionary<string,object>)data[0];
            response=tls.Request("POST","/game/rest/save/"+Program.Value(session,"accountId")+"/ListRealms",Encoding.UTF8.GetBytes("{}"),
                new Dictionary<string,string>{{"Content-Type","application/json"},{"Authorization","Bearer "+Program.Value(session,"token")}});
            if(response.Status!=200)throw new Exception("Realm list request failed");
            Console.WriteLine("Shared realms visible: "+((System.Collections.IList)Program.Json.DeserializeObject(Encoding.UTF8.GetString(response.Body))).Count);
        }
        public void Dispose(){stopping=true;if(api!=null)api.Stop();if(game!=null)game.Stop();lock(gate){foreach(var item in active.ToArray())try{item.Dispose();}catch{}active.Clear();}if(apiThread!=null)apiThread.Join(1000);if(gameThread!=null)gameThread.Join(1000);}
    }
}
