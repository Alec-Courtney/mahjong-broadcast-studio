"""Inspect and deliberately edit portable fixtures without changing local JSON."""
import base64
import hashlib
import json
from pathlib import Path
import zipfile
from backup_package import read_package,json_bytes

def fixture_bytes(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')


def read_document(path):
    path=Path(path)
    if path.suffix!='.mjbackup':return json.loads(path.read_text(encoding='utf-8'))
    data,assets,_=read_package(path)
    data['assets']={key:dict(sha256=Path(key).stem,data=base64.b64encode(assets[key]).decode('ascii')) for key in assets}
    return data


def write_document(path,data):
    path=Path(path)
    if path.suffix!='.mjbackup':path.write_text(json.dumps(data),encoding='utf-8');return
    with zipfile.ZipFile(path) as z:members={name:z.read(name) for name in z.namelist()}
    manifest=json.loads(members.pop('manifest.json'));document=dict(data);assets=document.pop('assets',None)
    if manifest['kind']=='event':
        state=document.pop('state');broadcast=dict(state['broadcast']);matches=broadcast.pop('matches')
        members['live.json']=fixture_bytes(dict(state,broadcast=broadcast))
        for name,match in zip(manifest['matches'],matches):members[name]=fixture_bytes(match)
    members['event.json']=fixture_bytes(document)
    if assets is not None:
        for name,value in assets.items():members[name]=base64.b64decode(value['data'])
    for name,raw in members.items():
        # Preserve a deliberately corrupted asset digest supplied by a test.
        sha=assets[name]['sha256'] if assets is not None and name in assets else hashlib.sha256(raw).hexdigest()
        manifest['files'][name]=dict(sha256=sha,size=len(raw))
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,raw in members.items():z.writestr(name,raw)
        z.writestr('manifest.json',json_bytes(manifest))
