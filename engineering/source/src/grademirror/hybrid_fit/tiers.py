"""Finite metadata-only stripe intent; never selects or changes a running tier."""
from ..common import GMError

def rows(value):
    if type(value)is not int or value not in (256,512):
        raise GMError('CPU_CORE_ROWS_INVALID','CPU stripe rows must be integer 256 or 512',5)
    return value

def effective(resource_request=None):
    return rows((resource_request or {}).get('cpu_core_rows',512))

def for_spec(spec):
    sem=spec['semantics'];intent=sem.get('resource_intent');core=effective(intent)
    if core==256 and not (spec['kind']=='replay'and sem.get('backend_id')=='cuda-r0-cpu-lld10-v1'and
            sem.get('fit_contract')=='gm023-hybrid-fit-v1'and intent.get('policy')=='guarded-v1'):
        raise GMError('CPU_CORE_ROWS_SCOPE','256 rows require guarded new-package hybrid replay',5)
    return core

def consistent(expected,*values):
    expected=rows(expected)
    if any(rows(value)!=expected for value in values):
        raise GMError('CPU_CORE_ROWS_CONFLICT','Immutable stripe bindings disagree',5)
    return expected
