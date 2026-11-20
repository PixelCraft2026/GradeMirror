"""GM064-R4 independent, pinned qualification consumer for the six fixed parts."""
import hashlib
import os
import sys
import time
import uuid
from pathlib import Path

from ..common import atomic_json, canonical_json, strict_load
from ..cpu_replay.contract import ROOT, binding, need
from ..winproc import current_identity, identity_alive
from . import portable_contract as portable
from .portable_renewal import Pins, _pin_runtime, _scope_rows, prepare_peer_table

SCHEMA = 'gm064-r4-qualification-consumer-v1'
MEMBER = 'validation/r4-target.json'


def enabled(c):
    return portable.segmented_fine_qualification(c) and (ROOT/MEMBER).is_file()


def _hex(value):
    return type(value) is str and len(value)==32 and all(ch in '0123456789abcdef' for ch in value)


def _retained(work, who):
    observer=Path(os.environ['GM052_OBSERVER_ROOT'])/'entries'
    for entry in observer.glob('*/entry.json'):
        row=strict_load(entry)
        if row.get('work')==str(work) and row.get('identity')==who:
            ack=entry.parent/'ack.json'
            proof=strict_load(ack)
            need(row['role'] in ('application_control','entry_application_control')
                 and proof.get('actual_handle_retained') is True and proof.get('identity')==who
                 and strict_load(work/'actual-entry.json')['identity']==who,
                 'GM064_R4_CONSUMER_NOT_RETAINED')
            return entry,ack
    need(False,'GM064_R4_CONSUMER_ENTRY_MISSING')


def _proof_rows(c,path):
    path=Path(path)
    rows=_scope_rows(c)+[binding(portable.package_member(MEMBER)),binding(path/'spec.json'),binding(path/'commit.json')]
    rows.append(binding(path/'session/session-spec.json'))
    rows.extend(strict_load(path/'commit.json')['artifacts'])
    return rows


def _scope(pins,c,path):
    for row in _proof_rows(c,path):
        pins.add(row)
    _pin_runtime(c,pins)


def _capability(c):
    path=portable.package_member(MEMBER)
    need(strict_load(path)['protocol']==SCHEMA,'GM064_R4_PROTOCOL_VERSION')


def _stamp():
    return dict(monotonic=time.monotonic(),wall=time.time())


def save(directory,name,record):
    try:
        atomic_json(Path(directory)/(name+'.json'),record)
    except Exception as error:
        # Preserve diagnostics without substituting their exception for the primary.
        from ..cpu_replay.runtime import stderr_error
        try:
            stderr_error(RuntimeError(f'R4_OBSERVATION {name} {record!r} {error!r}'))
        except Exception:
            if sys.stderr is not None:
                print(f'R4_OBSERVATION {name} {error!r}',file=sys.stderr)


def consume(c,path,spec,lease,work):
    """Only the separately retained public qualification worker uses this path."""
    from ..cpu_replay import api
    from .context import load

    prepare_started=time.monotonic()
    work=Path(work)
    who=current_identity()
    pins=Pins()
    try:
        prepare_peer_table()
        _scope(pins,c,path)
        _capability(c)
        # Repeat the entry verification once *after* the same handles are held.
        need(load(c['manifest_path'])==c and strict_load(path/'spec.json')==spec,
             'GM064_R4_CONSUMER_CONTEXT_CHANGED')
        api.check_binding(path,spec,lease)
        entry,ack=_retained(work,who)
        for leaf in (entry,ack,work/'payload.json',work/'actual-entry.json'):
            pins.add(binding(leaf))
        ticket=dict(schema=SCHEMA,identity=who,context=binding(c['manifest_path']),
                    manifest=binding(ROOT/'portable-manifest.json'),operation=str(path),binding=lease,
                    nonce=uuid.uuid4().hex)
        ticket_path=work/'qualification-consumer.json'
        atomic_json(ticket_path,ticket)
        raw=canonical_json(ticket)+b'\n'
        proof=dict(path=str(ticket_path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
        pins.add(proof)
        start=_stamp()
        api.call(path,'QUALIFY_OPEN',lease,consumer=proof)
        save(work,'qualification-prepared',dict(schema=SCHEMA,identity=who,preparation_started=prepare_started,
             prepared=start,open_returned=_stamp(),logical_pin_hash_bytes=pins.hashed_bytes,
             protected_files=len(pins.rows),full_binding_checks=1))
        rows=[];timings=[];deadline=None
        for index,(alpha,bits) in enumerate(portable.FINE_QUALIFICATION_ROWS):
            rid=uuid.uuid4().hex
            auth=dict(schema=SCHEMA,nonce=ticket['nonce'],index=index,request_id=rid)
            trace=dict(schema=SCHEMA,index=index,request_id=rid,operation=str(path),identity=who,
                       start=_stamp(),batch_deadline=deadline,renewal={},part={})
            try:
                need(pins.valid(),'GM064_R4_CONSUMER_PINS_CLOSED')
                renewed=api.call(path,'KEEP_ALIVE',lease,qualifier=auth,lease_policy=c['lease_policy'],
                                 context=ticket['context'],deadline_monotonic=deadline,trace=trace['renewal'])
                need(renewed.get('host_confirmed') is True and renewed.get('binding')==lease
                     and renewed.get('request_id')==rid and renewed['confirmed_at']<renewed['old_expires_at'],
                     'CPU_QUALIFY_RENEWAL_NOT_CONFIRMED')
                trace['lease_confirmation']={key:renewed[key]
                    for key in ('confirmed_at','old_expires_at','expires_at')}
                if deadline is None:
                    clock=_stamp()
                    deadline=clock['monotonic']+c['request_seconds']
                    save(work,'qualification-deadline',dict(clock,deadline=deadline,identity=who))
                trace['batch_deadline']=deadline
                reply=api.call(path,'QUALIFY_PART',lease,index=index,qualifier=auth,
                               deadline_monotonic=deadline,trace=trace['part'])
                received=time.monotonic()
                need(received<deadline,'CPU_QUALIFY_BATCH_DEADLINE')
                row=reply['row']
                need(row['alpha']==alpha and row['bits']==bits,'CPU_QUALIFY_PART_REPLY')
                rows.append(row)
                timings.append(dict(reply['timing'],received=received,renewed_at=renewed['confirmed_at']))
                trace.update(result='PASS',sha256=row['sha256'])
            except BaseException as error:
                trace.update(result='STOP',error=getattr(error,'code',type(error).__name__),message=str(error))
                raise
            finally:
                save(work,f'qualification-{index}',dict(trace,end=_stamp(),repeated_full_binding_checks=0))
        return dict(rows=rows,timing=timings,controller_deadline=deadline)
    finally:
        pins.close()


class HostConsumer:
    """Single-use proof owned by one host and one exact retained controller."""
    def __init__(self,host,request,pid):
        from ..cpu_replay import api
        from .first_residency import verify_inputs

        c=host.context
        need(enabled(c) and set(request)=={'command','binding','consumer'},'GM064_R4_OPEN_FIELDS')
        need(request['binding']==host.binding,'CPU_RESULT_BINDING_STALE')
        proof=request['consumer']
        need(type(proof) is dict and set(proof)=={'path','bytes','sha256'}
             and type(proof['bytes']) is int and 0<proof['bytes']<=16384,'GM064_R4_TICKET_FIELDS')
        p=Path(proof['path'])
        expected_parent=Path(c['workspace'])/'.adaptive/control'
        need(p.name=='qualification-consumer.json' and p.parent.parent==expected_parent
             and _hex(p.parent.name),'GM064_R4_TICKET_SCOPE')
        self.pins=Pins()
        self.poisoned=False
        started=time.monotonic()
        try:
            self.pins.add(proof)
            ticket=strict_load(p)
            need(set(ticket)=={'schema','identity','context','manifest','operation','binding','nonce'}
                 and ticket['schema']==SCHEMA and _hex(ticket['nonce'])
                 and ticket['identity']['pid']==pid and ticket['operation']==str(host.path)
                 and ticket['binding']==host.binding,'GM064_R4_TICKET_IDENTITY')
            self.ticket=ticket
            self.assert_live()
            entry,ack=_retained(p.parent,ticket['identity'])
            for leaf in (entry,ack,p.parent/'payload.json',p.parent/'actual-entry.json'):
                self.pins.add(binding(leaf))
            need(_retained(p.parent,ticket['identity'])==(entry,ack),'GM064_R4_RETAINED_BYTES')
            payload=strict_load(p.parent/'payload.json')
            need(payload==dict(action='qualify',arguments=dict(job_id=host.spec['job_id'],
                 attempt_id=host.spec['attempt_id'],binding=host.binding)),'GM064_R4_CONTROL_ACTION')
            _scope(self.pins,c,host.path)
            _capability(c)
            need(ticket['context']==binding(c['manifest_path'])==host.spec['semantics']['instance_context']
                 and ticket['manifest']==binding(ROOT/'portable-manifest.json')
                 and verify_inputs(host.spec)==c,'GM064_R4_HOST_CONTEXT')
            operation_spec=strict_load(host.path/'spec.json')
            need(all(operation_spec[key]==host.spec[key] for key in ('job_id','attempt_id','semantics')),
                 'GM064_R4_OPERATION_SESSION_MISMATCH')
            api.check_binding(host.path,operation_spec,host.binding)
            host.require_binding(request)
            self.index=0
            self.renewed=None
            self.used=set()
            save(host.session/'host','qualification-open',dict(schema=SCHEMA,operation=str(host.path),
                 consumer=ticket['identity'],identity=current_identity(),clock=_stamp(),
                 full_validation_seconds=time.monotonic()-started,logical_pin_hash_bytes=self.pins.hashed_bytes,
                 protected_files=len(self.pins.rows),full_binding_checks=1))
        except BaseException:
            self.pins.close()
            raise

    def assert_live(self):
        need(not self.poisoned and self.pins.valid() and identity_alive(self.ticket['identity']) is True,
             'GM064_R4_CONSUMER_INVALID')

    def authorize(self,host,request,pid):
        self.assert_live()
        auth=request['qualifier']
        command=request['command']
        fields={'command','binding','qualifier'}|({'lease_policy','context'} if command=='KEEP_ALIVE' else {'index'})
        need(command in ('KEEP_ALIVE','QUALIFY_PART') and set(request)==fields and
             type(auth) is dict and set(auth)=={'schema','nonce','index','request_id'}
             and auth['schema']==SCHEMA and auth['nonce']==self.ticket['nonce']
             and request['binding']==host.binding==self.ticket['binding']
             and self.ticket['identity']['pid']==pid and type(auth['index']) is int
             and auth['index']==self.index<len(portable.FINE_QUALIFICATION_ROWS)
             and _hex(auth['request_id']),'GM064_R4_CONSUMER_REQUEST')
        rid=auth['request_id']
        if command=='KEEP_ALIVE':
            need(self.renewed is None and rid not in self.used,'GM064_R4_RENEWAL_ORDER')
        else:
            need(request['index']==self.index and self.renewed==rid,'GM064_R4_PART_ORDER')
        return rid

    def close(self):
        self.poisoned=True
        self.pins.close()
