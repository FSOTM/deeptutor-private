"""Download an official pinned image through HTTPS and create a Docker archive.

The downloader is independent of the Docker daemon's registry connection. Every
compressed layer and unpacked diff ID is checked before Docker sees the archive.
"""
import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tarfile
import threading
import time
import urllib.error
import urllib.request


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return 'sha256:' + h.hexdigest()


def create_archive(lock_path, work, archive):
    lock = json.loads(Path(lock_path).read_text(encoding='utf-8'))
    if lock.get('source')!='ghcr.io/hkuds/deeptutor' or not re.fullmatch(r'\d+\.\d+\.\d+',lock.get('version','')):
        raise ValueError('Unexpected upstream image source')
    descriptors=[{'digest':lock.get('config_digest','')},*lock.get('layers',[])]
    if not lock.get('layers') or len(lock['layers'])>128:
        raise ValueError('Unexpected upstream layer count')
    if any(not re.fullmatch(r'sha256:[0-9a-f]{64}',d.get('digest','')) for d in descriptors):
        raise ValueError('Invalid upstream content digest')
    if any(d.get('size',0)<0 for d in lock['layers']) or sum(d.get('size',0) for d in lock['layers'])>4*1024**3:
        raise ValueError('Upstream image exceeds the automatic download limit')
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen('https://ghcr.io/token?scope=repository:hkuds/deeptutor:pull', timeout=30) as r:
        token = json.load(r)['token']

    def blob(descriptor):
        expected = descriptor['digest']
        target = work / expected.split(':', 1)[1]
        if target.exists() and sha(target) == expected:
            return target
        partial = target.with_suffix('.partial')
        for attempt in range(3):
            headers = {'Authorization': 'Bearer ' + token}
            offset = partial.stat().st_size if partial.exists() else 0
            if offset:
                headers['Range'] = 'bytes=' + str(offset) + '-'
            request = urllib.request.Request('https://ghcr.io/v2/hkuds/deeptutor/blobs/' + expected, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=45) as r:
                    resume = offset and r.status == 206
                    if resume and not r.headers.get('Content-Range', '').startswith('bytes ' + str(offset) + '-'):
                        raise RuntimeError('Unexpected image range response')
                    with partial.open('ab' if resume else 'wb') as out:
                        shutil.copyfileobj(r, out, length=1024 * 256)
                if descriptor.get('size') is not None and partial.stat().st_size != descriptor['size']:
                    raise RuntimeError('Downloaded image blob length mismatch')
                if sha(partial) != expected:
                    raise RuntimeError('Downloaded image blob digest mismatch')
                partial.replace(target)
                print('Verified image blob', expected[7:19], target.stat().st_size, flush=True)
                return target
            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError('Image blob download failed: ' + type(exc).__name__) from None
                time.sleep(2 * (attempt + 1))

    config_path = blob({'digest': lock['config_digest']})
    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['architecture'] == 'amd64' and config['os'] == 'linux'
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        compressed = list(pool.map(blob, lock['layers']))
    diff_ids = config['rootfs']['diff_ids']
    assert len(diff_ids) == len(compressed)
    layers = []
    for path, expected in zip(compressed, diff_ids):
        target = path.with_suffix('.tar')
        if not target.exists() or sha(target) != expected:
            partial = target.with_suffix('.tar.partial')
            with gzip.open(path, 'rb') as src, partial.open('wb') as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)
            assert sha(partial) == expected, 'Unpacked image layer digest mismatch'
            partial.replace(target)
        layers.append(target)
    archive = Path(archive)
    partial = archive.with_suffix('.tar.partial')
    tag = 'local/deeptutor-upstream:' + lock['version'] + '-' + lock['manifest_digest'][7:19]
    manifest = [{'Config': config_path.name + '.json', 'RepoTags': [tag],
                 'Layers': [p.stem + '/layer.tar' for p in layers]}]
    manifest_path = work / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with tarfile.open(partial, 'w') as tar:
        tar.add(config_path, arcname=config_path.name + '.json', recursive=False)
        tar.add(manifest_path, arcname='manifest.json', recursive=False)
        for layer in layers:
            tar.add(layer, arcname=layer.stem + '/layer.tar', recursive=False)
    partial.replace(archive)
    report = {'version': lock['version'], 'config_digest': lock['config_digest'],
              'archive_sha256': sha(archive)[7:], 'archive_bytes': archive.stat().st_size,
              'layers_verified': len(layers), 'tag': tag}
    archive.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Verified Docker archive', json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--lock', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    create_archive(args.lock, args.work, args.archive)
