"""Merge a stable upstream release locally; publication happens only after CI."""
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

from registry import resolve

ROOT=Path(__file__).resolve().parents[2]
BRANCH='io/nas-stable'
APPROVED='io/nas-approved'

def git(*args,check=True):
    p=subprocess.run(['git',*args],cwd=ROOT,capture_output=True,text=True)
    if check and p.returncode:raise RuntimeError('Git operation failed: '+args[0]+'; '+p.stderr[-1000:])
    return p

def main():
    if git('status','--porcelain').stdout.strip():raise RuntimeError('Source checkout has uncommitted changes')
    headers={'Accept':'application/vnd.github+json','User-Agent':'DeepTutor-private-release'}
    if os.environ.get('GH_TOKEN'):headers['Authorization']='Bearer '+os.environ['GH_TOKEN']
    request=urllib.request.Request('https://api.github.com/repos/HKUDS/DeepTutor/releases/latest',headers=headers)
    with urllib.request.urlopen(request,timeout=30) as r:release=json.load(r)
    tag=release['tag_name']
    if not re.fullmatch(r'v?\d+\.\d+\.\d+',tag) or release.get('draft') or release.get('prerelease'):
        raise RuntimeError('Upstream latest release is not a stable numeric tag')
    version=tag.lstrip('v')
    old=json.loads((ROOT/'deploy/nas/upstream-image.json').read_text())
    if tuple(map(int,version.split('.')))<tuple(map(int,old['version'].split('.'))):
        raise RuntimeError('Refusing an upstream downgrade')
    if git('remote','get-url','upstream',check=False).returncode:
        git('remote','add','upstream','https://github.com/HKUDS/DeepTutor.git')
    git('fetch','--no-tags','upstream','refs/tags/'+tag)
    upstream=git('rev-parse','FETCH_HEAD^{commit}').stdout.strip()
    git('config','user.name','github-actions[bot]')
    git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    git('merge','--no-edit','--no-ff',upstream)
    # The matching official frontend is reused. A local frontend/dependency
    # modification needs a full build recipe, so reject it instead of hiding it.
    git('diff','--exit-code',upstream,'--','web','deeptutor_cli','requirements','pyproject.toml','build_metadata.py')
    allowed=json.loads((ROOT/'deploy/nas/upstream-workflows.json').read_text())
    actual=sorted(p.name for p in (ROOT/'.github/workflows').glob('*.y*ml') if p.name!='nas-release.yml')
    if actual!=allowed:raise RuntimeError('Upstream workflow set changed; review before publishing')
    lock=resolve(version)
    if lock['source_revision']!=upstream:raise RuntimeError('Upstream image is not built from the selected release commit')
    path=ROOT/'deploy/nas/upstream-image.json'
    new=json.dumps(lock,indent=2)+'\n'
    if json.loads(path.read_text())!=lock:
        path.write_text(new,encoding='utf-8');git('add',str(path.relative_to(ROOT)))
        git('commit','-m','Pin official upstream image '+version)
    source=git('rev-parse','HEAD').stdout.strip()
    prior=None
    if not git('fetch','origin','refs/heads/'+APPROVED,check=False).returncode:
        found=git('show','FETCH_HEAD:deploy/nas/approved.json',check=False)
        if not found.returncode:prior=json.loads(found.stdout)
    changed=not prior or prior.get('source_commit')!=source
    outputs={'changed':str(changed).lower(),'source_commit':source,'version':version,
             'base_image':lock['source']+'@'+lock['manifest_digest']}
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as f:
            for k,v in outputs.items():f.write(k+'='+v+'\n')
    print(json.dumps(outputs))

if __name__=='__main__':main()
