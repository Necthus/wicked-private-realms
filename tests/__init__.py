"""Test bootstrap for the maintained server and archived Python reference client."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / 'server', ROOT / 'archive/python-client'):
    sys.path.insert(0, str(directory))
