"""Resolve and verify the public upstream image; never log registry tokens."""
import hashlib
import json
import re
import urllib.request


def resolve(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):
        raise ValueError('Only stable numeric release versions are accepted')
    with urllib.request.urlopen('https://ghcr.io/token?scope=repository:hkuds/deeptutor:pull',timeout=30) as r:
        token=json.load(r)['token']
    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'}
    def get(path):
        request=urllib.request.Request('https://ghcr.io/v2/hkuds/deeptutor/'+path,headers=headers)
        with urllib.request.urlopen(request,timeout=30) as r:
            raw=r.read();digest='sha256:'+hashlib.sha256(raw).hexdigest()
            advertised=r.headers.get('Docker-Content-Digest')
            if advertised and advertised!=digest:raise ValueError('Registry content digest mismatch')
        return json.loads(raw),digest
    index,index_digest=get('manifests/'+version)
    if 'manifests' in index:
        entry=next(m for m in index['manifests'] if m.get('platform',{}).get('os')=='linux' and m.get('platform',{}).get('architecture')=='amd64')
        manifest,digest=get('manifests/'+entry['digest'])
        if digest!=entry['digest']:raise ValueError('Platform manifest digest mismatch')
    else:manifest,digest=index,index_digest
    config,config_digest=get('blobs/'+manifest['config']['digest'])
    if config_digest!=manifest['config']['digest'] or config.get('os')!='linux' or config.get('architecture')!='amd64':
        raise ValueError('Upstream image identity mismatch')
    return {'version':version,'source':'ghcr.io/hkuds/deeptutor','index_digest':index_digest,
            'manifest_digest':digest,'config_digest':config_digest,'platform':'linux/amd64',
            'compressed_bytes':sum(l['size'] for l in manifest['layers']),
            'source_revision':config.get('config',{}).get('Labels',{}).get('org.opencontainers.image.revision'),
            'layers':manifest['layers']}
