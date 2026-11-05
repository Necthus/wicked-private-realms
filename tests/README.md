# Tests

From the repository root:

```bash
python -m pip install -r tests/requirements.txt
python -m unittest discover -s tests -t . -p "test_*.py" -q
```

The bootstrap in `__init__.py` adds the maintained `server/` modules and archived Python reference client to the import path. Protocol vectors remain in `research/`; private captures are not distributed, and their optional tests are skipped.

Native integration checks: build the client, then run `python native/test_native_host.py`. Test servers use isolated temporary data, not production saves.
