"""Fetch a pinned upstream client ZIP for its already-approved GSE DLL only."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

SOURCE_URL = ('https://github.com/Necthus/wicked-private-realms/releases/download/v0.2.5/'
              'Wicked-Private-Realms-Client-Native-v29466-v0.2.5.zip')
SOURCE_SHA256 = '85e50f0b494b638cf8a460ac0a14e0ae716c0e991d80db5ae18bc16fa72e4bba'
MAX_BYTES = 16 * 1024 * 1024


def fetch(output):
    request = urllib.request.Request(SOURCE_URL, headers={'User-Agent': 'Wicked-Private-Realms-CI'})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('Pinned upstream client ZIP checksum mismatch; refusing to use it')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    print('Verified upstream GSE source: ' + str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    fetch(parser.parse_args().output)
