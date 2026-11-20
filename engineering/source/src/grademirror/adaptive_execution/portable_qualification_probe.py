"""Dry-only GM064 timed IPC qualification. No mapping or numerical creation."""

import hashlib
import time
from multiprocessing.connection import Listener
from pathlib import Path

from ..common import GMError, atomic_json, strict_load
from ..cpu_replay import api, host
from ..cpu_replay.contract import need
from ..winproc import current_identity


def scenario(c):
    allocation = c['science_allocation']
    if allocation not in ('P0-lease-positive', 'P0-lease-timeout'):
        return None
    from .portable_contract import e, read, segmented_fine_qualification
    q = read(c['qualification']['path'])
    need(q['phase'] == 'dry' and segmented_fine_qualification(c)
         and read(e(c)/'session.json').get('n2_recovery'), 'GM064_TIMING_FIXTURE_DRY_ONLY')
    return allocation


def run(role, path, data, c):
    name = scenario(c)
    need(name is not None, 'GM064_TIMING_FIXTURE_SCOPE')
    if role == 'entry_host':
        return _serve(path.parent, name)
    need(role == 'entry_application_control' and data['action'] == 'qualify', 'GM064_TIMING_FIXTURE_ROLE')
    from .control import run as control
    args = data['arguments']
    store = Path(c['workspace']) / 'store'
    from ..gm004a_operations import OperationStore
    op, _ = OperationStore(store).path(args['job_id'], args['attempt_id'])
    mark = op / 'session' / 'quantization.json'
    try:
        value = control(data)
    except (GMError, TimeoutError) as error:
        code = getattr(error, 'code', str(error))
        need(name == 'P0-lease-timeout' and code in {
            'CPU_HOST_DEADLINE', 'CPU_QUALIFY_BATCH_DEADLINE', 'IPC_DEADLINE',
            'TRANSPORT_DEADLINE',
        }, 'GM064_TIMING_NEGATIVE_WRONG_CAUSE:' + code)
        need(not mark.exists(), 'GM064_LATE_QUANTIZATION_PUBLISHED')
        value = dict(refused=code)
    else:
        need(name == 'P0-lease-positive' and len(value['rows']) == 6, 'GM064_TIMING_RESULT')
        rows = value['timing']
        elapsed = rows[-1]['received'] - (value['controller_deadline'] - 120)
        need(elapsed <= 110 and all(x['finished'] - x['started'] >= 12 for x in rows),
             'GM064_TIMING_INSUFFICIENT_HEADROOM:' + str(elapsed))
        need(strict_load(mark)['rows'] == value['rows'], 'GM064_TIMING_PUBLICATION')
        value['elapsed'] = elapsed
    finally:
        api.call(op, 'RELEASE', args['binding'])
    atomic_json(path.parent / 'timing-control.json', dict(value, scenario=name, science=0))


class DryTimingHost(host.Host):
    def _qualify_row(self, alpha, bits):
        need(self.s is None and self.y is None, 'GM064_TIMING_PLANES_FORBIDDEN')
        start = time.monotonic()
        seconds = 12 if self.science_scenario == 'P0-lease-positive' else 24
        while time.monotonic() - start < seconds:
            self.check()
            time.sleep(.1)
        return dict(alpha=alpha, bits=bits,
                    sha256=hashlib.sha256(f'DRY-ONLY:{alpha}:{bits}'.encode()).hexdigest(), stats={})


def _serve(session, name):
    worker = DryTimingHost(session)
    worker.science_scenario = name
    worker.compute_complete = True
    actual = current_identity()
    atomic_json(session / 'host-entry.json', actual)
    with Listener(('127.0.0.1', 0), authkey=bytes.fromhex(worker.spec['auth_hex'])) as listener:
        worker.spec['pipe_address'] = list(listener.address)
        atomic_json(session / 'session-spec.json', worker.spec)
        atomic_json(session / 'host-ready.json', dict(identity=actual, address=list(listener.address)))
        until = time.monotonic() + 30
        while not (session / 'compute-go.json').is_file():
            need(time.monotonic() < until, 'GM064_TIMING_FIXTURE_OWNER_ACK')
            time.sleep(.02)
        worker.binding = strict_load(session / 'lease.json')['binding']
        outcome = host.serve(worker, listener)
    need(outcome == 'EXITED', 'GM064_TIMING_HOST_EXIT:' + outcome)
    atomic_json(session / 'timing-host.json', dict(scenario=name, science=0, rows=len(worker._qualification_rows)))
