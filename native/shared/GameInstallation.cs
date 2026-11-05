using System.IO;

namespace Wicked.Native {
    internal static class GameInstallation {
        internal static string Resolve(string clientDirectory,string explicitDirectory) {
            if(!string.IsNullOrEmpty(explicitDirectory))return Path.GetFullPath(explicitDirectory);
            var client=new DirectoryInfo(Path.GetFullPath(clientDirectory));
            // Accept a client beside the game EXE or inside an extracted subdirectory.
            for(var directory=client;directory!=null;directory=directory.Parent) {
                if(File.Exists(Path.Combine(directory.FullName,"NoRestForTheWicked.exe")))return directory.FullName;
            }
            // Keep network-only checks usable without a game installation. Validation
            // reports the missing EXE when a game launch is actually requested.
            return (client.Parent??client).FullName;
        }
    }
}
