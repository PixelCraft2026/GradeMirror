"""Portable CPU host: owns S/O/Y, consumes original frames, serves old codecs."""
import gc
import hashlib
import math
import os
import secrets
import time
import uuid
from pathlib import Path
from multiprocessing.connection import Listener

from ..common import atomic_json, strict_load
from ..gm003_ipc import send_json, recv_json
from ..winproc import current_identity, identity_alive, process_identity
from .contract import BACKEND, need, digest, authority, output, worker_authority
from . import runtime, lifecycle as life


def array_hash(value):
    return hashlib.sha256(memoryview(value).cast('B')).hexdigest()


class Host:
    def __init__(self, session):
        self.session = Path(session)
        self.spec = worker_authority(self.session)
        self.sem = self.spec['semantics']
        self.path = Path(self.spec['operation_path'])
        self.smap = self.omap = self.receiver = None
        self.s = self.o = self.y = self._display_reference = None
        self.icc = None
        self.counts = dict(source_decode=0, source_copy=0, R0_render=0, forward=0, CPU256_service=0,
                           qualification_quantization=0, preview=0, preview_JPG=0,
                           native_JPG_encode=0, native_JPG_full_readback=0, mix_traversals=0)
        self.binding = None
        self.context = life.context(self.spec)
        self.compute_complete = False
        self.request_deadline = None
        self._qualification_rows = []
        self._qualification_deadline = None
        self._qualification_failed = False
        self.deadline = time.monotonic() + (900 if self.sem.get('instance_context') and self.context and self.context.get('TEST_ONLY') else
                                           7200 if self.sem.get('first_fit') or self.sem.get('refine_prepare') else 1800 if self.sem['mode'] == 'fast' else 6000)

    def check(self):
        from ..adaptive_execution.portable_resources import pressure
        stats=getattr(self,'_r4_row_stats',None)
        if stats is None:
            pressure(self)
            need(not (self.path / 'cancel.signal').exists() and not (self.session / 'stop.signal').exists(), 'CANCELLED', exit_code=8)
            need(not life.stopping(self.session, self.spec), 'CPU_RESIDENT_STOPPED', exit_code=8)
            deadline = self.request_deadline if self.compute_complete else self.deadline
            need(deadline is None or time.monotonic() < deadline, 'CPU_HOST_DEADLINE')
            return
        from ..adaptive_execution.residency import stopped
        stats['checks']+=1
        start=time.monotonic()
        try:
            pressure(self)
            stats['pressure_seconds']+=time.monotonic()-start
            start=time.monotonic()
            need(not (self.path / 'cancel.signal').exists() and not (self.session / 'stop.signal').exists(), 'CANCELLED', exit_code=8)
            self._r4_consumer.assert_live()
            stats['authority_and_cancel_seconds']+=time.monotonic()-start
            start=time.monotonic()
            need(not stopped(self.session,self.spec,expected_binding=self.binding),'CPU_RESIDENT_STOPPED',exit_code=8)
            stats['lease_seconds']+=time.monotonic()-start
            start=time.monotonic()
            need(time.monotonic()<self.request_deadline,'CPU_HOST_DEADLINE')
            stats['deadline_seconds']+=time.monotonic()-start
        except BaseException as error:
            stats.update(failed_check=stats['checks'],failed_check_elapsed=time.monotonic()-start,
                         error=getattr(error,'code',type(error).__name__))
            raise

    def progress(self, stage, **detail):
        if self.context and self.context.get("light_plan"):
            detail.setdefault("gm066_monotonic",time.monotonic())
        atomic_json(self.path / 'progress.json', dict(stage=stage, **detail))

    def count(self, **delta):
        if 'preview_version' in self.sem:
            for key, value in delta.items():
                self.counts[key] = self.counts.get(key, 0) + value
            atomic_json(self.session / 'io-counts.json', self.counts)
            return
        if self.sem.get('adaptive_application') is True:
            from ..adaptive_execution.runtime import host_count
            return host_count(self,delta)
        if self.sem.get('cpu_refine') is True:
            from ..cpu_refine.science import host_count
            return host_count(self,delta)
        if self.sem.get('instance_context'):
            from ..cpu_first_fit.science import host_count
            return host_count(self, delta)
        limits = dict(source_decode=1, source_copy=1, R0_render=1, forward=140,
                      CPU256_service=1 if self.sem['mode'] == 'fine' else 0,
                      qualification_quantization=6 if self.sem['mode'] == 'fine' else 3,
                      preview=5 if self.sem['mode'] == 'fine' else 3,
                      preview_JPG=5 if self.sem['mode'] == 'fine' else 3,
                      native_JPG_encode=2 if self.sem['mode'] == 'fine' else 1,
                      native_JPG_full_readback=2 if self.sem['mode'] == 'fine' else 1)
        for key, value in delta.items():
            if not self.sem.get('TEST_ONLY') and key in limits:
                need(self.counts[key]+value <= limits[key], 'NATIVE_IO_OR_SCIENCE_CAP')
            self.counts[key] += value
        atomic_json(self.session / 'io-counts.json', self.counts)

    def allocate(self, shape):
        import numpy as np
        from multiprocessing import shared_memory
        self.smap = shared_memory.SharedMemory(create=True, size=math.prod(shape))
        self.omap = shared_memory.SharedMemory(create=True, size=math.prod(shape)*4)
        self.s = np.ndarray(shape, np.uint8, buffer=self.smap.buf)
        self.o = np.ndarray(shape, np.float32, buffer=self.omap.buf)
        atomic_json(self.session / 'shared-mappings.json', dict(
            S=dict(name=self.smap.name, bytes=self.s.nbytes), O=dict(name=self.omap.name, bytes=self.o.nbytes),
            full_O_allocations=1, shape=shape, owner=current_identity()))

    def prepare(self):
        if 'preview_version' in self.sem:
            from ..preview.host import prepare
            return prepare(self)
        if self.context and self.context.get('schema')=='cpu-application-execution-v5' and self.context['TEST_ONLY']:
            from ..adaptive_execution.frame import prepare
            return prepare(self)
        if self.context and self.context.get('schema')=='cpu-application-execution-v4':
            from ..adaptive_execution.first_host import prepare
            return prepare(self)
        if self.context and self.context.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):
            from ..adaptive_execution.native_host import prepare
            return prepare(self)
        if self.sem.get('adaptive_application') is True:
            from ..adaptive_execution.frame import prepare
            return prepare(self)
        if self.sem.get('cpu_refine') is True:
            from ..cpu_refine.host import prepare
            return prepare(self)
        if self.sem.get('instance_context'):
            from ..cpu_first_fit.host import prepare
            return prepare(self)
        import numpy as np
        from ..engine import group15_codec as codec
        self.check()
        test = self.sem.get('TEST_ONLY') is True
        need(not test or self.context is not None, 'TEST_ONLY_FACTORY_REQUIRED')
        shape = [33, 47, 3] if test else authority()['source_shape']
        self.allocate(shape)
        if test:
            self.s[:] = (np.arange(math.prod(shape)).reshape(shape) % 251).astype(np.uint8)
            self.o[:] = self.s.astype(np.float32) / np.float32(255) + np.float32(.03125)
            source_info = dict(pixel_sha256=array_hash(self.s), icc_bytes=None)
            if self.sem.get('cancel_at') == 'r0_tile':
                self.progress('R0', completed_tiles=1, TEST_ONLY=True)
                atomic_json(self.path / 'cancel.signal', dict(TEST_ONLY=True, at='r0_tile'))
                self.check()
        else:
            self.progress('read_source')
            self.count(source_decode=1)
            original, source_info = codec.read_source(self.sem['source_path'], strict_group15=False, development_pair=True)
            need(list(original.shape) == shape and original.dtype == np.uint8, 'SOURCE_DECODE_SCHEMA')
            self.s[:] = original
            self.count(source_copy=1)
            self.icc = source_info['icc_bytes']
            del original
            gc.collect()
        self.s.flags.writeable = False
        r0 = None
        if not test:
            self.progress('R0')
            work = self.session / 'R0'
            work.mkdir()
            payload = dict(S=dict(name=self.smap.name, shape=shape, dtype='uint8', readonly=True),
                           O=dict(name=self.omap.name, shape=shape, dtype='float32'),
                           bundle=self.sem['bundle_directory'], cancel=str(self.path / 'cancel.signal'),
                           operation=str(self.path), work=str(work),
                           reservation=dict(R0_render=1, forward=140, model=1, state_load=1))
            atomic_json(work / 'payload.json', payload)
            atomic_json(work / 'scientific-reservation.json', payload['reservation'])
            job, _ = runtime.launch('r0', work / 'payload.json', work)
            try:
                waited = runtime.wait(job, work, timeout=1200)
                need(waited['exit_code'] == 0, 'R0_WORKER_FAILED')
                r0 = strict_load(work / 'r0-result.json')
                self.count(R0_render=r0['counts']['render'], forward=r0['counts']['forward_batches'])
            finally:
                if job.poll() is None:
                    job.terminate()
                    runtime.wait(job, work, timeout=30)
                job.close()
        self.o.flags.writeable = False
        hashes = dict(O=array_hash(self.o))
        if self.sem['mode'] == 'fine':
            hashes.update(self.postprocess(test))
            self.o = None
            self.omap.close()
            self.omap.unlink()
            self.omap = None
            with self.receiver.prepared_view() as view:
                self.y = view.view()
            del view
        else:
            # S plus one O/Y mapping: there is no second full fast result.
            self.y = self.o
        return self.finish_prepare(source_info, r0, hashes, test)

    def finish_prepare(self, source_info, r0, hashes, test, *, phase_clock=None):
        self.check()
        self.binding = dict(backend_id=BACKEND, handle_id=str(uuid.uuid4()), generation=1,
                            owner_instance_id=self.spec['session_id'], project_revision=self.sem['math_id'],
                            recipe_sha256=self.sem['math_id'], job_id=self.spec['job_id'], attempt_id=self.spec['attempt_id'],
                            bundle_id=self.sem['bundle_id'], math_id=self.sem['math_id'], executor_build=self.sem['executor_build'],
                            pool_token=self.spec['pool_token'], owner=strict_load(self.session / 'owner.json'), host=current_identity())
        lease = dict(state='PREPARED', binding=self.binding, handle_id=self.binding['handle_id'],
                     generation=1, owner_instance_id=self.spec['session_id'], bytes_reserved=self.s.nbytes+self.y.nbytes,
                     expires_at=time.time()+(self.context['lease_seconds'] if self.context else 7200), capabilities=['preview', 'export', 'release'])
        self.lease(lease)
        result = dict(status='COMPLETE', backend_id=BACKEND, mode=self.sem['mode'], hashes=hashes,
                      R0=r0, source_pixel_sha256=source_info['pixel_sha256'], counts=self.counts,
                      SharedO_direct=True, fast_O_Y_same_mapping=self.sem['mode'] == 'fast',
                      full_float_blend_arrays=0, reference_decodes=self.counts.get('reference_decode', 0), TEST_ONLY=test)
        atomic_json(self.path / 'compute-result.json', result)
        atomic_json(self.path / 'compute-cleanup.json', dict(workers_actual_wait=True, worker_trees_empty=True,
                    R0_wait=None if (test and (not self.sem.get('instance_context') or r0 is None)) else strict_load(self.session / 'R0/owned-wait.json'),
                    CPU_wait=None if self.receiver is None else strict_load(self.session / 'CPU/owned-wait.json')))
        if self.sem.get('instance_context'):
            cleanup = strict_load(self.path / 'compute-cleanup.json')
            if self.sem.get('first_fit') and self.context['phase'] != 'frame':
                cleanup['fit_wait'] = strict_load(self.session / 'fit/owned-wait.json')
            from ..cpu_first_fit.publication import validate_worker_waits
            validate_worker_waits(cleanup)
            atomic_json(self.path / 'compute-cleanup.json', cleanup)
        if self.sem.get('cpu_refine') is True and self.sem.get('refine_prepare') and not test:
            from ..cpu_refine.publication import validate_prepare_waits
            cleanup=strict_load(self.path/'compute-cleanup.json')
            cleanup.update(P3_wait=strict_load(self.session/'P3/owned-wait.json'),
                           solver_wait=strict_load(self.session/'P3/solver/solver-cleanup.json'))
            validate_prepare_waits(cleanup)
            atomic_json(self.path/'compute-cleanup.json',cleanup)
        atomic_json(self.path / 'replay-identity.json', {k: self.sem[k] for k in ('backend_id', 'bundle_id', 'math_id', 'executor_build', 'source_sha256')})
        if self.context and self.context.get('schema')=='cpu-application-execution-v4':
            from ..adaptive_execution.first_science import stage_complete
            stage_complete(self.context)
            from ..adaptive_execution.first_result import publish as publish_result_authority
            publish_result_authority(self)
        if phase_clock is not None:
            from ..light.eta import save_phase_sidecar
            save_phase_sidecar(self.session / "light-host-times.json",phase_clock.completed())
        atomic_json(self.session / 'prepared.json', dict(lease=lease, binding=self.binding))
        self.progress('resident_prepared')
        self.compute_complete = True
        if self.context:
            self.deadline -= self.context['prepared_clock_shift']
            atomic_json(self.session / 'clock-proof.json', dict(compute_deadline=self.deadline,
                        now_monotonic=time.monotonic(), lease_expires=lease['expires_at'],
                        prepared_clock_shift=self.context['prepared_clock_shift']))

    def postprocess(self, test):
        from ..engine.cpu_postprocess import EXECUTOR_ID
        from ..engine.cpu_postprocess.contract import CONSTANTS,runtime_torch
        from ..engine.cpu_postprocess.receiver import ReceiverStore, ResultReceiver, RECEIVER_ID
        from ..engine.cpu_postprocess.transport import _bounded
        from ..engine.cpu_fine_image.contract import service_budget
        from .. import winproc as win
        if self.context and self.context.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):
            from ..adaptive_execution.native_host import postprocess_metadata
            bmeta,ranges,reference_wh=postprocess_metadata(self)
        else:
            bmeta = (None if self.context and self.context.get("light_plan") else
                     dict(shape=[4, 3], dtype='float64', sha256='0'*64) if test else authority()['b'])
            ranges = [] if test else strict_load(Path(self.sem['bundle_directory']) / 'manifest.json')['mathematical']['p3']['ranges']
            reference_wh=[8,8] if test else [1024,1536]
        if self.sem.get('cpu_refine') and not test:
            from ..cpu_recipe.generated_fine import complete
            recipe,_=complete(self.sem['bundle_directory']);p3=recipe['mathematical']['p3']
            bmeta=dict(shape=p3['b']['members']['b']['shape'],dtype='float64',sha256=p3['b']['value_sha256'])
            reference_wh=p3['reference_wh']
        sem = dict(executor_id=EXECUTOR_ID, receiver_id=RECEIVER_ID,
                   binding=dict(O=dict(shape=list(self.o.shape))), manifest_sha256=digest(self.sem))
        from ..adaptive_execution.portable_contract import receiver_directory
        location=receiver_directory(self.session)
        store = (ReceiverStore(location.parent,namespace=location.name)
                 if location!=self.session/'receiver-store' else ReceiverStore(location))
        path, spec, state = store.begin('replay', sem, 'cpu-' + self.spec['attempt_id'])
        store.transition(spec['job_id'], spec['attempt_id'], 'RUNNING', 'CPU', 'PORTABLE_CPU_STARTED')
        work = self.session / 'CPU'
        work.mkdir()
        m = dict(executor_id=EXECUTOR_ID, job_id=spec['job_id'], attempt_id=spec['attempt_id'], owner=current_identity(),
                 source=dict(sha256=self.sem['source_sha256']), recipe=dict(sha256=self.sem['math_id']),
                 O=dict(shape=list(self.o.shape), dtype='float32', sha256=array_hash(self.o)), b=bmeta,
                 reference_wh=reference_wh, constants=CONSTANTS,
                 runtime=dict(torch=runtime_torch(), numpy='1.26.4', threads=4, interop=1), backend='cpu',
                 core_rows=256, budget=dict(TEST_ONLY=0) if test else service_budget(list(self.o.shape), ranges, 256),
                 deadline_monotonic=time.monotonic()+(180 if test else 3600), output_root=str(work))
        listener = Listener(('127.0.0.1', 0), authkey=(auth := secrets.token_bytes(32)))
        payload = dict(manifest=m, O_shared_memory=self.omap.name,
                       b_path=str(Path(self.sem['bundle_directory']) / 'p3/b.npz'),
                       address=list(listener.address), auth_hex=auth.hex(), ranges=ranges)
        if test:
            payload.update(TEST_ONLY=True, partial=self.sem.get('partial', False))
        atomic_json(work / 'payload.json', payload)
        atomic_json(work / 'exact-prepayment.json', dict(before_LLF=True, budget=m['budget']))
        if not test:
            self.count(CPU256_service=1)
        receiver = self.receiver = ResultReceiver(m, store, path, spec)
        job = conn = actual_handle = None
        native_early_identity = None
        from ..adaptive_execution import c2_cpu_tail
        protected_tail = not test and c2_cpu_tail.enabled(self.context)
        primary = who = None
        tail_waited = False
        fields = []
        try:
            job, launcher = runtime.launch('frame_sender' if test else 'cpu', work / 'payload.json', work)
            if self.context and self.context.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5') and not test:
                from ..adaptive_execution.native_host import retain_cpu_before_ready
                actual_handle,native_early_identity=retain_cpu_before_ready(job,work)
                who=native_early_identity
            deadline = m['deadline_monotonic']
            if self.context and self.context.get('schema')=='cpu-application-execution-v4':deadline+=30
            conn = _bounded(listener.accept, deadline, lambda: (self.path / 'cancel.signal').exists(), work)
            hello = _bounded(lambda: recv_json(conn), deadline, lambda: False, work, conn)
            need(set(hello) == {'event', 'identity', 'cuda_initialized'} and hello['event'] == 'ready'
                 and hello['cuda_initialized'] is False and job.member(hello['identity']['pid']), 'CPU_SENDER_IDENTITY')
            who = hello['identity']
            if native_early_identity is not None:
                need(win.identity_key(native_early_identity)==win.identity_key(who) and win.identity_key(win.process_identity(who['pid']))==win.identity_key(who),'CPU_READY_RETAINED_IDENTITY')
            else:
                actual_handle = win.k.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION | win.SYNCHRONIZE, False, who['pid'])
                need(actual_handle and win.identity_key(win.process_identity(who['pid'])) == win.identity_key(who), 'CPU_ACTUAL_HANDLE')
            def proof(key, start, end, value):
                fields.append(dict(key=key, start=start, end=end, sha256=array_hash(value)))
            if self.context and self.context.get("light_plan"):
                self.progress("CPU",done=0,total=math.ceil(self.o.shape[0]/256))
            for _ in range(math.ceil(self.o.shape[0]/256)):
                if test and self.sem.get('cancel_at') == 'CPU_ACK':
                    header, reply = receiver.receive_block(conn, deadline=deadline, ack=False, proof=proof)
                    atomic_json(work / 'cancel-four-fields.json', dict(fields=fields, before_ACK=True))
                    atomic_json(self.path / 'cancel.signal', dict(TEST_ONLY=True, before_ACK=True))
                    raise InterruptedError('TEST_CPU_CANCEL_BEFORE_ACK')
                receiver.receive_block(conn, deadline=deadline, cancelled=lambda: (self.path / 'cancel.signal').exists(), proof=proof)
                if self.context and (self.context.get("light_plan") or self.context.get('schema') in
                                     ('cpu-application-execution-v4','cpu-application-execution-v5')):
                    self.progress('CPU',done=math.ceil(receiver.coverage.next_row/256),total=math.ceil(self.o.shape[0]/256))
            hashes = receiver.receive_terminal(conn, deadline=deadline)
            if protected_tail:
                waited = c2_cpu_tail.finish(job, actual_handle, native_early_identity, work, time.monotonic(), deadline)
                tail_waited = True
            else:
                waited = runtime.wait(job, work, timeout=30)
                need(waited['exit_code'] == 0 and win.k.WaitForSingleObject(actual_handle, 30000) == win.WAIT_OBJECT_0, 'CPU_ACTUAL_WAIT')
                waited.update(actual_worker_handle_wait=True, worker_identity=who)
                if self.context and self.context.get("light_plan"):
                    waited["actual_worker_identity"]=who
            atomic_json(work / 'owned-wait.json', waited)
            receiver.prepare(waited, who)
            atomic_json(work / 'fields-ACK.json', dict(fields=fields, cores=math.ceil(self.o.shape[0]/256),
                        four_fields_per_ACK=True, complete=True, blocks=receiver.coverage.next_row))
            if self.context and self.context.get('schema')=='cpu-application-execution-v4':
                from ..adaptive_execution.first_science import complete as component_complete
                from .contract import binding
                component_complete(self.context,'CPU',binding(work/'fields-ACK.json'))
            return hashes
        except BaseException as error:
            primary = error
            raise
        finally:
            if protected_tail:
                c2_cpu_tail.cleanup(job, actual_handle, native_early_identity, work, conn, listener, primary, tail_waited)
            else:
                if conn:
                    conn.close()
                listener.close()
                if job:
                    if job.poll() is None:
                        job.terminate()
                        runtime.wait(job, work, timeout=30)
                    job.close()
                if actual_handle:
                    actual_waited = win.k.WaitForSingleObject(actual_handle, 30000) == win.WAIT_OBJECT_0
                    atomic_json(work / 'actual-worker-wait.json', dict(identity=who, actual_handle_wait=actual_waited))
                    need(actual_waited, 'CPU_ACTUAL_WAIT_ON_FAILURE')
                    win.close(actual_handle)

    def lease(self, value):
        from ..adaptive_execution.residency import versioned,write
        if versioned(self.context):
            return write(self,value)
        atomic_json(self.session / 'lease.json', value)

    def require_binding(self, request, allow_prepared=False):
        current = strict_load(self.session / 'lease.json')
        states = {'PREPARED', 'RESIDENT'} if allow_prepared else {'RESIDENT'}
        need(current['state'] in states and request['binding'] == self.binding == current['binding'], 'RESULT_NOT_RESIDENT', exit_code=4)
        if getattr(self,'_r4_row_stats',None) is not None:
            from ..adaptive_execution.residency import stopped
            stop=stopped(self.session,self.spec,expected_binding=self.binding)
        else:
            stop=life.stopping(self.session,self.spec)
        need(time.time() < current['expires_at'] and not stop,'RESULT_LEASE_EXPIRED',exit_code=4)
        need(identity_alive(self.binding['owner']) is True, 'RESULT_OWNER_UNKNOWN')
        if not self.sem.get('TEST_ONLY'):
            actual = self.context['source'] if self.sem.get('instance_context') else authority()['source']
            need(Path(self.sem['source_path']).is_file() and Path(self.sem['source_path']).stat().st_size == actual['bytes'], 'SOURCE_CHANGED')

    def _qualify_row(self, alpha, bits):
        mix_quantize=getattr(self,'_fixture_quantize',None)
        if mix_quantize is None:
            from ..export_pixels import mix_quantize
        stats = {}
        self.count(qualification_quantization=1)
        timer=getattr(self,'_r4_row_stats',None)
        start=time.monotonic() if timer is not None else None
        try:
            q = mix_quantize(self.s, self.y, alpha, bits=bits, check=self.check, stats=stats)
        finally:
            if timer is not None:
                timer.update(mix_including_checks_seconds=time.monotonic()-start,
                             completed_blocks=stats.get('blocks',0),completed_rows=stats.get('rows',0))
        start=time.monotonic() if timer is not None else None
        sha=array_hash(q)
        if timer is not None:
            timer['output_hash_seconds']=time.monotonic()-start
        return dict(alpha=alpha,bits=bits,sha256=sha,stats=stats)

    def qualify(self):
        rows = [self._qualify_row(alpha, bits) for alpha in (0., .5, 1.)
                for bits in ((8, 16) if self.sem['mode'] == 'fine' else (8,))]
        atomic_json(self.session / 'quantization.json', dict(rows=rows))
        return dict(rows=rows)

    def qualify_part(self, request, *, qualified=False):
        from ..adaptive_execution.portable_contract import FINE_QUALIFICATION_ROWS
        fields={'command','binding','index'}
        need(set(request)==(fields|{'qualifier'} if qualified else fields),'CPU_QUALIFY_PART_FIELDS')
        self.require_binding(request)
        index = request['index']
        rows = self._qualification_rows
        need(type(index) is int and index == len(rows) < len(FINE_QUALIFICATION_ROWS)
             and not self._qualification_failed, 'CPU_QUALIFY_PART_ORDER')
        self._qualification_failed = True
        if index == 0:
            self._qualification_deadline = self.request_deadline
            if qualified:
                from ..adaptive_execution.portable_qualification import save
                save(self.session/'host','qualification-deadline',
                     dict(monotonic=self._qualification_deadline-self.context['request_seconds'],
                          wall_observed=time.time(),deadline=self._qualification_deadline,
                          identity=current_identity()))
        self.request_deadline = min(self.request_deadline, self._qualification_deadline)
        self.check()
        started = time.monotonic()
        row = self._qualify_row(*FINE_QUALIFICATION_ROWS[index])
        finished = time.monotonic()
        self.check()
        rows.append(row)
        if len(rows) == len(FINE_QUALIFICATION_ROWS):
            atomic_json(self.session / 'quantization.json', dict(rows=rows))
        self._qualification_failed = False
        return dict(row=row, timing=dict(index=index, started=started, finished=finished,
                    host_deadline=self._qualification_deadline))

    def display_reference(self):
        from ..engine.group15_codec import read_reference
        from .contract import verify
        entry=self.context.get("display_reference")
        need(entry is not None, "LIGHT_REFERENCE_UNAVAILABLE")
        record=entry["file"]
        if getattr(self,"_display_reference",None) is None:
            verify(record)
            self.check()
            pixels,info=read_reference(Path(record["path"]),strict_group15=False,development_pair=True)
            h=entry["header"]
            need(list(pixels.shape)==[h["height"],h["width"],3] and pixels.dtype.itemsize*8==h["bits"]
                 and info["sha256"]==record["sha256"],"LIGHT_REFERENCE_CHANGED")
            verify(record)
            pixels.flags.writeable=False
            self._display_reference=(pixels,info)
        return self._display_reference

    def preview(self, request):
        from ..gm004a_preview import get_preview
        from ..engine import group15_codec as codec
        from ..lease import BINDING_FIELDS
        self.require_binding(request)
        self.check()
        legacy = {key: self.binding[key] for key in BINDING_FIELDS}
        self.count(preview=1, mix_traversals=1)
        light=bool(self.context and self.context.get("light_plan"))
        from ..light.eta import emit
        report=(lambda stage,done=None,total=None:emit(self.session/"light-preview-progress.json",stage,done,total)) if light else None
        if report and request['view']=="reference":
            report("preview_reference")
        reference,info=self.display_reference() if light and request['view']=="reference" else (None,None)
        pixels, meta = get_preview(self.s, self.y, reference, info["icc_bytes"] if info else self.icc,
                                   legacy, view=request['view'], alpha=request['alpha'],
                                   max_side=request['max_side'], roi=request['roi'], request_id=request['request_id'],
                                   color_interpretation=info["color"]["interpretation"] if info else None,
                                   reference_roi=light,progress=report,
                                   cancelled=lambda: (self.path / 'cancel.signal').exists(), deadline=time.monotonic()+120)
        dest = output(request['destination'])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if light:
            report("preview_transfer")
            from ..common import sha_file
            need(pixels.nbytes <= 3*4096**2, "LIGHT_PREVIEW_FILE_CAP")
            with dest.open("xb") as stream:
                stream.write(memoryview(pixels).cast("B"))
            meta['binding'] = self.binding
            return dict(preview=meta,file=dict(path=str(dest),bytes=pixels.nbytes,sha256=sha_file(dest)),
                        fit_count=0,render_count=self.counts['R0_render'])
        self.count(preview_JPG=1)
        file = codec.write_jpg(dest, pixels, self.icc)
        need(file['bytes'] <= 2*1024**2, 'PREVIEW_FILE_CAP')
        meta['binding'] = self.binding
        return dict(preview=meta, file=file, fit_count=0, render_count=self.counts['R0_render'])

    def export(self, request):
        if 'preview_version' in self.sem:
            from ..preview.host import export
            return export(self, request)
        from ..export_pixels import mix_quantize
        from ..engine import group15_codec as codec
        from ..gm004a_export import publish_export
        from PIL import Image
        self.require_binding(request)
        key = request['request_key']
        need(type(key) is str and 1 <= len(key) <= 128, 'EXPORT_KEY_REQUIRED')
        from ..adaptive_execution.portable_contract import enabled as portable_enabled, export_transaction
        intent_path = export_transaction(self.session, hashlib.sha256(key.encode('utf-8')).hexdigest()) / 'intent.json'
        old = strict_load(intent_path) if intent_path.is_file() else None
        quantized = None
        self.progress('export')
        if old is not None:
            expected = old['request']['expected_pixels_sha256']
        else:
            self.count(mix_traversals=1)
            quantized = mix_quantize(self.s, self.y, request['alpha'], bits=8, check=self.check)
            expected = array_hash(quantized)
        export = dict(request_key=key, alpha=request['alpha'], format='jpeg', bit_depth=8,
                      icc_policy='preserve_source' if self.icc is not None else 'none',
                      icc_sha256=hashlib.sha256(self.icc).hexdigest() if self.icc else None,
                      source_path=self.sem['source_path'], source_sha256=self.sem['source_sha256'],
                      recipe_sha256=self.sem['math_id'], lease_id=self.binding['handle_id'], lease_generation=1,
                      width=self.s.shape[1], height=self.s.shape[0], expected_pixels_sha256=expected,
                      executor_build=self.sem['executor_build'])
        from ..export_metadata import policy_request
        export.update(policy_request(export["source_path"], export["source_sha256"]))
        def writer(temp, req):
            self.check()
            self.count(native_JPG_encode=1)
            codec.write_jpg(temp, quantized, self.icc)
            need(temp.stat().st_size <= 96*1024**2, 'NATIVE_JPG_FILE_CAP')
        def validator(temp, req):
            self.check()
            # Drop the integer target before the full lossy readback.
            nonlocal quantized
            del quantized
            quantized = None
            gc.collect()
            self.count(native_JPG_full_readback=1)
            with Image.open(temp, formats=["JPEG"]) as image:
                need(image.info.get('icc_profile') == self.icc and image.mode == 'RGB' and
                     image.size == (self.s.shape[1], self.s.shape[0]), 'EXPORT_READBACK_SCHEMA')
                image.load()
                decoded = hashlib.sha256(image.tobytes()).hexdigest()
            readback=(intent_path.parent/'full-readback.json' if portable_enabled(self.context)
                      else self.session/('readback-'+digest(key)+'.json'))
            atomic_json(readback, dict(full_decode=True, pixel_sha256=decoded,
                        preencode_Q8_sha256=expected, lossy=True, readback_count=1))
            return dict(alpha=req['alpha'], format='jpeg', bit_depth=8, icc_sha256=req['icc_sha256'],
                        source_sha256=req['source_sha256'], recipe_sha256=req['recipe_sha256'], lease_id=req['lease_id'],
                        lease_generation=1, width=req['width'], height=req['height'], pixels_sha256=expected)
        dest = output(request['destination'])
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = publish_export(self.session, dest, export, writer, validator, metadata_check=self.check, metadata_progress=self.progress)
        return dict(export=result, counts=self.counts)

    def cleanup(self):
        self.y = self.o = self.s = self._display_reference = None
        gc.collect()
        if self.receiver:
            self.receiver.release()
            self.receiver = None
        for mapping in (self.smap, self.omap):
            if mapping:
                mapping.close()
                mapping.unlink()
        self.smap = self.omap = None
        if self.binding and not self.sem.get('instance_context'):
            current = strict_load(self.session / 'lease.json')
            self.lease(dict(current, state='RELEASED'))
        from ..adaptive_execution.calibration_contract import measurement
        if measurement(self.context):
            from ..adaptive_execution.calibration_observation import stage
            stage(self.session,'host_cleanup_complete',owned_mappings_closed=True)


def serve(host, listener):
    from ..engine.cpu_postprocess.transport import _bounded
    outcome = 'EXITED'
    session = host.session
    done = False
    host._r4_consumer=None
    host._r4_row_stats=None

    def stopping():
        if host._r4_consumer is not None and host._r4_consumer.pins.valid():
            from ..adaptive_execution.residency import stopped
            return stopped(session,host.spec,expected_binding=host.binding)
        return life.stopping(session,host.spec)

    while not done:
        lease = strict_load(session / 'lease.json')
        idle_deadline = time.monotonic()+max(.001, lease['expires_at']-time.time())
        try:
            conn = _bounded(listener.accept, idle_deadline, lambda: bool(stopping()), session)
        except (InterruptedError, TimeoutError):
            if stopping():
                outcome = 'STOPPED_OR_EXPIRED'
                break
            raise
        with conn:
            # Bound framing/authentication separately from compute and TTL.
            host.request_deadline = time.monotonic()+(host.context['request_seconds'] if host.context else 120)
            request = _bounded(lambda: recv_json(conn), host.request_deadline,
                               lambda: bool(stopping()), session, conn)
            command = request['command']
            if 'preview_version' in host.sem:
                need(command in ('ACTIVATE', 'PREVIEW', 'EXPORT', 'RELEASE')
                     or command in ('KEEP_ALIVE', 'QUALIFY') and host.context.get('light_plan'),
                     'PREVIEW_HOST_COMMAND')
            received=time.monotonic()
            from ..adaptive_execution.residency import versioned
            if command == 'EXPORT' and (not versioned(host.context) or host.context.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5')):
                host.request_deadline = time.monotonic()+1200
            trace=None
            authorized=False
            try:
                if command == 'QUALIFY' and 'preview_version' in host.sem:
                    from ..adaptive_execution.portable_renewal import peer_pid
                    from ..light.runtime import operation
                    plan = operation()
                    client = host.context['client']
                    need(plan and plan.get('validation_evidence') is True
                         and plan['plan_id'] == host.context['light_plan']
                         and plan['client'] == client and host.context.get('TEST_ONLY') is False
                         and set(request) == {'command', 'binding'}, 'LIGHT_VALIDATION_EVIDENCE_REQUIRED')
                    peer = peer_pid(conn)
                    need(peer == client['pid'] and process_identity(peer) == client,
                         'LIGHT_VALIDATION_PEER_CHANGED')
                if command=='QUALIFY_OPEN':
                    from ..adaptive_execution.portable_qualification import HostConsumer
                    need(host._r4_consumer is None and not host._qualification_rows
                         and not host._qualification_failed,'GM064_R4_ALREADY_OPEN')
                    from ..adaptive_execution.portable_renewal import peer_pid
                    host._r4_consumer=HostConsumer(host,request,peer_pid(conn))
                    send_json(conn,dict(consumer_open=True))
                    continue
                qualifier='qualifier' in request
                if qualifier:
                    from ..adaptive_execution.portable_renewal import peer_pid
                    need(host._r4_consumer is not None,'GM064_R4_CONSUMER_REQUIRED')
                    rid=host._r4_consumer.authorize(host,request,peer_pid(conn))
                    authorized=True
                    trace=dict(index=host._r4_consumer.index,request_id=rid,operation=str(host.path),
                               command=command,host_received=received,wall=time.time(),
                               identity=current_identity(),request_deadline=host.request_deadline,
                               repeated_full_input_checks=0)
                elif command=='QUALIFY_PART':
                    need(host._r4_consumer is None,'GM064_R4_LEGACY_PART_REFUSED')
                pinned=qualifier or (getattr(host,'renewal_proof',None)
                        and (command=='ACTIVATE' or command=='KEEP_ALIVE' and 'handoff' in request))
                if host.context and host.context.get('schema')=='cpu-application-execution-v4':
                    from ..adaptive_execution.first_residency import active_context,verify_inputs
                    if not qualifier:
                        if pinned:
                            need(active_context(host.spec)==host.context,'GM064_PINNED_HOST_CONTEXT')
                        else:
                            verify_inputs(host.spec)
                if command == 'ACTIVATE':
                    host.require_binding(request, allow_prepared=True)
                    current = strict_load(session / 'lease.json')
                    host.lease(dict(current, state='RESIDENT'))
                    if host.receiver:
                        host.receiver.activate()
                    reply = dict(binding=host.binding, resident=True)
                elif command == 'KEEP_ALIVE':
                    from ..adaptive_execution.residency import renew
                    if host.context and host.context.get("light_plan"):
                        from ..adaptive_execution.portable_renewal import peer_pid
                        from ..light.residency import renew as light_renew
                        reply=light_renew(host,request,peer_pid(conn))
                    elif qualifier:
                        if host._qualification_deadline is not None:
                            host.request_deadline=min(host.request_deadline,host._qualification_deadline)
                        reply=renew(host,request,qualifier_id=rid)
                        host._r4_consumer.renewed=rid
                        host._r4_consumer.used.add(rid)
                        trace['lease_confirmation']={key:reply[key]
                            for key in ('confirmed_at','old_expires_at','expires_at')}
                    elif 'handoff' in request:
                        from ..adaptive_execution.portable_renewal import peer_pid
                        reply = renew(host,request,peer_pid=peer_pid(conn))
                    else:
                        reply = renew(host,request)
                elif command == 'RELEASE':
                    need(request['binding'] == host.binding, 'CPU_RESULT_BINDING_STALE')
                    host.progress('release')
                    if host._r4_consumer is not None:
                        host._r4_consumer.close()
                    host.cleanup()
                    reply = dict(released=True)
                    done = True
                elif command in ('QUALIFY', 'QUALIFY_PART'):
                    from ..adaptive_execution.portable_contract import segmented_fine_qualification
                    segmented = segmented_fine_qualification(host.context)
                    need(segmented == (command == 'QUALIFY_PART'), 'CPU_QUALIFY_COMMAND_SCOPE')
                    if segmented:
                        if qualifier:
                            host._r4_row_stats=dict(checks=0,pressure_seconds=0.,authority_and_cancel_seconds=0.,
                                                    lease_seconds=0.,deadline_seconds=0.)
                            trace['blocks']=host._r4_row_stats
                        reply = host.qualify_part(request,qualified=qualifier)
                        if qualifier:
                            host._r4_consumer.index+=1
                            host._r4_consumer.renewed=None
                            trace['sha256']=reply['row']['sha256']
                    else:
                        host.require_binding(request)
                        reply = host.qualify()
                elif command == 'PREVIEW':
                    reply = host.preview(request)
                elif command == 'EXPORT':
                    reply = host.export(request)
                else:
                    raise ValueError('CPU_HOST_COMMAND')
                if host.context and host.context.get('schema')=='cpu-application-execution-v4' and not pinned:
                    verify_inputs(host.spec)
                if trace is not None:
                    trace['host_reply_started']=time.monotonic()
                if command == 'QUALIFY_PART':
                    _bounded(lambda: send_json(conn, reply), host.request_deadline,
                             lambda: bool(stopping()), session, conn)
                else:
                    send_json(conn, reply)
            except BaseException as exc:
                if authorized:
                    host._r4_consumer.poisoned=True
                if trace is not None:
                    trace.update(error=getattr(exc,'code',type(exc).__name__),message=str(exc))
                runtime.error(session / 'host', exc)
                if isinstance(exc, InterruptedError) and str(exc)=="CANCELLED":
                    from ..common import GMError
                    exc=GMError("CANCELLED","Export cancelled",8)
                try:
                    send_json(conn, dict(error=getattr(exc, 'code', type(exc).__name__), message=str(exc), exit_code=getattr(exc, 'exit_code', 7)))
                except OSError:
                    if (command != 'QUALIFY_PART' or host._qualification_deadline is None
                            or time.monotonic() < host._qualification_deadline):
                        raise
            finally:
                if trace is not None:
                    from ..adaptive_execution.portable_qualification import save
                    trace.update(host_finished=time.monotonic(),effective_deadline=host.request_deadline,
                                 batch_deadline=host._qualification_deadline)
                    save(session/'host',f"qual-{trace['index']}-{command.lower()}",trace)
                host._r4_row_stats=None
                host.request_deadline = None
    return outcome


def main(session):
    session = Path(session)
    runtime.enter('host', session / 'host')
    atomic_json(session / 'host-entry.json', current_identity())
    until = time.monotonic()+30
    while not (session / 'host-retained.json').is_file():
        need(time.monotonic() < until, 'OWNER_ACTUAL_HANDLE_ACK')
        time.sleep(.01)
    from ..light.resources import stop_before_decode
    stop_before_decode(session,"cpu_host")
    host = Host(session)
    listener = None
    outcome = 'EXITED'
    host.renewal_proof=None
    try:
        from ..adaptive_execution.portable_contract import enabled as portable_enabled
        if portable_enabled(host.context):
            from ..adaptive_execution.portable_renewal import prepare_host
            host.renewal_proof=prepare_host(host)
        elif host.context and host.context.get("light_plan"):
            from ..adaptive_execution.portable_renewal import prepare_peer_table
            prepare_peer_table()
        life.pause(session, host.spec, 'before_ready')
        listener = Listener(('127.0.0.1', 0), authkey=bytes.fromhex(host.spec['auth_hex']))
        host.spec['pipe_address'] = list(listener.address)
        atomic_json(session / 'session-spec.json', host.spec)
        atomic_json(session / 'host-ready.json', dict(identity=current_identity(), address=list(listener.address)))
        while not (session / 'compute-go.json').is_file():
            host.check()
            time.sleep(.02)
        life.pause(session, host.spec, 'before_compute')
        host.prepare()
        outcome = serve(host, listener)
        runtime.observation(session / 'host', 'host', outcome)
        return 0
    except BaseException as exc:
        outcome = repr(exc)
        life.diagnostic(session / 'host', exc)
        raise
    finally:
        if listener:
            listener.close()
        try:
            host.cleanup()
        finally:
            if getattr(host,'_r4_consumer',None):
                host._r4_consumer.close()
            if host.renewal_proof:
                host.renewal_proof[0].close()
        atomic_json(session / 'host/normal-tail.json', dict(outcome=outcome, cleanup_completed=True))
