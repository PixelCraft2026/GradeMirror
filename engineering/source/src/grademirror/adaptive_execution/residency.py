"""Versioned TEST_ONLY idle leases. No lock is held across IPC or waits.

Lock order is record.lock -> idle-lease.lock -> admission.lock. Owner/host
only take idle-lease.lock (host may then take admission.lock). Old v1 never
enters these branches. The immutable result binding and commit never change.
"""
import time
import uuid
from pathlib import Path
from ..common import atomic_json,strict_load
from ..store import locked
from ..cpu_replay.contract import need

SCHEMA='cpu-application-execution-v2'
POLICY=dict(schema='application-idle-lease-v1',idle_seconds=120,renew_interval_seconds=30,
            request_seconds=120,TEST_ONLY=True,native_authorized=False)

def policy(c):
    from .portable_contract import enabled as portable_enabled
    if portable_enabled(c):return c['lease_policy']
    if c and c.get('schema')=='cpu-application-execution-v5':
        if c['TEST_ONLY']:return POLICY
        from .independent_contract import POLICY as independent_policy
        return independent_policy
    if c and c.get('schema')=='cpu-application-execution-v4':
        from .calibration_contract import measurement
        if measurement(c):return c['lease_policy']
        from .first_contract import POLICY as first_policy
        return first_policy
    if c and c.get('schema')=='cpu-application-execution-v3':
        from .native_contract import POLICY as native_policy
        return native_policy
    return POLICY

def versioned(c):
    return bool(c and (c.get('schema') in (SCHEMA,'cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5')
                       or c.get('light_plan') and c.get('schema')=='gm065-preview-request-v1' and c.get('TEST_ONLY') is False))

def context(spec):
    sem=spec.get('semantics',spec)
    if sem.get('preview_version'):
        from ..light.runtime import application
        if application():
            from ..preview.contract import context as preview_context
            c=preview_context(spec,cleanup=True)
            return c if versioned(c) else None
        return None
    from .context import context as load
    from .first_residency import active_context
    verified=active_context(spec)
    if verified is not None:return verified
    if not spec.get('semantics',spec).get('adaptive_application'):return None
    c=load(spec,cleanup=True)
    if not versioned(c):return None
    if c['schema']=='cpu-application-execution-v5':
        need(c['lease_policy']==policy(c) and c['metadata_only'] is c['TEST_ONLY'],'IDLE_LEASE_AUTHORITY')
        return c
    if c['schema'] in ('cpu-application-execution-v3','cpu-application-execution-v4'):
        need(c['lease_policy']==policy(c) and c['TEST_ONLY'] is False and c['metadata_only'] is False,'IDLE_LEASE_AUTHORITY')
        return c
    need(c['lease_policy']==POLICY and c['TEST_ONLY'] and c['metadata_only'],'IDLE_LEASE_AUTHORITY')
    return c

def lock(session):
    return locked(Path(session)/'idle-lease.lock',timeout=10)

def stopped(session,spec,*,expected_binding=None):
    session=Path(session)
    if (session/'stop.signal').exists() or (Path(spec['operation_path'])/'cancel.signal').exists():return 'STOP_REQUESTED'
    p=session/'lease.json'
    if p.is_file():
        with lock(session):
            lease=strict_load(p)
            if expected_binding is not None and lease.get('binding')!=expected_binding:return 'BINDING_STALE'
            if lease['state'] in ('RELEASE_PENDING','RELEASED'):return 'STOP_REQUESTED'
            if time.time()>=lease['expires_at']:return 'LEASE_EXPIRED'
    return None

def write(host,value):
    with lock(host.session):
        p=host.session/'lease.json'
        if p.exists():
            current=strict_load(p)
            need(current['binding']==value['binding'],'CPU_RESULT_BINDING_STALE')
            if value['state']=='RESIDENT':
                need(current['state'] in ('PREPARED','RESIDENT') and time.time()<current['expires_at'],
                     'RESULT_LEASE_EXPIRED',exit_code=4)
            value=dict(value,expires_at=current['expires_at'])
        atomic_json(p,value)

def renew(host,request,*,peer_pid=None,qualifier_id=None):
    fields={'command','binding','lease_policy','context'}
    from ..cpu_replay.contract import binding
    request_id=None
    if qualifier_id is not None:
        fields.add('qualifier')
        c=host.context
        context_binding=host._r4_consumer.ticket['context']
        request_id=qualifier_id
    elif 'handoff' in request:
        fields.add('handoff')
        from .portable_renewal import authorized_host
        c,context_binding,request_id=authorized_host(host,request,peer_pid)
    else:
        c=context(host.spec);need(c is not None,'KEEP_ALIVE_VERSION_NOT_SUPPORTED',exit_code=4)
        context_binding=binding(c['manifest_path'])
    need(set(request)==fields and request['command']=='KEEP_ALIVE','IDLE_LEASE_REQUEST_FIELDS')
    need(request.get('lease_policy')==policy(c) and request.get('context')==context_binding,
         'IDLE_LEASE_AUTHORITY')
    from ..winproc import identity_alive
    with lock(host.session):
        current=strict_load(host.session/'lease.json');now=time.time()
        if qualifier_id is not None:
            need(time.monotonic()<host.request_deadline,'CPU_HOST_DEADLINE')
        need(current['state']=='RESIDENT' and current['binding']==request['binding']==host.binding,
             'RESULT_NOT_RESIDENT',exit_code=4)
        need(now<current['expires_at'] and not (host.session/'stop.signal').exists() and
             not (Path(host.spec['operation_path'])/'cancel.signal').exists(),'RESULT_LEASE_EXPIRED',exit_code=4)
        need(all(identity_alive(host.binding[k]) is True for k in ('owner','host')),'RESULT_OWNER_UNKNOWN')
        renewed=dict(current,expires_at=now+policy(c)['idle_seconds'])
        from ..cpu_replay.pool import pool
        try:
            if request_id is None:
                pool(host.spec).renew_idle(host.spec,current,renewed)
            else:
                from ..cpu_replay.pool import PortableAdmission
                PortableAdmission(Path(c['pool_root'])).renew_idle(host.spec,current,renewed,verified=c)
            atomic_json(host.session/'lease.json',renewed)
            receipt=dict(schema='application-idle-renewal-v1',binding=host.binding,context=binding(c['manifest_path']),
                policy=policy(c),old_expires_at=current['expires_at'],expires_at=renewed['expires_at'],
                confirmed_at=now,host_confirmed=True,no_compute=True)
            if request_id is not None:
                receipt['request_id']=request_id
            from .portable_contract import renewal_directory
            atomic_json(renewal_directory(host.session)/(uuid.uuid4().hex+'.json'),receipt)
        except BaseException:
            atomic_json(host.session/'lease.json',dict(current,state='RELEASE_PENDING'))
            atomic_json(host.session/'stop.signal',dict(reason='RENEWAL_COMMIT_FAILURE'))
            raise
        return receipt

def decision(session,force,release_requested):
    """Re-read and decide expiry/release atomically against host renewal."""
    session=Path(session)
    with lock(session):
        p=session/'lease.json';lease=strict_load(p) if p.is_file() else None
        expired=bool(lease and time.time()>=lease['expires_at'])
        releasing=release_requested or bool(lease and lease['state'] in ('RELEASE_PENDING','RELEASED'))
        cleanup=bool(force or expired or releasing)
        if cleanup and lease and lease['state']!='RELEASED':
            lease=dict(lease,state='RELEASE_PENDING');atomic_json(p,lease)
        return lease,cleanup
