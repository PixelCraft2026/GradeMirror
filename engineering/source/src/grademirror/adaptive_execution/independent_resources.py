"""One existing Win32 fact read at each new-version scientific stage, no monitor."""
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import need,RESOURCE_ENVELOPE
from .memory_snapshot import ram_facts

def check(facts,stage):
 values=dict(physical=facts['physical']['available_bytes'],system_commit=facts['commit']['headroom_bytes'],legacy_process_commit=facts['legacy_process_commit_available'])
 need(all(type(v)is int for v in values.values()),'INDEPENDENT_STAGE_RESOURCE_UNKNOWN',stage+':'+str(values))
 need(all(v>=1024**3 for v in values.values()),'INDEPENDENT_STAGE_RESOURCE_LIMIT',stage+':'+str(values));return values

def stage(c,name):
 if c.get('schema')=='cpu-application-execution-v6':
  from .low_memory_contract import admit
  return admit(c,name)
 need(c['schema']=='cpu-application-execution-v5' and c['TEST_ONLY'] is False,'INDEPENDENT_NATIVE_STAGE_ONLY');facts=ram_facts()
 from uuid import uuid4
 path=Path(c['evidence_root'])/('resource-stage-'+name+'-'+uuid4().hex+'.json');atomic_json(path,dict(stage=name,snapshot=facts,margin_bytes=1024**3,source='GlobalMemoryStatusEx plus GetPerformanceInfo'))
 return check(facts,name)
