# Native client migration (candidate)

Stable reference: v0.1.4; backup path is recorded in data/native-client-backup-path.txt.
Server-side backup is under /var/lib/wicked-multiplayer-test/before-native-client-*.

Target release: compiled WinForms launcher, compiled .NET Framework 4.8 host,
and a statically linked C adapter DLL using MinHook v1.3.4. No Python, Frida,
JavaScript engine, runtime directory or game DLL rewrite in the client package.
GSE remains an optional separate, pinned binary component.

The host owns certificate-pinned TLS, loopback HTTP/Photon bridges, GSE configuration,
Steam API identity helper, supported-build hash validation and process lifetime.
It creates the game suspended, loads WickedAdapter.dll using the Windows loader,
and supplies a short-lived session via an ACL-restricted memory mapping. The
adapter installs module-load and build-specific IL2CPP hooks before the primary
game thread is resumed. Session tokens are never written to an argument or file.

Preserve: original Steam ID save paths and graphics preferences; single-member
shared realms; private backend login; Photon TCP routing to loopback; no cloud
fallback; native TCP heartbeat timestamp repair; existing persistence protocol.

Development tests must cover hook helpers, native DLL loading in a disposable
process, TLS/API/byte-stream comparison against Python, and actual game startup.
Keep candidate output separate until those pass. Use a local isolated clone of
the backed-up server database for gameplay tests. Do not alter the live server.

The current native host has completed a local v29466 launch test: direct TLS
login, online session start, player-add frame 120, sustained confirmed input and
multiple save cycles. Manual movement, chat, two-client interaction, graceful
menu return and crash recovery remain required before replacing the stable
Python-backed client release.
