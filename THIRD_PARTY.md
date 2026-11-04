# Third-party components

The project source is MIT licensed. Separate components retain their own licenses.

* **GSE / Goldberg Steam emulator fork**: the unchanged approved DLL and all eight
  settings files are under `plugin/gse`. See its LGPLv3/GPLv3 texts and
  `UPSTREAM.md` for hash, build identifier and source provenance. Personal values
  in the settings template are sanitized.
* **Frida 17.24.3**: included only in the full Windows client Release. Its package
  metadata/license files are distributed with it. Upstream:
  <https://github.com/frida/frida>, <https://pypi.org/project/frida/17.24.3/>.
* **Python 3.13.2 embedded Windows runtime**: included only in the full client
  Release with `runtime/LICENSE.txt`. Upstream:
  <https://www.python.org/downloads/release/python-3132/>.
* **cryptography**: installed as a server dependency via pip, with its upstream
  licensing: <https://github.com/pyca/cryptography>.
* **MinHook 1.3.4**: statically linked into the native candidate adapter. The
  BSD-2-Clause license and source are under `native/vendor/minhook`.

No game executable, GameAssembly.dll, Unity assets, actual realm/character saves,
private SSH key, server signing secret or TLS private key is included.
