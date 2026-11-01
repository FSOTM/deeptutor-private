"""NAS updater for CI-approved private Git commits, with cold rollback copies.

Only the DeepTutor application and its dedicated TLS proxy are controlled.
GitHub access uses a repository-scoped read-only SSH deployment key.
"""
import argparse
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.request

ROOT=Path('/volume1/docker/deeptutor')
REPOSITORY='FSOTM/deeptutor-private'
APPROVED='io/nas-approved'
DOCKER='/usr/local/bin/docker'
COMPOSE='/usr/local/bin/docker-compose'
MODULES={
 'agents/loop/agent_loop.py','agents/loop/pipeline.py',
 'capabilities/mastery/history.py','capabilities/mastery/memory.py',
 'capabilities/mastery/pipeline.py','capabilities/mastery/state_updates.py',
 'services/session/model_history.py','services/session/context_builder.py',
 'services/session/turns/executor.py','services/session/total_compaction.py',
 'services/llm/provider_core/openai_responses/converters.py'}


class Deferred(RuntimeError):pass


def run(args,**kwargs):
    print('RUN',args[0],args[1] if len(args)>1 else '',flush=True)
    return subprocess.run([str(a) for a in args],check=True,**kwargs)


def output(args,**kwargs):
    return subprocess.check_output([str(a) for a in args],**kwargs).decode().strip()


def api(port,path):
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+path,timeout=15) as r:return json.load(r)


def inspect(name):
    return json.loads(output([DOCKER,'inspect',name]))[0]


def validate_approval(value):
    if value.get('schema')!=1 or value.get('repository')!=REPOSITORY:raise ValueError('Unknown approval format or repository')
    if not re.fullmatch(r'[0-9a-f]{40}',value.get('source_commit','')):raise ValueError('Invalid approved commit')
    if not re.fullmatch(r'\d+\.\d+\.\d+',value.get('upstream_version','')):raise ValueError('Invalid upstream version')
    if value.get('checks')!={'policy_and_state':'passed','responses_optional_tools':'passed','frontend_edit_retry':'passed'}:
        raise ValueError('Required CI checks have not passed')
    if (value.get('model_window'),value.get('compaction_trigger'),value.get('retained_history_budget'))!=(1000000,500000,20000):
        raise ValueError('Approved policy differs from the configured learning policy')


def rewrite_compose(old,image_id,manifest_digest,local_only=False):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image_id):raise ValueError('Invalid verified image ID')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',manifest_digest):raise ValueError('Invalid upstream digest')
    if old.count('  deeptutor:')!=1 or 'container_name: deeptutor\n' not in old:raise ValueError('Unexpected compose service layout')
    lines=[];images=0;ports=0
    for line in old.splitlines():
        if '/app/deeptutor/' in line and line.lstrip().startswith('- '):
            match=re.search(r':/app/deeptutor/(.+):ro\s*$',line)
            if not match or match.group(1) not in MODULES:raise ValueError('Unknown code mount requires review')
            continue
        if line.startswith('    image:'):
            line='    image: '+image_id;images+=1
        if '"0.0.0.0:3782:3782"' in line or '"127.0.0.1:3782:3782"' in line:
            line='      - "'+('127.0.0.1' if local_only else '0.0.0.0')+':3782:3782"';ports+=1
        if line.lstrip().startswith('deployment.upstream.digest:'):
            line='      deployment.upstream.digest: '+manifest_digest
        if line.lstrip().startswith('org.opencontainers.image.source:'):
            line='      org.opencontainers.image.source: https://github.com/'+REPOSITORY
        if 'Official 1.7.0 image' in line:line='    # CI-approved private source on the digest-pinned matching upstream image.'
        lines.append(line)
    new='\n'.join(lines)+'\n'
    if images!=1 or ports!=1 or './data:/app/data' not in new or 'DEEPTUTOR_AUTO_COMPACT_TOKENS: "500000"' not in new:
        raise ValueError('Compose data, port or policy invariant failed')
    return new


def extract_source(raw,destination):
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            rel=Path(member.name)
            if rel.is_absolute() or '..' in rel.parts or member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise ValueError('Unsafe source archive member')
            if not (member.name.startswith('deeptutor/') or member.name.startswith('deploy/nas/') or member.name in ['deeptutor','deploy','deploy/nas']):
                raise ValueError('Unexpected source archive path')
        archive.extractall(destination)


def copy_data(source,target):
    if source.is_symlink() or not source.is_dir() or target.exists():raise ValueError('Unsafe data copy target')
    run(['/usr/bin/cp','-a','--reflink=auto',source,target])


def container_inventory():
    ids=output([DOCKER,'ps','-aq']).split()
    if not ids:return {}
    rows=json.loads(output([DOCKER,'inspect',*ids]))
    return {d['Name']:(d['Id'],d['State']['Status'],d['State']['StartedAt']) for d in rows}


class Updater:
    def __init__(self,root=ROOT):
        self.root=Path(root)
        if self.root.is_symlink() or self.root.name!='deeptutor' or not (self.root/'compose.yaml').is_file():
            raise ValueError('Unexpected deployment root')
        self.state=self.root/'.updates';self.state.mkdir(mode=0o700,exist_ok=True)
        self.state.chmod(0o700)
        key=self.state/'keys/git-readonly';known=self.state/'keys/known_hosts'
        self.env=dict(os.environ)
        self.env['GIT_SSH_COMMAND']='/usr/bin/ssh -i '+shlex.quote(str(key))+' -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile='+shlex.quote(str(known))+' -p 443'
        self.git_dir=self.state/'repository.git'

    def git(self,*args):
        return output(['/usr/bin/git','--git-dir='+str(self.git_dir),*args],env=self.env)

    def approved(self):
        if not self.git_dir.exists():
            run(['/usr/bin/git','init','--bare',self.git_dir])
            self.git('remote','add','origin','ssh://git@ssh.github.com:443/'+REPOSITORY+'.git')
        self.git('fetch','--depth=4','origin','refs/heads/'+APPROVED)
        value=json.loads(self.git('show','FETCH_HEAD:deploy/nas/approved.json'))
        validate_approval(value)
        source=value['source_commit']
        self.git('fetch','--depth=1','origin',source)
        return value

    def idle(self):
        status=api(3782,'/api/system/runtime')
        if status.get('worker_count')!=1 or status.get('owner_turn_count') or status.get('recovery_backlog'):
            raise Deferred('Learning or recovery work is active; keep the running version')

    def wait_ready(self,port):
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            try:
                if api(port,'/api/auth/status').get('authenticated') is True:return
            except Exception:pass
            time.sleep(2)
        raise RuntimeError('Application readiness timeout')

    def stage(self,approval):
        source=approval['source_commit']
        folder=self.state/'stages'/(source[:12]+'-'+str(time.time_ns()))
        folder.mkdir(parents=True,mode=0o700)
        raw=subprocess.check_output(['/usr/bin/git','--git-dir='+str(self.git_dir),'archive',source,'deeptutor','deploy/nas'],env=self.env)
        extract_source(raw,folder/'source')
        if approval.get('frontend_bundle'):
            approval_path=folder/'frontend-approval.json'
            approval_path.write_text(json.dumps(approval))
            run(['/usr/bin/python3',folder/'source/deploy/nas/frontend_bundle.py','download',
                 '--approval',approval_path,'--destination',folder/'source/web-runtime'])
        lock=json.loads((folder/'source/deploy/nas/upstream-image.json').read_text())
        if lock['source']!='ghcr.io/hkuds/deeptutor' or lock['manifest_digest']!=approval['upstream_manifest_digest'] or lock['version']!=approval['upstream_version']:
            raise RuntimeError('Approved source/image identity mismatch')
        base='local/deeptutor-upstream:'+lock['version']+'-'+lock['manifest_digest'][7:19]
        present=subprocess.run([DOCKER,'image','inspect',lock['config_digest']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
        if not present:
            cache=self.state/'images'/lock['version'];cache.mkdir(parents=True,exist_ok=True)
            archive=cache/'image.tar'
            run(['/usr/bin/python3',folder/'source/deploy/nas/image_archive.py','--lock',folder/'source/deploy/nas/upstream-image.json','--work',cache/'blobs','--archive',archive])
            run([DOCKER,'load','-i',archive])
        run([DOCKER,'tag',lock['config_digest'],base])
        image='local/deeptutor-private:'+source[:12]
        run([DOCKER,'build','--build-arg','BASE_IMAGE='+base,'--build-arg','SOURCE_COMMIT='+source,'-f','deploy/nas/Dockerfile','-t',image,'.'],cwd=folder/'source')
        built=json.loads(output([DOCKER,'image','inspect',image]))[0]
        if built['Config']['Labels'].get('org.opencontainers.image.revision')!=source:raise RuntimeError('Candidate image is not the approved commit')
        (folder/'approval.json').write_text(json.dumps(approval))
        return folder,built['Id']

    def gate(self,enabled):
        run([DOCKER,'exec','deeptutor-tls',*(['touch','/tmp/deeptutor-maintenance'] if enabled else ['rm','-f','/tmp/deeptutor-maintenance'])])

    def reload_tls(self):
        run([DOCKER,'exec','deeptutor-tls','nginx','-t','-c','/etc/nginx/deeptutor.conf'])
        run([DOCKER,'exec','deeptutor-tls','nginx','-s','reload','-c','/etc/nginx/deeptutor.conf'])

    def recreate(self):
        run([COMPOSE,'-f','compose.yaml','up','-d','--no-deps','--no-build','deeptutor'],cwd=self.root)
        self.wait_ready(3782)

    def candidate(self,folder,image,baseline):
        data=folder/'candidate-data'
        copy_data(self.root/'data',data)
        # Capture using the previously running image and the copied data. The
        # candidate only ever sees its own bind mount and loopback web port.
        old=inspect('deeptutor')['Image']
        verifier=folder/'source/deploy/nas/verify_runtime.py'
        run([DOCKER,'run','--rm','--network','none','--entrypoint','python','-w','/app','-e','PYTHONPATH=/app','-v',str(data)+':/app/data','-v',str(verifier)+':/verify.py:ro','-v',str(folder)+':/verification',old,'/verify.py','--capture','/verification/baseline.json'])
        name='deeptutor-upgrade-candidate'
        if subprocess.run([DOCKER,'container','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
            raise RuntimeError('A staging container already exists; inspect it before proceeding')
        run([DOCKER,'run','-d','--name',name,'--label','deployment.role=deeptutor-upgrade-candidate',
             '--memory','2g','--memory-swap','3g','--cpuset-cpus','0,1','--cpu-shares','128',
             '--tmpfs','/app/web/.next/cache:rw,uid=1026,gid=100,mode=0700,size=64m',
             '-e','PUID=1026','-e','PGID=100','-e','TZ=Asia/Shanghai','-e','NODE_OPTIONS=--max-old-space-size=512',
             '-e','DEEPTUTOR_AUTO_COMPACT_TOKENS=500000','-e','TIKTOKEN_CACHE_DIR=/app/data/cache/tiktoken',
             '-p','127.0.0.1:13782:3782','-v',str(data)+':/app/data','-v',str(folder/'baseline.json')+':/verification/baseline.json:ro',image])
        try:
            self.wait_ready(13782)
            run([DOCKER,'exec','-u','1026:100','-w','/app','-e','PYTHONPATH=/app',name,'python','/opt/deeptutor-nas/verify_runtime.py','--check','/verification/baseline.json'])
            shutil.copyfile(folder/'baseline.json',baseline)
        finally:
            info=inspect(name)
            if info['Config'].get('Labels',{}).get('deployment.role')!='deeptutor-upgrade-candidate':raise RuntimeError('Staging container identity changed')
            run([DOCKER,'stop','-t','30',name]);run([DOCKER,'rm',name])

    def apply(self,approval,folder,image):
        self.idle()
        current=inspect('deeptutor')
        if not current['State']['Running']:raise Deferred('Application is stopped; preserve it')
        if shutil.disk_usage(self.root).free<12*1024**3:raise Deferred('Insufficient free space for a safe backup and staged update')
        compose_path=self.root/'compose.yaml';old=compose_path.read_text()
        final=rewrite_compose(old,image,approval['upstream_manifest_digest'])
        trial=rewrite_compose(old,image,approval['upstream_manifest_digest'],local_only=True)
        check=folder/'compose-check.yaml';check.write_text(final)
        run([COMPOSE,'--project-directory',self.root,'-f',check,'config','--quiet'])
        inventory=container_inventory()
        activity=self.root/'data/user/.runtime/workspace-activity.sqlite3'
        if not activity.is_file():raise RuntimeError('Native activity lock database missing')
        handle=sqlite3.connect(activity,timeout=0)
        try:
            handle.execute('BEGIN EXCLUSIVE')
            handle.execute('PRAGMA user_version').fetchone()
        except sqlite3.OperationalError:
            handle.close();raise Deferred('Active browser/data access; upgrade deferred')
        backup=self.state/'backups'/str(time.time_ns());backup.mkdir(parents=True,mode=0o700)
        (backup/'compose.yaml').write_text(old)
        (backup/'previous-image.txt').write_text(current['Image'])
        stopped=False;migrated=False
        try:
            if compose_path.read_text()!=old or inspect('deeptutor')['Id']!=current['Id']:raise RuntimeError('Concurrent deployment change')
            self.gate(True)
            run([DOCKER,'stop','-t','30','deeptutor']);stopped=True
            handle.close();handle=None
            copy_data(self.root/'data',backup/'data')
            self.candidate(folder,image,backup/'baseline.json')
            # First start on the real data is loopback-only. Browser access is
            # kept closed until migration and data checks have passed.
            compose_path.write_text(trial);migrated=True
            self.recreate()
            run([DOCKER,'cp',backup/'baseline.json','deeptutor:/tmp/nas-upgrade-baseline.json'])
            run([DOCKER,'exec','-u','1026:100','-w','/app','-e','PYTHONPATH=/app','deeptutor','python','/opt/deeptutor-nas/verify_runtime.py','--check','/tmp/nas-upgrade-baseline.json'])
            compose_path.write_text(final);self.recreate()
            if inspect('deeptutor')['Image']!=image:raise RuntimeError('Unexpected running image')
            self.reload_tls()
            after=container_inventory()
            for name,value in inventory.items():
                if name!='/deeptutor' and after.get(name)!=value:raise RuntimeError('Unrelated container changed during update')
            result={'status':'updated','source_commit':approval['source_commit'],'version':approval['upstream_version'],'backup':str(backup),'image':image}
            (self.state/'installed.json').write_text(json.dumps(result,indent=2))
            self.gate(False)
            return result
        except BaseException:
            if handle is not None:handle.close();handle=None
            if stopped:
                if migrated:
                    run([DOCKER,'stop','-t','30','deeptutor'])
                    failed=backup/'failed-data'
                    (self.root/'data').rename(failed)
                    copy_data(backup/'data',self.root/'data')
                compose_path.write_text(old);self.recreate();self.reload_tls();self.gate(False)
            else:self.gate(False)
            raise
        finally:
            if handle is not None:handle.close()


def once(apply=False):
    updater=Updater()
    with (updater.state/'update.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Deferred('Another updater owns the deployment')
        if apply:updater.idle()
        approval=updater.approved()
        current=inspect('deeptutor')
        if current['Config'].get('Labels',{}).get('org.opencontainers.image.revision')==approval['source_commit']:
            return {'status':'current','source_commit':approval['source_commit']}
        folder,image=updater.stage(approval)
        result=updater.apply(approval,folder,image) if apply else {'status':'prepared','source_commit':approval['source_commit'],'stage':str(folder),'image':image}
        (updater.state/'last-result.json').write_text(json.dumps(result,indent=2))
        return result


def scheduler_now():
    # Synology's Python omits time.tzset even on Linux. Shanghai currently
    # uses UTC+08:00 without DST; use an aware clock instead of process TZ.
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def daemon():
    state=ROOT/'.updates'
    with (state/'daemon.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        (state/'daemon.pid').write_text(str(os.getpid()))
        day=None;next_try=0
        while True:
            now=scheduler_now()
            if day!=now.date():day=now.date();next_try=0
            if (now.hour==4 and now.minute>=40 or 5<=now.hour<7) and time.monotonic()>=next_try:
                try:
                    result=once(apply=True);print(json.dumps(result),flush=True)
                    next_try=float('inf')
                except Deferred as exc:
                    print('DEFERRED',str(exc),flush=True);next_try=time.monotonic()+900
                except Exception as exc:
                    print('FAILED',type(exc).__name__,str(exc),flush=True);next_try=float('inf')
            time.sleep(30)


def daemon_process():
    file=ROOT/'.updates/daemon.pid'
    if not file.exists():return None
    try:
        pid=int(file.read_text())
        args=Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')
        if str(Path(__file__).resolve()).encode() not in args or b'--daemon' not in args:return None
        return pid
    except (OSError,ValueError):return None


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--daemon',action='store_true')
    parser.add_argument('--daemon-status',action='store_true')
    parser.add_argument('--stop-daemon',action='store_true')
    args=parser.parse_args()
    try:
        if args.daemon_status:print(json.dumps({'running':daemon_process() is not None,'timezone':'Asia/Shanghai','window':'04:40-06:59'}))
        elif args.stop_daemon:
            pid=daemon_process()
            if pid:
                os.kill(pid,signal.SIGTERM)
                for _ in range(30):
                    if daemon_process() is None:break
                    time.sleep(0.1)
                else:raise RuntimeError('Updater daemon did not stop')
            print(json.dumps({'running':False}))
        elif args.daemon:daemon()
        else:print(json.dumps(once(apply=args.apply)),flush=True)
    except Deferred as exc:
        print(json.dumps({'status':'deferred','reason':str(exc)}),flush=True)
