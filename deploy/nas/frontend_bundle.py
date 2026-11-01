"""Publish and verify the CI-built frontend without compiling on the NAS."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile
import urllib.request

REPOSITORY = 'FSOTM/deeptutor-private'
MAX_BYTES = 512 * 1024 * 1024


def validate(metadata, commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):raise ValueError('Invalid frontend commit')
    expected = 'https://github.com/'+REPOSITORY+'/releases/download/nas-'+commit+'/frontend.tar.gz'
    if metadata.get('url') != expected or metadata.get('source_commit') != commit:
        raise ValueError('Frontend bundle belongs to a different source commit')
    if not re.fullmatch(r'[0-9a-f]{64}', metadata.get('sha256','')):
        raise ValueError('Invalid frontend bundle digest')
    if not isinstance(metadata.get('bytes'), int) or not 0 < metadata['bytes'] <= MAX_BYTES:
        raise ValueError('Invalid frontend bundle size')


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def unpack(archive, destination, metadata, commit):
    validate(metadata, commit)
    archive=Path(archive);destination=Path(destination)
    if archive.stat().st_size != metadata['bytes'] or digest(archive) != metadata['sha256']:
        raise ValueError('Frontend bundle checksum mismatch')
    if destination.exists():raise ValueError('Frontend destination already exists')
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers()
        if len(members)>50000 or sum(m.size for m in members)>1024**3:
            raise ValueError('Frontend bundle exceeds extraction limits')
        for member in members:
            rel=Path(member.name)
            if rel.is_absolute() or '..' in rel.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe frontend archive member')
            if rel.name == '.env' or rel.name.startswith('.env.'):
                raise ValueError('Environment files must not be published')
        destination.mkdir(parents=True)
        tar.extractall(destination)
    if not (destination/'server.js').is_file() or not (destination/'.next/static').is_dir():
        raise ValueError('Frontend standalone runtime is incomplete')


def create(source, archive, commit):
    source=Path(source);archive=Path(archive)
    if not (source/'server.js').is_file() or not (source/'.next/static').is_dir():
        raise ValueError('Build the standalone frontend before packaging')
    for file in source.rglob('*'):
        if file.name == '.env' or file.name.startswith('.env.'):
            raise ValueError('Environment files must not be published')
    archive.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive,'w:gz',dereference=True) as tar:
        for file in sorted(source.iterdir()):tar.add(file,arcname=file.name)
    metadata={'source_commit':commit,'url':'https://github.com/'+REPOSITORY+'/releases/download/nas-'+commit+'/frontend.tar.gz',
              'sha256':digest(archive),'bytes':archive.stat().st_size}
    validate(metadata,commit)
    return metadata


def download(approval_path, destination):
    approval=json.loads(Path(approval_path).read_text())
    metadata=approval['frontend_bundle'];commit=approval['source_commit']
    validate(metadata,commit)
    destination=Path(destination)
    archive=destination.parent/'frontend.tar.gz'
    if not archive.exists() or archive.stat().st_size!=metadata['bytes'] or digest(archive)!=metadata['sha256']:
        temporary=archive.with_suffix('.partial')
        request=urllib.request.Request(metadata['url'],headers={'User-Agent':'DeepTutor-NAS-Updater'})
        with urllib.request.urlopen(request,timeout=60) as response,temporary.open('wb') as output:
            size=0
            while True:
                block=response.read(1024*1024)
                if not block:break
                size+=len(block)
                if size>metadata['bytes']:raise ValueError('Frontend download exceeds approved size')
                output.write(block)
        temporary.replace(archive)
    unpack(archive,destination,metadata,commit)
    print(json.dumps({'frontend_verified':True,'source_commit':commit,'bytes':metadata['bytes']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest='command',required=True)
    pack=commands.add_parser('create');pack.add_argument('--source',required=True);pack.add_argument('--archive',required=True);pack.add_argument('--metadata',required=True);pack.add_argument('--commit',required=True)
    fetch=commands.add_parser('download');fetch.add_argument('--approval',required=True);fetch.add_argument('--destination',required=True)
    args=parser.parse_args()
    if args.command=='create':Path(args.metadata).write_text(json.dumps(create(args.source,args.archive,args.commit),indent=2)+'\n')
    else:download(args.approval,args.destination)
