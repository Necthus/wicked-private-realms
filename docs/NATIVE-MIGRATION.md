# Native client v0.2.0 candidate

The native candidate replaces the Python/Frida runtime with three compiled pieces:

* `WickedLauncher.exe`: WinForms UI. It starts `WickedNativeHost.exe` when present.
* `WickedNativeHost.exe`: .NET Framework 4.8 host. It performs TLS login,
  certificate trust, loopback API/Photon bridges, Steam/GSE configuration,
  suspended game startup, and DLL injection.
* `WickedAdapter.dll`: native x64 C DLL using MinHook v1.3.4. It validates the
  v29466 PE layout, receives a short-lived config through a named mapping,
  installs build-specific IL2CPP/Photon hooks and repairs the private ping.

The native package has no Python, Frida, JavaScript engine or runtime directory.
The old Python package remains available as the v0.1.x rollback. Do not replace
the stable package for every player until the full game regression is complete.

## Current native validation

* MSVC x64 and Windows SDK build succeeds with `/MT`.
* `WickedAdapterSelfTest` returns `0x1F`: MinHook detour, SDK endpoint strings,
  private peer address filter, and token mapping checks pass.
* Native host direct TLS login and `--check` pass against AliyunServer.
* A native-host game launch was performed against v29466: GSE install/identity,
  online session start, player-add frame 120, confirmed input, and repeated
  save cycles completed. No original DLL was rewritten; GSE replacement was
  backed up by the installer.

## Still required before calling it stable

Manually test the native package with the full game UI: movement, combat,
chat, two machines in the same realm, map transition, return to menu, re-entry,
crash/reconnect, and long-running save cycles. Verify the native adapter log and
server transport log after every run. A native launch that reaches simulation and
saves is strong evidence, but it does not replace this full client regression.

## Build

From Windows with Visual Studio Build Tools and the Windows SDK:

```powershell
python native/build_native.py
python native/build_host.py
python native/build_selftest.py
python native/build_native_release.py
```

MinHook source and license are vendored under `native/vendor/minhook`. The native
adapter is not a general-purpose DLL injector: it only accepts the exact recorded
GameAssembly image size/timestamp and the client-created mapping, and it fails
closed on mismatches.
