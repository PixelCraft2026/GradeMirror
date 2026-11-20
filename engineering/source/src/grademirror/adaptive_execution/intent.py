"""Strict mathematical intent, separate from server identities and resource facts."""
import math
from pathlib import Path
from ..common import GMError,canonical_json,sha_bytes

def need(ok,code='ADAPTIVE_REQUEST_INVALID',message=None):
    if not ok:raise GMError(code,message or code,5)

def digest(value):return sha_bytes(canonical_json(value))

def normalize(value):
    need(type(value) is dict and set(value)=={'schema','quality','operation','execution_preference','source','reference','recipe','limits','output'})
    need(value['schema']=='adaptive-intent-v1' and value['quality'] in ('fast','fine') and
        value['operation'] in ('fit','refine','replay','resident_preview','export') and
        value['execution_preference'] in ('current','auto','cpu'))
    for name in ('source','reference'):
        item=value[name]
        if item is None:continue
        need(type(item) is dict and set(item)=={'file','metadata'})
        file=item['file'];meta=item['metadata']
        need(type(file) is dict and set(file)=={'path','bytes','sha256'} and type(file['path']) is str and
            Path(file['path']).is_absolute() and type(file['bytes']) is int and file['bytes']>=0 and
            type(file['sha256']) is str and len(file['sha256'])==64 and set(file['sha256'])<=set('0123456789abcdef'))
        need(type(meta) is dict and set(meta)=={'width','height','bits','format'} and
            all(type(meta[k]) is int and 0<meta[k]<=200000 for k in ('width','height')) and
            meta['bits'] in (8,16) and meta['format'] in ('JPEG','PNG','TIFF','TEST_FRAME'))
    need(value['source'] is not None)
    need(value['operation'] not in ('fit','refine') or value['reference'] is not None)
    if value['recipe'] is not None:
        need(type(value['recipe']) is dict and set(value['recipe'])=={'directory','bundle_id','math_id','version'} and
            Path(value['recipe']['directory']).is_absolute() and value['recipe']['version'] in (2,3))
    need(value['operation'] not in ('replay','refine') or value['recipe'] is not None)
    need(value['operation']!='refine' or value['quality']=='fine')
    need(value['operation']!='fit' or value['recipe'] is None)
    need(value['operation'] in ('fit','refine') or value['reference'] is None)
    if value['operation']=='refine':need(value['recipe']['version'] in (2,3))
    if value['operation']=='replay':need(value['recipe']['version'] in ((2,3) if value['quality']=='fast' else (3,)))
    limits=value['limits'];need(type(limits) is dict and set(limits)=={'ram_bytes','commit_bytes','vram_bytes'})
    need(all(x is None or type(x) is int and 0<=x<2**63 for x in limits.values()))
    need(type(value['output']) is dict and set(value['output'])=={'format','icc_policy','bit_depth'} and
        value['output']['format']=='source' and value['output']['icc_policy']=='preserve_source' and value['output']['bit_depth']=='source')
    return __import__('json').loads(canonical_json(value))
