# Compiled native client

Current candidate: v0.2.2-native. See docs/NATIVE-MIGRATION.md for exact validation, remaining gameplay tests and rollback.

Sources: host/*.cs (.NET Framework 4.8 x64), adapter/adapter.c (MSVC x64 /MT), vendor/minhook (v1.3.4).

No Python, Frida, JavaScript or bundled runtime is shipped in the client. Python scripts here are development/build/test tools only. Integration state, real game logs, source database copies and trust pins must never be exported to GitHub or Release.

Earlier v0.2.1-native was withdrawn to draft after an audit found old logs had been mistaken for native-game proof. The current candidate has an explicit remote initialization handshake and dedicated game/adapter logs, and was retested against a local copy of the server database.
