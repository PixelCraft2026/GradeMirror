"""Path budget and real atomic-write probes for the GM064 validation layout."""
import os
import uuid
from pathlib import Path

from ..common import GMError, atomic_json
from ..cpu_replay.contract import need
from . import portable_contract as layout


def utf16_units(value):
    return len(str(value).encode('utf-16-le')) // 2


def checked(root, path, role):
    root, path = layout.ancestors(root), layout.ancestors(path)
    need(path.is_relative_to(root) and not path.is_relative_to(layout.ROOT),
         'PORTABLE_PATH_SCOPE', str(path))
    limit = layout.layout()['max_utf16']
    length = utf16_units(path)
    if length > limit or any(utf16_units(part) > 255 for part in path.parts[1:]):
        raise GMError('PORTABLE_PATH_BUDGET_EXCEEDED', dict(
            path=str(path), role=role, utf16=length, limit=limit, report=str(root/'report.json')))
    return path


def write_probe(root, directory, role):
    directory = checked(root, directory, role)
    # Match common.atomic_json's same-directory tempfile spelling and length.
    checked(root, directory/'.gm-xxxxxxxx.partial', role+':atomic_temp')
    path = checked(root, directory/'r2-path-probe.json', role+':atomic_replace')
    need(not path.exists(), 'PORTABLE_PATH_PROBE_COLLISION', str(path))
    need(directory.is_dir(),'PORTABLE_PATH_PARENT_MISSING',str(directory))
    try:
        for generation in (1, 2):
            value = dict(role=role, generation=generation)
            atomic_json(path, value)
            need(layout.read(path)==value, 'PORTABLE_PATH_PROBE_READBACK', str(path))
    except (FileNotFoundError, PermissionError) as exc:
        code = 'PORTABLE_PATH_PARENT_MISSING' if isinstance(exc, FileNotFoundError) else 'PORTABLE_PATH_ACCESS_DENIED'
        raise GMError(code, dict(path=str(path), role=role, errno=exc.errno,
                                 winerror=getattr(exc,'winerror',None))) from exc
    finally:
        if path.is_file():
            path.unlink()


def inventory(root):
    """Longest leaves per directory family. Uses unchanged double UUID store IDs."""
    root = layout.session(root)
    uuid36 = '00000000-0000-4000-8000-000000000000'
    hex32 = uuid36.replace('-', '')
    result = {}
    from ..cpu_replay.runtime import runtime_snapshot_names
    snapshot_names=runtime_snapshot_names(uuid36)
    for unit in ('P0','T1','T2','T3','T4'):
        ws = layout.workspace(root)/unit
        op = ws/'store/gm004a-operations'/uuid36/uuid36
        session = op/'session'
        tx = layout.export_transaction(session, 'f'*64) if unit != 'P0' else None
        receiver = layout.receiver_directory(session) if unit != 'P0' else None
        run = root/'runs'/unit/'managed'
        families = {} if unit == 'P0' else {
            'store_index': ws/'store/gm004a-operations/keys'/('f'*64+'.json'),
            'operation': op/'scientific-reservation.json',
            'operation_recipe': op/'new-recipe/r0/independent-r0-parameters.npz',
            'owner': session/'hybrid-first-activation.json',
            'host': session/'host/portable-dry-refusals.json',
            'renewal': layout.renewal_directory(session)/(hex32+'.json'),
            'renewal_handoff_ticket': ws/'operations'/uuid36/'renewal-client.json',
            'renewal_handoff_send': run/('renewal-confirm-'+hex32+'.json'),
            'renewal_handoff_receive': layout.renewal_directory(session)/'r3'/('recv-'+hex32+'.json'),
            'receiver_residency': receiver/uuid36/uuid36/'receiver-residency.json',
            'receiver_index': receiver/'keys'/('f'*64+'.json'),
            'fit': session/'fit/measurement-stages.jsonl',
            'r0': session/'R0/measurement-stages.jsonl',
            'prepare': session/'P3/range-scan-actual.json',
            'solver': session/'P3/solver/before-math-runtime.json',
            'cpu': session/'CPU/before-math-runtime.json',
            'recipe': ws/'operations'/uuid36/'fine-recipe/p3/b.npz',
            'application_context': ws/'.adaptive/contexts'/(uuid36+'.json'),
            'application_control': ws/'.adaptive/control'/hex32/'before-math-runtime.json',
            'qualification_ticket': ws/'.adaptive/control'/hex32/'qualification-consumer.json',
            'application_logs': ws/'.adaptive/logs'/uuid36/'before_launch_application_control.json',
            'preview_export': ws/'exports'/('.gm004a-'+'f'*12+'-'+hex32+'.partial'),
            'export_transaction': tx/'full-readback.json',
            'export_manifest_temp': tx/('.gm004a-manifest-'+'x'*8+'.partial'),
            'ipc_observer_plan': run/'plans'/(hex32+'-base.json'),
            'ipc_observer_entry': run/'entries'/hex32/'entry.json',
            'wait_attachment': run/'outer-owner-release-proof.json',
            'seal_attachment': root/'nonrecursive-exit-attachments.json.partial',
            'report_temp': root/'report.json.partial',
        }
        runtime_directories=dict(owner=session,host=session/'host',fit=session/'fit',r0=session/'R0',
                                 prepare=session/'P3',solver=session/'P3/solver',cpu=session/'CPU',
                                 application_control=ws/'.adaptive/control'/hex32)
        for role,directory in runtime_directories.items():
            for kind,name in snapshot_names.items():families['runtime_'+role+'_'+kind]=directory/name
        for role, path in families.items():
            for kind, candidate in [('file',path), ('json_temp',path.parent/'.gm-xxxxxxxx.partial')]:
                label=f'{unit}:{role}:{kind}'
                checked(root, candidate, label)
                result[label]=dict(path=str(candidate),utf16=utf16_units(candidate))
    return result


def launch_probe(context, work, payload, env, role):
    root = layout.e(context)
    directory = checked(root, work, role)
    checked(root, directory/'launcher.json', role+':launcher')
    checked(root, payload, role+':payload')
    need(env.get('TEMP')==env.get('TMP')==str(directory), 'PORTABLE_PATH_ROLE_TEMP')
    layout.ancestors(layout.ROOT)
    # Parent exists because launch prepares work; recheck all ancestors at use.
    write_probe(root, directory, role)
