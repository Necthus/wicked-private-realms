using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Wicked.Native {
    internal static class NativeTests {
        static int count;
        static void Assert(bool value,string name){if(!value)throw new Exception("Native self-test failed: "+name);count++;}
        static string ErrorMessage(Action operation){try{operation();}catch(Exception error){return error.Message;}throw new Exception("Native self-test expected an error");}
        internal static int Run(){
            Assert(Address.Parse("example.com").Port==8443,"default TLS port");
            Assert(Address.Parse("example.com:9443").Port==9443,"explicit port");
            Assert(Address.Parse("https://example.com").Port==443,"explicit HTTPS default");
            var ipv6=Address.Parse("[::1]:8443");Assert(ipv6.Host.Contains(":")&&ipv6.Port==8443&&ipv6.Authority.StartsWith("[")&&ipv6.Authority.EndsWith("]:8443"),"IPv6 authority "+ipv6.Authority);
            bool rejected=false;try{Address.Parse("http://example.com");}catch{rejected=true;}Assert(rejected,"reject plaintext");
            rejected=false;try{Steam.Validate("123");}catch{rejected=true;}Assert(rejected,"reject invalid Steam ID");
            Assert(Steam.Validate("76561197960278073")=="76561197960278073","valid Steam ID");
            Assert(Crypto.Sha(Encoding.UTF8.GetBytes("abc"))=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","SHA-256");
            string tmp=Path.Combine(Path.GetTempPath(),"wicked-native-test-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(tmp);
            try{
                string game=Path.Combine(tmp,"game"),client=Path.Combine(game,"wicked-client");
                string nested=Path.Combine(client,"extra extraction level");Directory.CreateDirectory(nested);
                File.WriteAllText(Path.Combine(game,"NoRestForTheWicked.exe"),"test fixture; never executed");
                Assert(GameInstallation.Resolve(game,null)==game,"client in game root");
                Assert(GameInstallation.Resolve(client,null)==game,"client in standard subdirectory");
                Assert(GameInstallation.Resolve(nested,null)==game,"client in nested extraction directory");
                string missing=Path.Combine(tmp,"missing game");
                Assert(GameInstallation.Resolve(client,missing)==missing,"explicit game directory is authoritative");
                string error=ErrorMessage(()=>GameProcess.ValidateBuild(missing));
                Assert(error.Contains("Game executable not found:")&&error.Contains(Path.Combine(missing,"NoRestForTheWicked.exe"))&&!error.Contains("Unsupported"),"missing EXE is a path error");
                error=ErrorMessage(()=>GameProcess.ValidateBuild(game));
                string assembly=Path.Combine(game,"GameAssembly.dll");
                Assert(error.Contains("GameAssembly.dll not found:")&&error.Contains(assembly),"missing DLL is an installation error");
                File.WriteAllText(assembly,"unsupported test DLL");
                error=ErrorMessage(()=>GameProcess.ValidateBuild(game));
                Assert(error.Contains("Unsupported GameAssembly.dll")&&error.Contains(assembly)&&
                    error.Contains("Expected SHA-256: 5b00ee90833b1be2ea73e01cb83e710e09e86199d12ac769be3c0e82add8b4bb")&&
                    error.Contains("Actual SHA-256: "+Crypto.ShaFile(assembly)),"unknown DLL rejected with diagnostic hashes");
                Assert(GameInstallation.Resolve(missing,null)==tmp,"network checks without game keep fallback directory");
                string file=Path.Combine(tmp,"configs.user.ini");string body="[user::general]\r\naccount_name=Player\r\naccount_steamid=76561197960278073\r\nlanguage=schinese\r\n[user::saves]\r\n# keep\r\n";
                byte[] encoded=new UTF8Encoding(true).GetPreamble();byte[] raw=Encoding.UTF8.GetBytes(body);byte[] original=new byte[encoded.Length+raw.Length];encoded.CopyTo(original,0);raw.CopyTo(original,encoded.Length);File.WriteAllBytes(file,original);
                Steam.BindIni(file,"76561197960278074",Path.Combine(tmp,"backup"));byte[] changed=File.ReadAllBytes(file);
                Assert(changed[0]==239&&changed[1]==187&&changed[2]==191,"INI BOM preserved");
                Assert(File.ReadAllText(file)==body.Replace("76561197960278073","76561197960278074"),"INI settings and CRLF preserved");
                Assert(Crypto.ShaFile(Directory.GetFiles(Path.Combine(tmp,"backup"))[0])==Crypto.Sha(original),"INI backup exact");
                Steam.BindIni(file,"76561197960278074",Path.Combine(tmp,"backup"));Assert(Directory.GetFiles(Path.Combine(tmp,"backup")).Length==1,"no redundant config writes");
                string dll=Path.Combine(Program.Root,"WickedAdapter.dll");if(File.Exists(dll))Assert(GameProcess.ExportRva(dll,"WickedAdapterInitialize")!=0,"adapter initialization export parsed");
                using(var stream=new MemoryStream(Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n"))){var message=HttpMessage.ReadHead(stream);message.ReadBody(stream,true);Assert(Encoding.ASCII.GetString(message.Body)=="abcde","HTTP chunked decoding");}
                using(var stream=new MemoryStream(Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 5\r\nCurrent-Epoch-Ms: 42\r\n\r\nhello"))){var message=HttpMessage.ReadHead(stream);message.ReadBody(stream,true);Assert(message.Status==200&&Encoding.ASCII.GetString(message.Body)=="hello"&&message.Headers["Current-Epoch-Ms"]=="42","HTTP metadata and body");}
            }finally{Directory.Delete(tmp,true);}
            Console.WriteLine("Native host self-tests passed: "+count);return 0;
        }
    }
}
