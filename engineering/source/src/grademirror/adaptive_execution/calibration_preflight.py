"""Actual dry role interfaces; no model, arrays, codec or solver execution."""
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import need


def entry_checks(role,path,c):
    need(role.startswith('entry_'),'GM063_ENTRY_ONLY')
    effective=role.removeprefix('entry_')
    if effective=='fit':
        from ..cpu_first_fit.worker import validate_payload
        validate_payload(strict_load(path),'fit')
    elif effective=='cpu':
        from ..engine.cpu_postprocess.service import worker
        worker(Path(path),entry_only=True)
    # first_worker.authorize has already checked exact argv role/runtime,
    # session, shapes, mapping descriptors and loopback IPC before preload.
    need(c['source_identity']['shape']==[6000,4000,3] and c['TEST_ONLY'] is False,
         'GM063_ENTRY_EXACT_REAL_INPUT_DOMAIN')
