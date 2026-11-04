# Bundled GSE build

This is the existing GSE DLL from the user's game installation, not the separately
submitted WUS/RUNE-style attachment. It is redistributed byte-for-byte unchanged.

* SHA-256: `723e13f5a2e1ec799f342612a3294229ca832d143fdea85c00025216b6696e88`
* File description: GSE Client API (win64)
* File version: 08.33.09.23
* Embedded Source Control ID: 8330923
* Upstream project: <https://github.com/Detanup01/gbe_fork>
* Upstream license: GNU LGPL version 3; the LGPL and referenced GPL texts are included.

The embedded source-control identifier could not be resolved in the current
upstream GitHub history. It is recorded as provenance, not as a verified link to
a matching source revision. Upstream source and build instructions are available
at the project URL above. The DLL remains a replaceable, separate component;
this repository's MIT license does not relicense GSE or its dependencies.

All eight original steam_settings files are included. The user configuration is
a distribution template: personal nickname, Steam ID and absolute Windows save
path have been removed. The launcher writes each user's supplied SteamID64
before loading GSE. Existing installations of this exact GSE build keep their
settings; replacing another API creates a backup first.
