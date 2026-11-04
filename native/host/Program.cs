using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace Wicked.Native {
    internal static class Program {
        internal static readonly JavaScriptSerializer Json=new JavaScriptSerializer {MaxJsonLength=4*1024*1024};
        internal static string Root=AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\','/');
        static int Main(string[] args) {
            Console.OutputEncoding=new UTF8Encoding(false); Console.InputEncoding=new UTF8Encoding(false);
            try {
                if(args.Length>0 && args[0]=="--steam-identity") {
                    Console.WriteLine("WICKED_STEAM_IDENTITY="+Json.Serialize(Steam.ReadNative(args[1])));return 0;
                }
                if(args.Contains("--self-test"))return NativeTests.Run();
                string input=Console.ReadLine();
                if(string.IsNullOrEmpty(input))throw new Exception("Launch this program through WickedLauncher.exe");
                var config=Json.Deserialize<Dictionary<string,object>>(input);
                string server=Value(config,"server"),password=Value(config,"password"),id=Steam.Validate(Value(config,"steam_id"));
                string cancel=Argument(args,"--cancel-file"),secondsText=Argument(args,"--seconds");
                int seconds=string.IsNullOrEmpty(secondsText)?0:int.Parse(secondsText);
                string game=Argument(args,"--game-directory");
                if(string.IsNullOrEmpty(game))game=Directory.GetParent(Root).FullName;
                game=Path.GetFullPath(game);
                Func<bool> cancelled=()=>!string.IsNullOrEmpty(cancel)&&File.Exists(cancel);
                bool bridgeOnly=args.Contains("--bridge-only"),networkOnly=args.Contains("--network-check");
                var address=Address.Parse(server);
                if(string.IsNullOrEmpty(password))throw new Exception("Enter the server join password");
                if(!bridgeOnly && !networkOnly) {
                    GameProcess.ValidateBuild(game);
                    if(Process.GetProcessesByName("NoRestForTheWicked").Length!=0)throw new Exception("Close the running game first");
                    string mode=Value(config,"steam_mode");
                    if(string.IsNullOrEmpty(mode))mode="gse";
                    if(mode!="gse" && mode!="native")throw new Exception("Unknown Steam compatibility mode");
                    if(mode=="gse")Steam.Install(game,Root,id);
                    var identity=Steam.ReadHelper(game);
                    if(Value(identity,"steam_id")!=id)throw new Exception("The game's Steam identity differs from the supplied ID. For genuine Steam use the signed-in account ID.");
                    Console.WriteLine("Steam identity: "+id);
                }
                if(cancelled())return 0;
                var tls=new PrivateTls(address,Path.Combine(Root,"data","server-trust"));
                Console.WriteLine("Connecting directly to the private TLS server...");
                var body=Encoding.UTF8.GetBytes(Json.Serialize(new {steam_id=id,password=password}));
                var reply=tls.Request("POST","/game/launcher/login",body,new Dictionary<string,string>{{"Content-Type","application/json"}});
                var profile=Json.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(reply.Body));
                if(reply.Status!=200)throw new Exception(Value(profile,"error"));
                string token=Value(profile,"token");
                if(token.Length<20 || token.Length>=4096)throw new Exception("Invalid private session token");
                Console.WriteLine((Convert.ToBoolean(profile["created"])?"Created account: ":"Signed in: ")+id);
                password=null;config.Clear();input=null;body=null;
                if(networkOnly){Console.WriteLine("Direct TLS network login verified.");return 0;}
                using(var bridge=new Bridge(tls,token)) {
                    bridge.Start();
                    if(args.Contains("--check")) {
                        bridge.CheckApi();Console.WriteLine("Direct TLS login and game API verified; no game was launched.");return 0;
                    }
                    if(bridgeOnly) {
                        Console.WriteLine("Native loopback bridge ready.");
                        DateTime until=seconds>0?DateTime.UtcNow.AddSeconds(seconds):DateTime.MaxValue;
                        while(!cancelled()&&DateTime.UtcNow<until)Thread.Sleep(100);
                        return 0;
                    }
                    Directory.CreateDirectory(Path.Combine(Root,"research"));
                    using(var process=new GameProcess(game,Root,id,token))process.Run(cancelled,seconds);
                }
                return 0;
            } catch(Exception error) {Console.Error.WriteLine("ERROR: "+error.Message);return 1;}
        }
        internal static string Value(Dictionary<string,object> source,string key){object value;return source.TryGetValue(key,out value)&&value!=null?Convert.ToString(value):"";}
        static string Argument(string[] args,string key){int index=Array.IndexOf(args,key);return index>=0&&index+1<args.Length?args[index+1]:null;}
    }
}
