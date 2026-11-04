"""Private save cache namespace; Unity graphics/preferences keep their usual paths."""
import hashlib
import uuid
from pathlib import Path


def cache_directory(root, profile):
    if profile.get('steam_id'):
        identifier=profile['steam_id']
        if not isinstance(identifier,str) or len(identifier)!=17 or not identifier.isascii() or not identifier.isdecimal():
            raise ValueError('Invalid SteamID64')
        return None
    account=str(uuid.UUID(profile['account_id']))
    server=profile.get('server_id') or profile['endpoint']
    namespace=hashlib.sha256(server.encode('utf-8')).hexdigest()[:24]
    return Path(root)/'data/account-cache-v2'/namespace/account/'DataStore'


def game_arguments(executable, logpath):
    # Graphics settings belong to the game. Avoid overriding saved fullscreen,
    # resolution or quality settings with diagnostic launch flags.
    return [str(executable), '-logFile', str(logpath)]
