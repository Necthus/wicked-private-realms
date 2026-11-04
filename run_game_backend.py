"""Run control API plus verified portions of the game's HTTP protocol."""
import argparse
import os
from http.server import ThreadingHTTPServer
from server import Store
from game_backend import GameBackend, GameHandler

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=18768)
    p.add_argument('--db',default='data/game-test.sqlite3')
    p.add_argument('--transport-experiment', action='store_true')
    args=p.parse_args()
    store=Store(args.db,os.environ['REALM_ENROLLMENT_CODE'])
    service=ThreadingHTTPServer(('127.0.0.1',args.port),GameHandler)
    service.store=store
    service.game=GameBackend(store,'http://127.0.0.1:'+str(args.port),args.transport_experiment,
                             join_password_hash=os.environ.get('REALM_JOIN_PASSWORD_HASH'))
    print('Private game HTTP backend listening on '+service.game.base,flush=True)
    service.serve_forever()
