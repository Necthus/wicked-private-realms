"""Shared server password and SteamID64 account mapping for private realms."""
import hashlib
import hmac
import secrets
import time
import uuid

from server import ApiError, digest


def steam_id(value):
    # IDs must be strings: JSON/JavaScript numbers cannot represent SteamID64.
    if not isinstance(value,str) or len(value)!=17 or not value.isascii() or not value.isdecimal():
        raise ApiError(400,'Steam ID must be a 17-digit SteamID64 string')
    number=int(value)
    if (number>>56)!=1 or ((number>>52)&15)!=1 or ((number>>32)&0xfffff)!=1 or not number&0xffffffff:
        raise ApiError(400,'Expected a public individual desktop SteamID64')
    return value


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200000)
    return 'pbkdf2_sha256$200000$' + salt + '$' + value.hex()


def password_matches(password, encoded):
    if not isinstance(password, str) or not 1 <= len(password) <= 256 or not encoded:return False
    try:
        scheme, rounds, salt, expected = encoded.split('$')
        if scheme != 'pbkdf2_sha256' or rounds != '200000':return False
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):return False


def initialize_steam_accounts(store):
    with store.transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS game_steam_accounts (
            steam_id TEXT PRIMARY KEY, account TEXT UNIQUE NOT NULL REFERENCES accounts(id))''')


def login_account(store, identifier):
    """Atomic SteamID lookup/create. Internal GUIDs remain stable for Quantum."""
    identifier=steam_id(identifier)
    with store.transaction() as db:
        row=db.execute('''SELECT a.id FROM accounts a JOIN game_steam_accounts s
            ON a.id=s.account WHERE s.steam_id=?''',(identifier,)).fetchone()
        created=row is None
        if created:
            account=str(uuid.uuid4())
            db.execute('INSERT INTO accounts VALUES(?,?,?,?)',
                       (account,identifier,digest(secrets.token_urlsafe(32)),time.time()))
            db.execute('INSERT INTO game_steam_accounts VALUES(?,?)',(identifier,account))
        else:account=row['id']
    return {'account_id':account,'name':identifier,'steam_id':identifier,'created':created}
