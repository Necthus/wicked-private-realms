# Compiled native client

Current release: v0.2.3. The client has fully migrated to compiled EXE/DLL delivery. See docs/NATIVE-MIGRATION.md for installation, validation and rollback.

Sources: launcher/launcher.cs (WinForms), host/*.cs (.NET Framework 4.8 x64), adapter/adapter.c (MSVC x64 /MT), vendor/minhook (v1.3.4).

The original Python/Frida implementation remains in archive/python-client/ as historical source and a protocol reference. The current client does not ship or load Python, Frida, JavaScript or a bundled runtime. The server remains Python; build/test scripts also use development-machine Python.

Keep the source checkout separate from the game installation. Build with build_native.py, build_host.py and build_launcher.py, package with build_native_release.py --gse-from <native Release ZIP>, then deploy the ZIP with ../tools/deploy_native_client.py.

Integration state, real game logs, source database copies and trust pins must never be exported to GitHub or Release. Earlier v0.2.1-native was withdrawn; use the current release.

All compiled output goes to native/dist/ and is ignored by Git. EXE/DLL files are distributed only as Release assets.
