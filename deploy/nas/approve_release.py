"""Publish source and approval metadata only after all required CI steps pass."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
def git(*args,cwd=ROOT,check=True):
    p=subprocess.run(['git',*args],cwd=cwd,capture_output=True,text=True)
    if check and p.returncode:raise RuntimeError('Git publication failed: '+args[0]+'; '+p.stderr[-1000:])
    return p

source=sys.argv[1]
if not re.fullmatch(r'[0-9a-f]{40}',source):raise ValueError('Invalid tested source commit')
if git('rev-parse','HEAD').stdout.strip()!=source:raise RuntimeError('Checkout differs from tested source')
lock=json.loads((ROOT/'deploy/nas/upstream-image.json').read_text())
git('push','origin',source+':refs/heads/io/nas-stable')
work=Path(tempfile.mkdtemp(prefix='nas-approved-'))/'worktree'
prior=git('fetch','origin','refs/heads/io/nas-approved',check=False)
base=git('rev-parse','FETCH_HEAD').stdout.strip() if not prior.returncode else source
git('worktree','add','--detach',str(work),base)
git('merge','--no-edit','--no-ff',source,cwd=work)
approval={'schema':1,'repository':'FSOTM/deeptutor-private','source_commit':source,
          'upstream_version':lock['version'],'upstream_manifest_digest':lock['manifest_digest'],
          'checks':{'policy_and_state':'passed','responses_optional_tools':'passed','frontend_edit_retry':'passed'},
          'model_window':1000000,'compaction_trigger':500000,'retained_history_budget':20000}
target=work/'deploy/nas/approved.json'
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(approval,indent=2)+'\n',encoding='utf-8')
git('add','deploy/nas/approved.json',cwd=work)
git('commit','-m','Approve tested NAS source '+source[:12],cwd=work)
git('push','origin','HEAD:refs/heads/io/nas-approved',cwd=work)
print(json.dumps({'approved_source':source,'version':lock['version']}))
