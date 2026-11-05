"""Build-29466 MoonBackend HTTP compatibility, backed by our private accounts.

Implements persistence, not the Photon/Quantum real-time server. API field names
were extracted from the running client's BackendJson tables, not guessed.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlsplit

from server import ApiError, Handler, MAX_SNAPSHOT
from launcher_auth import initialize_steam_accounts, login_account, password_matches


class GameBackend:
    def __init__(self, store, base_url, transport_experiment=False, join_password_hash=None):
        self.store, self.base = store, base_url.rstrip('/') + '/game'
        self.transport_experiment = transport_experiment
        self.join_password_hash = join_password_hash
        self.key = hmac.digest(store.code.encode(), b'wicked-private-game-session-v1', 'sha256')
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS game_entities (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    owner TEXT NOT NULL REFERENCES accounts(id),
                    metadata BLOB NOT NULL, contents BLOB NOT NULL,
                    revision INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS game_entity_versions (
                    id TEXT NOT NULL REFERENCES game_entities(id), revision INTEGER NOT NULL,
                    metadata BLOB NOT NULL, contents BLOB NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS game_exclusions (
                    realm TEXT NOT NULL REFERENCES game_entities(id),
                    account TEXT NOT NULL REFERENCES accounts(id), PRIMARY KEY(realm,account));
                CREATE TABLE IF NOT EXISTS game_transport_saves (
                    session TEXT NOT NULL, cycle INTEGER NOT NULL,
                    realm TEXT NOT NULL, account TEXT NOT NULL,
                    digest TEXT NOT NULL, ticks INTEGER NOT NULL,
                    PRIMARY KEY(session,cycle));
            ''')
        initialize_steam_accounts(store)

    def token(self, account, realm=None):
        def encode(obj):
            return base64.urlsafe_b64encode(json.dumps(obj,separators=(',',':')).encode()).rstrip(b'=')
        now = int(time.time())
        claims={
            'sub':account,'accountId':account,'iss':'wicked-private-realms',
            'aud':'wicked-private-game','iat':now,'exp':now+86400}
        if realm is not None:claims.update(realm=realm,purpose='private-transport-test')
        msg = encode({'alg':'HS256','typ':'JWT'}) + b'.' + encode(claims)
        return (msg+b'.'+base64.urlsafe_b64encode(hmac.digest(self.key,msg,'sha256')).rstrip(b'=')).decode()

    def verify(self, header):
        return self.verify_claims(header)['sub']

    def verify_claims(self, header):
        token = header[7:] if header.startswith('Bearer ') else header
        try:
            head, body, sig = token.split('.')
            signature=base64.urlsafe_b64decode(sig+'='*(-len(sig)%4))
            if not hmac.compare_digest(signature,hmac.digest(self.key,(head+'.'+body).encode(),'sha256')):
                raise ValueError('signature')
            claims=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
            if claims['exp'] <= time.time() or claims['iss'] != 'wicked-private-realms' or claims['aud'] != 'wicked-private-game':
                raise ValueError('claims')
            account=claims['sub']
            with self.store.transaction() as db:
                if not db.execute('SELECT 1 FROM accounts WHERE id=?',(account,)).fetchone():
                    raise ValueError('account')
            return claims
        except (ValueError,KeyError,TypeError):
            raise ApiError(401,'Invalid private game session')

    @staticmethod
    def binary(value):
        if not isinstance(value,str): raise ApiError(400,'Expected base64 save data')
        try: data=base64.b64decode(value,validate=True)
        except ValueError: raise ApiError(400,'Invalid base64 save data')
        if len(data)>MAX_SNAPSHOT: raise ApiError(413,'Save data too large')
        return data

    @staticmethod
    def encoded(data):
        return base64.b64encode(data).decode()

    def entity(self,db,identifier,kind,account,write=False):
        row=db.execute('SELECT * FROM game_entities WHERE id=? AND kind=?',(identifier,kind)).fetchone()
        if row is None: raise ApiError(404,'Game save not found')
        if (kind=='character' or write) and row['owner']!=account:
            raise ApiError(403,'This account does not own the save')
        if kind=='realm' and db.execute('SELECT 1 FROM game_exclusions WHERE realm=? AND account=?',(identifier,account)).fetchone():
            raise ApiError(403,'Account has left or was excluded from this realm')
        return row

    def participation(self,db,realm,account):
        others=[]
        for row in db.execute('''SELECT id,name FROM accounts WHERE id!=? AND id NOT IN
            (SELECT account FROM game_exclusions WHERE realm=?)''',(account,realm['id'])):
            others.append({'Kind':0 if row['id']==realm['owner'] else 1,
                           'AccountInfo':{'AccountId':row['id'],
                                          'Development':{'UserId':row['id'],'UserName':row['name']},
                                          'Steam':None},'IsPlaying':False})
        return {'OwnKind':0 if realm['owner']==account else 1,'Others':others}

    def commit_transport_save(self,account,realm_id,session,cycle,raw,save,authorized_accounts=None):
        """Commit validated realm and owned characters atomically, once per cycle."""
        checksum=hashlib.sha256(raw).hexdigest()
        allowed={account} if authorized_accounts is None else set(authorized_accounts)
        if account not in allowed:raise ApiError(403,'Save provider is not a participant')
        with self.store.transaction() as db:
            realm=self.entity(db,realm_id,'realm',account)
            prior=db.execute('SELECT digest,ticks FROM game_transport_saves WHERE session=? AND cycle=?',
                             (session,cycle)).fetchone()
            if prior is not None:
                if prior['digest']!=checksum:raise ApiError(409,'Save cycle was replayed with different data')
                return prior['ticks']
            entries=[(realm,save['metadata'],save['contents'])]
            for character in save['characters']:
                owner=db.execute("SELECT owner FROM game_entities WHERE id=? AND kind='character'",(character['id'],)).fetchone()
                if owner is None or owner['owner'] not in allowed:raise ApiError(403,'Save character is not owned by a validated participant')
                row=self.entity(db,character['id'],'character',owner['owner'],write=True)
                entries.append((row,character['metadata'],character['contents']))
            now=time.time();ticks=time.time_ns()//100+621355968000000000
            for row,metadata,contents in entries:
                rev=row['revision']+1
                db.execute('INSERT INTO game_entity_versions VALUES(?,?,?,?,?)',
                           (row['id'],rev,metadata,contents,now))
                db.execute('UPDATE game_entities SET metadata=?,contents=?,revision=? WHERE id=?',
                           (metadata,contents,rev,row['id']))
            db.execute('INSERT INTO game_transport_saves VALUES(?,?,?,?,?,?)',
                       (session,cycle,realm_id,account,checksum,ticks))
        return ticks

    def save_api(self,account,action,payload):
        if not isinstance(payload,dict): raise ApiError(400,'Expected object')
        with self.store.transaction() as db:
            if action=='ListRealms':
                rows=db.execute('''SELECT * FROM game_entities WHERE kind='realm' AND id NOT IN
                    (SELECT realm FROM game_exclusions WHERE account=?) ORDER BY created''',(account,)).fetchall()
                return [{'RealmId':r['id'],'Participation':self.participation(db,r,account),
                         'Metadata':self.encoded(r['metadata']),'IsEphemeral':False} for r in rows]
            if action=='ListCharacters':
                return [{'CharacterId':r['id'],'Metadata':self.encoded(r['metadata'])} for r in
                        db.execute("SELECT * FROM game_entities WHERE kind='character' AND owner=? ORDER BY created",(account,))]
            if action in ('CreateRealm','CreateCharacter'):
                metadata,contents=self.binary(payload.get('Metadata')),self.binary(payload.get('Contents'))
                identifier=str(uuid.uuid4());kind='realm' if action=='CreateRealm' else 'character';now=time.time()
                db.execute('INSERT INTO game_entities VALUES(?,?,?,?,?,?,?)',(identifier,kind,account,metadata,contents,1,now))
                db.execute('INSERT INTO game_entity_versions VALUES(?,?,?,?,?)',(identifier,1,metadata,contents,now))
                return {'Status':0,'Id':identifier}
            if action in ('LoadRealm','LoadCharacter'):
                kind='realm' if action=='LoadRealm' else 'character'
                r=self.entity(db,payload.get('RealmId' if kind=='realm' else 'CharacterId'),kind,account)
                return {'Id':r['id'],'Metadata':self.encoded(r['metadata']),'Contents':self.encoded(r['contents'])}
            if action=='QueryRealmParticipation':
                return self.participation(db,self.entity(db,payload.get('RealmId'),'realm',account),account)
            if action=='SaveData':
                if self.transport_experiment and payload.get('Realm') is not None:
                    # This service exposes persistent shared realms. A legacy
                    # solo client must not replace one with its offline branch.
                    raise ApiError(409,'Shared realm requires multiplayer save; update the private client')
                entries=[]
                if payload.get('Realm') is not None: entries.append(('realm',payload['Realm']))
                characters=payload.get('Characters') or []
                if not isinstance(characters,list): raise ApiError(400,'Characters must be an array')
                entries.extend(('character',entry) for entry in characters)
                # Validation and all entity updates share one transaction.
                for kind,entry in entries:
                    if not isinstance(entry,dict): raise ApiError(400,'Invalid save entry')
                    row=self.entity(db,entry.get('Id'),kind,account,write=True)
                    meta,content=self.binary(entry.get('Metadata')),self.binary(entry.get('Contents'))
                    revision=row['revision']+1;now=time.time()
                    db.execute('INSERT INTO game_entity_versions VALUES(?,?,?,?,?)',(row['id'],revision,meta,content,now))
                    db.execute('UPDATE game_entities SET metadata=?,contents=?,revision=? WHERE id=?',(meta,content,revision,row['id']))
                return 0
            if action=='GetAccountIdsFromSteamIds': return []
            if action=='PlayRealm':
                self.entity(db,payload.get('RealmId'),'realm',account)
                if self.transport_experiment:
                    # Explicit isolated protocol probe; not advertised as playable.
                    return {'Status':0,'Region':0,'PlayJwt':self.token(account,payload.get('RealmId'))}
                # The original client turns non-2xx responses into a null string
                # and repeatedly dereferences it. Our adapter explicitly handles
                # this private status; it is never a successful session grant.
                return {'Status':1001,'Region':None,'PlayJwt':None}
            raise ApiError(501,'Game operation not implemented: '+action)

    def request(self,path,method,payload,authorization):
        if path=='/game/launcher/login' and method=='POST':
            if not isinstance(payload,dict):raise ApiError(400,'Expected login object')
            if not self.join_password_hash:raise ApiError(503,'Launcher login is not configured')
            if not password_matches(payload.get('password'),self.join_password_hash):
                raise ApiError(403,'Incorrect server password')
            result=login_account(self.store,payload.get('steam_id'))
            result.update(token=self.token(result['account_id']),endpoint=self.base,
                          server_id=hashlib.sha256(self.key+b'client-save-cache-namespace-v1').hexdigest()[:32])
            return result
        if path.startswith('/game/gatekeeper/') and method=='GET':
            return {'data':[{'endpoints':{'auth':self.base+'/auth','queue':self.base+'/queue',
                                          'rest':self.base+'/rest','wss':self.base.replace('http','ws',1)},
                             'authApiKey':'private-realms'}]}
        if path in ('/game/auth/v3/token','/game/auth/v3/signup') and method=='POST':
            if not isinstance(payload,dict): raise ApiError(400,'Expected login object')
            # Adapter supplies a private account token, never an actual Steam ticket.
            ticket=payload.get('entitlementTicket','')
            if not isinstance(ticket,str):raise ApiError(400,'Invalid login ticket')
            try:account=self.store.authenticate(ticket)
            except ApiError:
                claims=self.verify_claims(ticket)
                if 'realm' in claims or 'purpose' in claims:raise ApiError(401,'Not a launcher session')
                account=claims['sub']
            return {'data':[{'accountId':account,'token':self.token(account),'queueApiKey':'private-realms'}]}
        account=self.verify(authorization)
        if path=='/game/queue/join' and method=='POST':
            return {'data':[{'mainApiKey':'private-realms','accessToken':self.token(account)}]}
        parts=path.split('/')
        if len(parts)==6 and parts[1:4]==['game','rest','save'] and method=='POST':
            if parts[4]!=account: raise ApiError(403,'Account path does not match session')
            return self.save_api(account,parts[5],payload)
        raise ApiError(501,'Unsupported game endpoint: '+path)


class GameHandler(Handler):
    def dispatch(self):
        path=urlsplit(self.path).path
        if not path.startswith('/game/'):
            return super().dispatch()
        path=path.replace('//','/')
        raw=self.read_body(MAX_SNAPSHOT*3) if self.command=='POST' else b''
        try: payload=json.loads(raw) if raw else {}
        except (ValueError,UnicodeError): raise ApiError(400,'Invalid JSON')
        # Only field names and sizes, never authorization or binary data.
        print(json.dumps({'game_api':path,'bytes':len(raw),'fields':list(payload) if isinstance(payload,dict) else 'non-object'}),flush=True)
        result=self.server.game.request(path,self.command,payload,self.headers.get('Authorization',''))
        return self.respond(200,result,{'Current-Epoch-Ms':int(time.time()*1000)})
