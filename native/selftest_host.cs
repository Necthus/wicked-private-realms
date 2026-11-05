using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

internal static class NativeSelfTest {
    [DllImport("WickedAdapter.dll", CallingConvention=CallingConvention.Winapi)]
    static extern uint WickedAdapterSelfTest(IntPtr unused);
    static int Main() {
        try {
            uint result=WickedAdapterSelfTest(IntPtr.Zero);
            Console.WriteLine("WickedAdapterSelfTest=0x{0:X8}",result);
            return result==0x7Fu ? 0 : 2;
        } catch(DllNotFoundException ex) { Console.Error.WriteLine(ex.Message);return 3; }
          catch(BadImageFormatException ex) { Console.Error.WriteLine(ex.Message);return 4; }
          catch(EntryPointNotFoundException ex) { Console.Error.WriteLine(ex.Message);return 5; }
    }
}
