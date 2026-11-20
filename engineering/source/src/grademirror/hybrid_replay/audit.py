"""Durable operation accounting and optional fixed qualification caps."""
import json, os, time
from pathlib import Path
from ..common import atomic_json, strict_load, GMError
from ..store import locked


def append(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps(value,separators=(',',':'),allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())


def qualification(kind,label,counts):
    root=os.environ.get('GM021_QUALIFICATION_ROOT')
    if not root:return
    root=Path(root).resolve()
    if root==Path(__file__).resolve().parents[3]/'docs/evidence/gm-023/20261023-v1':
        from ..hybrid_fit.audit import qualification as new_qualification
        return new_qualification(kind,label,counts)
    expected=Path(__file__).resolve().parents[3]/'docs/evidence/gm-021/20261023-v1'
    if root==expected.parents[1]/'gm-021-r3/20261023-v1':
        config=strict_load(root/'queue.json');phase=os.environ['GM021_PHASE']
        label=os.environ['GM021_RUN_LABEL']+':'+label
        caps={'P0':8,'P1':144,'closeout':8}
        io={'source_small_JPG_decodes':12,'saved_field_file_loads':48,
            'qualification_quantization_passes':64,'preview_generations':12,'preview_JPG':0,
            'export_requests':4,'native_JPG_encodes':2,'native_export_full_readbacks':2}
        if config['launch_caps']!=caps or phase not in caps:raise GMError('QUALIFICATION_CONFIG_INVALID','R3 fixed caps',7)
        file=root/('starts.jsonl' if kind=='starts' else 'science.jsonl')
        with locked(root/'accounting.lock'):
            old=[json.loads(x) for x in file.read_text(encoding='utf-8').splitlines()] if file.exists() else []
            if any(x['label']==label for x in old):raise GMError('DUPLICATE_QUALIFICATION_CALL',label,7)
            if any(type(v)is not int or v<0 for v in counts.values()):raise GMError('QUALIFICATION_COUNT_INVALID',label,7)
            if kind=='starts':
                if label not in config['launch_labels'] or counts!={'count':config['launch_labels'][label]['count']}:raise GMError('UNPLANNED_START',label,7)
                if sum(x['counts']['count'] for x in old if x['phase']==phase)+counts['count']>caps[phase] or sum(x['counts']['count'] for x in old)+counts['count']>160:raise GMError('START_CAP',phase,7)
            else:
                if phase!='P1' or any(k not in io and v for k,v in counts.items()):raise GMError('R3_ALGORITHM_FORBIDDEN',label,7)
                for k,v in counts.items():
                    if k in io and sum(x['counts'].get(k,0) for x in old)+v>io[k]:raise GMError('R3_SMALL_IO_CAP',k,7)
            append(file,dict(label=label,phase=phase,counts=counts,wall=time.time(),monotonic=time.monotonic()))
        return
    if root==expected.parents[1]/'gm-021-r2/20261023-v1':
        config=strict_load(root/'queue.json');phase=os.environ['GM021_PHASE']
        label=os.environ['GM021_RUN_LABEL']+':'+label
        caps={'P0':12,'P1':256,'closeout':8}
        io={'source_small_JPG_decodes':24,'qualification_quantization_passes':120,
            'preview_generations':40,'preview_JPG':4,'export_requests':6,
            'native_JPG_encodes':3,'native_export_full_readbacks':3,'saved_field_file_loads':112}
        if config['launch_caps']!=caps or phase not in caps:raise GMError('QUALIFICATION_CONFIG_INVALID','R2 fixed caps',7)
        file=root/('starts.jsonl' if kind=='starts' else 'science.jsonl')
        with locked(root/'accounting.lock'):
            old=[json.loads(x) for x in file.read_text(encoding='utf-8').splitlines()] if file.exists() else []
            if any(x['label']==label for x in old):raise GMError('DUPLICATE_QUALIFICATION_CALL',label,7)
            if any(type(v)is not int or v<0 for v in counts.values()):raise GMError('QUALIFICATION_COUNT_INVALID',label,7)
            if kind=='starts':
                if label not in config['launch_labels'] or counts!={'count':config['launch_labels'][label]['count']}:raise GMError('UNPLANNED_START',label,7)
                if sum(x['counts']['count'] for x in old if x['phase']==phase)+counts['count']>caps[phase] or sum(x['counts']['count'] for x in old)+counts['count']>276:raise GMError('START_CAP',phase,7)
            else:
                if phase!='P1' or any(k not in io and v for k,v in counts.items()):raise GMError('R2_ALGORITHM_FORBIDDEN',label,7)
                for k,v in counts.items():
                    if k in io and sum(x['counts'].get(k,0) for x in old)+v>io[k]:raise GMError('R2_SMALL_IO_CAP',k,7)
            append(file,dict(label=label,phase=phase,counts=counts,wall=time.time(),monotonic=time.monotonic()))
        return
    r1=expected.parents[1]/'gm-021-r1/20261023-v1'
    if root==r1:
        config=strict_load(root/'queue.json');phase=os.environ['GM021_PHASE']
        label=os.environ['GM021_RUN_LABEL']+':'+label
        caps={'P0':12,'P1':256,'closeout':8}
        io={'source_small_JPG_decodes':24,'qualification_quantization_passes':120,
            'preview_generations':64,'preview_JPG':12,'export_requests':12,
            'native_JPG_encodes':6,'native_export_full_readbacks':6,'saved_field_file_loads':128}
        if config['launch_caps']!=caps or phase not in caps:
            raise GMError('QUALIFICATION_CONFIG_INVALID','Fixed R1 caps changed',7)
        file=root/('starts.jsonl' if kind=='starts' else'science.jsonl')
        with locked(root/'accounting.lock'):
            old=[json.loads(x) for x in file.read_text(encoding='utf-8').splitlines()] if file.exists() else[]
            if any(x['label']==label for x in old):raise GMError('DUPLICATE_QUALIFICATION_CALL',label,7)
            if any(type(v)is not int or v<0 for v in counts.values()):raise GMError('QUALIFICATION_COUNT_INVALID',label,7)
            if kind=='starts':
                if label not in config['launch_labels'] or counts!={'count':config['launch_labels'][label]['count']}:
                    raise GMError('UNPLANNED_START',label,7)
                if sum(x['counts']['count'] for x in old if x['phase']==phase)+counts['count']>caps[phase] or sum(x['counts']['count'] for x in old)+counts['count']>276:
                    raise GMError('START_CAP',phase,7)
            else:
                if phase!='P1' or any(k not in io and v for k,v in counts.items()):
                    raise GMError('R1_ALGORITHM_FORBIDDEN',label,7)
                for k,v in counts.items():
                    if k in io and sum(x['counts'].get(k,0) for x in old)+v>io[k]:raise GMError('R1_SMALL_IO_CAP',k,7)
            append(file,dict(label=label,phase=phase,counts=counts,wall=time.time(),monotonic=time.monotonic()))
        return
    if root not in (expected,expected/'supplement-1',expected/'supplement-2',expected/'supplement-3',expected/'supplement-4'):raise GMError('QUALIFICATION_ROOT_INVALID','Unrecognized qualification root',7)
    config=strict_load(root/'queue.json');phase=os.environ['GM021_PHASE'];prefix=os.environ['GM021_RUN_LABEL']
    label=prefix+':'+label
    file=root/('starts.jsonl' if kind=='starts' else'science.jsonl')
    with locked(root/'accounting.lock'):
        old=[json.loads(x) for x in file.read_text(encoding='utf-8').splitlines()] if file.exists() else[]
        if any(r['label']==label for r in old):raise GMError('DUPLICATE_QUALIFICATION_CALL',label,7)
        if kind=='starts':
            if label not in config['launch_labels']:raise GMError('UNPLANNED_START',label,7)
            if sum(r['counts']['count'] for r in old if r['phase']==phase)+counts['count']>config['launch_caps'][phase]:raise GMError('START_CAP',phase,7)
            if sum(r['counts']['count'] for r in old)+counts['count']>192:raise GMError('GLOBAL_START_CAP',phase,7)
        elif phase=='P2':
            for k,v in counts.items():
                if k not in config['science_caps'] or sum(r['counts'].get(k,0) for r in old if r['phase']=='P2')+v>config['science_caps'][k]:raise GMError('SCIENCE_CAP',k,7)
        elif phase=='P1':
            allowed={'qualification_quantization_passes','preview_generations','preview_JPG',
                     'export_requests','native_JPG_encodes','native_export_full_readbacks'}
            if any(v for k,v in counts.items() if k not in allowed):raise GMError('SCIENCE_OUTSIDE_P2',label,7)
        elif any(counts.values()):raise GMError('SCIENCE_OUTSIDE_P2',label,7)
        append(file,dict(label=label,phase=phase,counts=counts,wall=time.time(),monotonic=time.monotonic()))


def start(label,count=1):qualification('starts',label,dict(count=count))


def science(path,label,**counts):
    qualification('science',label,counts)
    append(Path(path)/'science-reservations.jsonl',dict(label=label,counts=counts,wall=time.time(),monotonic=time.monotonic()))


def progress(path,stage,**values):
    from .resource_journal import publish_progress
    from ..light.runtime import operation
    if operation() is not None:
        values["gm066_monotonic"]=time.monotonic()
    publish_progress(path,dict(detail_stage=stage,wall=time.time(),**values))
