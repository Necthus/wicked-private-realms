"""Upload prepared release assets, then publish; credentials never enter files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request


def credential():
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:return token
    process=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',text=True,
        capture_output=True,env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never'),timeout=20)
    values=dict(line.split('=',1) for line in process.stdout.splitlines() if '=' in line)
    if not values.get('password'):raise RuntimeError('GitHub authentication is required')
    return values['password']


def publish(repo,tag,commit,notes,assets,stable=False,title=None):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise ValueError('Invalid repository')
    token=credential()
    def call(method,path,data=None,content_type='application/json'):
        url=path if path.startswith('https://') else 'https://api.github.com'+path
        if urllib.parse.urlsplit(url).hostname not in ('api.github.com','uploads.github.com'):raise ValueError('Unexpected GitHub endpoint')
        if isinstance(data,dict):data=json.dumps(data).encode()
        request=urllib.request.Request(url,data=data,method=method,headers={
            'Authorization':'Bearer '+token,'User-Agent':'Wicked-Private-Realms-Release',
            'Accept':'application/vnd.github+json','Content-Type':content_type})
        with urllib.request.urlopen(request,timeout=180) as response:return json.load(response)
    path='/repos/'+repo+'/releases'
    try:release=call('GET',path+'/tags/'+urllib.parse.quote(tag,safe=''))
    except urllib.error.HTTPError as error:
        if error.code!=404:raise
        release=call('POST',path,{'tag_name':tag,'target_commitish':commit,'name':title or tag+' — Direct TLS server and GSE client',
            'body':Path(notes).read_text(encoding='utf-8'),'draft':True,'prerelease':not stable})
    if not release['draft']:raise RuntimeError('Release is already published; use a new version tag')
    existing={item['name']:item for item in release.get('assets',[])}
    upload=release['upload_url'].split('{',1)[0]
    for file in map(Path,assets):
        raw=file.read_bytes();digest='sha256:'+hashlib.sha256(raw).hexdigest()
        if file.name in existing:
            if existing[file.name].get('digest')==digest:continue
            raise RuntimeError('Existing draft asset differs: '+file.name)
        result=call('POST',upload+'?name='+urllib.parse.quote(file.name),raw,'application/octet-stream')
        if result['size']!=len(raw):raise RuntimeError('Uploaded size mismatch: '+file.name)
        if result.get('digest') and result['digest']!=digest:raise RuntimeError('Uploaded digest mismatch: '+file.name)
        print('Uploaded '+file.name+' ('+str(len(raw))+' bytes)',flush=True)
    result=call('PATCH',path+'/'+str(release['id']),{'draft':False,'prerelease':not stable,'make_latest':'true' if stable else 'false',
        'name':title or release['name'],
        'body':Path(notes).read_text(encoding='utf-8')})
    print(json.dumps({'release_url':result['html_url'],'draft':result['draft'],'tag':tag}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',required=True);p.add_argument('--tag',required=True)
    p.add_argument('--commit',required=True);p.add_argument('--notes',type=Path,required=True);p.add_argument('assets',nargs='+',type=Path)
    p.add_argument('--stable',action='store_true',help='Publish a non-prerelease and mark it Latest')
    p.add_argument('--title')
    args=p.parse_args();publish(args.repo,args.tag,args.commit,args.notes,args.assets,args.stable,args.title)
