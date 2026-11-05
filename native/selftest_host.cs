using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

internal static class NativeSelfTest {
    [DllImport("WickedAdapter.dll", CallingConvention=CallingConvention.Winapi)]
    static extern uint WickedAdapterSelfTest(IntPtr unused);
    [DllImport("WickedAdapter.dll", CallingConvention=CallingConvention.Winapi)]
    static extern uint WickedFivePlayerSelfTest(IntPtr unused);
    static int Main(string[] args) {
        try {
            uint result=WickedAdapterSelfTest(IntPtr.Zero);
            Console.WriteLine("WickedAdapterSelfTest=0x{0:X8}",result);
            if(result!=0x7Fu)return 2;
            if(Array.IndexOf(args,"--five-player")>=0) {
                uint five=WickedFivePlayerSelfTest(IntPtr.Zero);Console.WriteLine("WickedFivePlayerSelfTest=0x{0:X8}",five);
                if(five!=0x1Fu)return 6;
            }
            return 0;
        } catch(DllNotFoundException ex) { Console.Error.WriteLine(ex.Message);return 3; }
          catch(BadImageFormatException ex) { Console.Error.WriteLine(ex.Message);return 4; }
          catch(EntryPointNotFoundException ex) { Console.Error.WriteLine(ex.Message);return 5; }
    }
}
