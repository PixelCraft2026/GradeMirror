"""Explicit GM063 commit-floor supplement; original default remains16GiB."""
from pathlib import Path
from ..common import strict_load,atomic_json
from ..cpu_replay.contract import need,binding,verify
from . import calibration_contract as k
G=1024**3


def floor(q,request):
    if q is None or q.get('admission_addendum')is None:return 16*G
    need(k.pointer(q['manifest_path'])and q['task']=='GM-063','GM063_ADMISSION_QUALIFICATION')
    accepted=k.authorize(q['manifest_path'],request);need(accepted==q,'GM063_ADMISSION_QUALIFICATION_BYTES')
    b=q['admission_addendum'];need(Path(b['path']).resolve()==k.E/'commit-155-addendum.json','GM063_ADMISSION_POINTER');verify(b)
    a=strict_load(Path(b['path']));need(a['schema']=='gm063-commit-admission-addendum-v1'and a['task']=='GM-063'and a['physical_floor_bytes']==6*G and a['commit_floor_bytes']==31*G//2 and a['original_commit_floor_bytes']==16*G and a['stage_physical_margin_bytes']==a['stage_commit_margin_bytes']==G and a['Job_and_IO_and_workspace_changed']is False and a['numerical_or_production_gates_changed']is False,'GM063_ADMISSION_ADDENDUM_FIELDS')
    return a['commit_floor_bytes']


def admission(c):
    from .memory_snapshot import ram_facts
    p=Path(c['qualification']['path']);q=strict_load(p);minimum=floor(q,q['request']);facts=ram_facts()
    physical=facts['physical']['available_bytes'];system=facts['commit']['headroom_bytes'];legacy=facts['legacy_process_commit_available']
    known=all(type(x)is int and x>=0 for x in(physical,system,legacy))
    row=dict(stage='native_start',physical=physical,commit=legacy,system_commit_headroom=system,physical_floor=6*G,commit_floor=minimum,native=True,unknown=not known,admission_addendum=q.get('admission_addendum'),system_and_legacy_commit_both_required=True)
    atomic_json(Path(c['output_root'])/'current-admission.json',row)
    need(known,'RESOURCE_UNKNOWN',str(row));need(physical>=6*G and system>=minimum and legacy>=minimum,'RESOURCE_NOT_READY',str(row));return row
