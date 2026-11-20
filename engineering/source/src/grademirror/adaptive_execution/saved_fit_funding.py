"""Exactly one new T3 remainder; retained T4 is never added to the cap twice."""
import json,os,time
from pathlib import Path
from ..common import atomic_json,canonical_json
from ..store import locked
from ..cpu_replay.contract import need,binding,verify,ROOT
from . import portable_contract as k
from .saved_fit_recovery import authority,AUTHORITY


def clock(mark,root,now,unit):
    r=mark['t34r3_recovery'];root=Path(root)
    need(root==k.session_path(root) and not mark.get('local_only') and unit in ('T3','T4'),'R3_WINDOW_SCOPE')
    need(set(r)=={'schema','authority','window','nonce'} and r['schema']=='gm064-t34r3-session-v1' and
         r['authority']==binding(k.package_member(AUTHORITY)) and Path(r['window']['path'])==root/'science/t34r3-window.json','R3_WINDOW_AUTHORITY')
    verify(r['window']);v=k.read(r['window']['path'])
    need(v['schema']=='gm064-t34r3-window-v1' and v['session']==str(root) and v['nonce']==r['nonce'] and
         type(v['nonce'])is str and len(v['nonce'])==32 and set(v['nonce'])<=set('0123456789abcdef') and
         v['manifest']==binding(ROOT/'portable-manifest.json') and v['authority']==r['authority'] and
         v['seconds']==28800 and v['deadline_at']==v['started_at']+28800 and
         1793788456.605<=v['started_at']<=now<=v['deadline_at']<=1793788456.605+168*3600,'R3_WINDOW_EXPIRED')
    return v['deadline_at']


def normalized(value,keys):
    need(type(value)is dict and not set(value)-set(keys) and all(type(x)is int and x>=0 for x in value.values()),'R3_COUNTER_UNKNOWN')
    return {key:value.get(key,0) for key in keys}


def plan(rows,name,phase,budget,a):
    s=a['science'];keys=s['T3_recovery'].keys()
    need(len(keys)==31 and keys==s['T4_retained'].keys(),'R3_COUNTER_DIMENSIONS')
    need(name in ('T3','T4') and [x['id']for x in rows]==(['T3'] if name=='T4' else []) and
         phase==('fast_recovery' if name=='T3' else 'refine'),'R3_FIXED_ORDER')
    expected=s['T3_recovery' if name=='T3' else 'T4_retained']
    need(normalized(budget,keys)==expected,'R3_EXACT_BUDGET')
    for r in rows:
        need(r['no_refund'] is True and normalized(r['budget'],keys)==s['T3_recovery'],'R3_PRIOR_PREPAYMENT')
    need(all(s['proposed_cap'][key]==s['original_cap'][key]+s['T3_recovery'][key] and
             s['proposed_complete_projection'][key]==s['original_complete_projection'][key]+s['T3_recovery'][key] and
             s['proposed_complete_projection'][key]<=s['proposed_cap'][key] for key in keys),'R3_NO_DOUBLE_T4')
    return s['proposed_complete_projection']


def reserve(q):
    from .portable_science import budget
    root=k.session(q['manifest_path']);mark=k.read(root/'session.json');a=authority();phase=next(iter(q['allocations']));name=q['allocations'][phase]
    clock(mark,root,time.time(),name);b=budget(phase,q)
    with locked(root/'science-budget.lock'):
        p=root/'science-budget.jsonl';rows=[json.loads(x)for x in p.read_bytes().splitlines()]
        projection=plan(rows,name,phase,b,a)
        row=dict(kind='unit_reserve',id=name,domain='24mp',phase=phase,budget=b,full_31_counters=normalized(b,a['science']['T3_recovery']),
            recovery_label='T3_RECOVER' if name=='T3' else 'T4_RETAINED',authority=binding(k.package_member(AUTHORITY)),
            complete_projection=projection,wall=time.time(),before_any_science_or_IO=True,no_refund=True)
        with p.open('ab')as f:f.write(canonical_json(row)+b'\n');f.flush();os.fsync(f.fileno())
    return row
