"""Full desktop adapter sharing the existing engines and operation journals."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import shutil
import time
import uuid

from ..common import GMError, atomic_json, strict_load
from ..cpu_replay.contract import need
from ..desktop.backend import EngineBackend
from ..store import locked
from . import planner, runtime as rt
from . import resource_retry as retry


class Backend(EngineBackend):
    REFERENCE_ROI = True
    COLD_REFINEMENT = True

    def __init__(self,preference=None,*,validation=None):
        self.runtime_problem=None
        self._preference=preference
        self.validation=validation
        self._operation_preferences={}
        self._retry_sources={}
        self.last_plan=None

    def choose_cpu_once(self,key):
        self._operation_preferences[key]="cpu"

    def clear_choice(self,key):
        self._operation_preferences.pop(key,None)
        self._retry_sources.pop(key,None)

    def choose_retry(self,key,path):
        self._retry_sources[key]=path

    @contextmanager
    def _inner_refusal(self,selected,route,store,env):
        try:
            yield
        except GMError as error:
            proof=getattr(error,"inner_resource",None)
            if proof is None:
                if error.code not in ("PREVIEW_MEMORY_NOT_READY","PREVIEW_MEMORY_UNKNOWN","PREVIEW_PROBE_MEMORY_NOT_READY",
                                      "GPU_RESOURCE_UNQUALIFIED","GPU_RAM_REQUIRED","IMAGE_RAM_REQUIRED","LIGHT_DEVICE_CHANGED",
                                      "RESIDENT_EXIT_UNCONFIRMED"):
                    raise
                proof=dict(code=error.code,stage="unknown",sampled_at=None,shortages=[],science="UNKNOWN",cleanup="UNKNOWN")
            if route.get("cpu_segment")=="fine":
                proof["science"]="STARTED"
                proof["completed_stage"]="cpu_fast_parent"
            safe=(proof["science"]=="NOT_STARTED" and proof["cleanup"]=="COMPLETE"
                  and not (store/"light-cancel.json").exists() and self.validation is None)
            path=store/retry.RECEIPT
            receipt=dict(inner_resource=proof,message=str(error),intent=retry.intent(selected),plan_id=route["plan_id"])
            if safe:
                current=planner.plan(selected["source"],selected["reference"],selected["quality"],
                                     selected["operation"],env,selected["producer"],prefer=selected["preference"])
                receipt["routing"]=dict(reason="INNER_RESOURCE_CHANGED",device=current["device_state"],
                    candidates=current["candidates"],resources=current["resources"],can_choose_cpu=current["can_choose_cpu"],
                    project=route["project"],operation=route["operation"],request_key=route["key"],
                    retry=dict(path=str(path),previous_key=route["key"],previous_attempt=proof["attempt_id"]))
            proof.update(backend=route["backend"],request_key=route["key"],operation=selected["operation"])
            atomic_json(path,receipt)
            raise retry.ResourceChoice(receipt) from error

    def device_overview(self):
        import platform
        from .system import manifest
        env=rt.snapshot()
        for name,row in env.items():
            rt.verify_role(row,name)
        return dict(version=rt.VERSION,edition=manifest().get("edition"),cpu=platform.processor(),
                    architecture=platform.machine(),windows_build=__import__("sys").getwindowsversion().build,
                    roles=sorted(env),device=planner.device_status(env),memory=planner.ram_facts(),
                    preference=self.preference(),observed_at=time.time())

    def preference(self):
        if self._preference is not None:
            return self._preference
        p=rt.data_root()/"preview-preference.json"
        return strict_load(p)["backend"] if p.is_file() else "auto"

    def open_project(self,path):
        view=super().open_project(path)
        if view.get("algorithm_retired"):
            return view
        folder=Path(path)
        from .recovery import check
        saved=check(folder)
        if saved and view.get("metadata_only"):
            return dict(view, saved_recovery=True, light_backend="gpu")
        cpu=folder/"grade-cpu.grademirror"
        mixed=folder/"grade-mixed"
        if (cpu/"COMPLETE.json").exists():
            from ..preview.service import recipe
            legacy=(folder/"cpu-compatibility.json").is_file()
            from ..engine.profiles import FINE
            bundle=recipe(cpu,fine=view["profile_id"]==FINE,legacy=legacy)
            need(bundle["mathematical"]["inputs"]["source_identity"]["file_sha256"]==
                 view["source"]["byte_sha256"],"LIGHT_PROJECT_CPU_SOURCE")
            return dict(view,metadata_only=False,light_backend="cpu",light_recipe=str(cpu),read_only=False)
        if (mixed/"execution-manifest.json").exists():
            from ..hybrid_fit.contract import read_manifest
            bundle,_=read_manifest(mixed/"execution-manifest.json")
            need(bundle["source_identity"]["file_sha256"]==view["source"]["byte_sha256"],
                 "LIGHT_PROJECT_MIXED_SOURCE")
            return dict(view,metadata_only=False,light_backend="mixed",
                        light_recipe=str(mixed/"execution-manifest.json"),read_only=False)
        if (folder/"cpu-parent.grademirror/COMPLETE.json").is_file():
            return dict(view,refinement_pending=True,light_backend="cpu")
        return view

    @staticmethod
    def _copy_cpu_bundle(source,target):
        from ..cpu_recipe.generated import complete
        manifest,_=complete(source)
        target.mkdir()
        for name in ("manifest.json","COMPLETE.json",*(a["path"] for a in manifest["assets"])):
            path=target/name
            path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/name,path)
        complete(target)
        return manifest

    def import_legacy_cpu(self,project,recipe):
        from ..preview.contract import package
        from .cpu_compatibility import import_object,contract
        from ..cpu_recipe.generated import complete
        from ..common import writable_path
        folder=writable_path(Path(project))
        view=self.open_project(folder)
        row=rt.snapshot().get("cpu")
        need(row and view["metadata_only"],"LIGHT_LEGACY_CPU_PROJECT_REQUIRED")
        runtime=dict(schema="gm066-cpu-runtime-v1",inventory_sha256=row["lock"],
                     numeric=row["versions"]["torch"],python=row["python"],threads=4)
        source=Path(recipe).resolve(strict=True)
        bundle,_=complete(source)
        m=contract(source,bundle,runtime,package()["build"]["sha256"])
        for k in ("source","reference"):
            need(view[k]["byte_sha256"]==bundle["mathematical"]["inputs"][k+"_identity"]["file_sha256"],
                 "LIGHT_LEGACY_PROJECT_INPUT_CHANGED")
        with locked(folder/"light-legacy.lock"):
            target=folder/"grade-cpu.grademirror"
            need(not target.exists() and not (folder/"cpu-compatibility.json").exists(),"LIGHT_LEGACY_PROJECT_EXISTS")
            stage=folder/(".legacy-"+uuid.uuid4().hex)
            self._copy_cpu_bundle(source,stage)
            target_marker=folder/"cpu-compatibility.json"
            # Publish authority before the final rename. Opening sees a recipe only after both exist.
            marker=dict(m,recipe={**m["recipe"],"path":str(target/"COMPLETE.json")})
            atomic_json(target_marker,marker)
            stage.rename(target)
        return self.open_project(folder)

    def create_refinement(self,project,destination):
        view=self.open_project(project)
        if view.get("light_backend")!="cpu":
            return super().create_refinement(project,destination)
        from ..engine.profiles import R0
        need(view["profile_id"]==R0 and not (Path(project)/"cpu-compatibility.json").exists(),
             "LIGHT_CPU_REFINEMENT_PARENT_UNSUPPORTED")
        super().create_project(Path(view["source"]["canonical_path"]),Path(view["reference"]["local_path"]),
                               destination,mode="fine")
        self._copy_cpu_bundle(Path(view["light_recipe"]),Path(destination)/"cpu-parent.grademirror")
        return self.open_project(destination)

    def inspect(self,project,operation):
        from ..image_probe import inspect_pair
        from ..engine.profiles import FINE
        view=self.open_project(project)
        src=Path(view["source"]["canonical_path"])
        ref=Path(view["reference"]["local_path"])
        a,b=inspect_pair(src,ref)
        quality="fine" if view["profile_id"]==FINE else "fast"
        if view.get("saved_recovery"):
            operation="replay"
        elif operation=="fit" and view.get("refinement_pending"):
            operation="refine"
        producer=view.get("light_backend","gpu") if operation in ("replay","refine") else None
        return view,src,ref,a,b,quality,operation,producer

    @staticmethod
    def _verified_saved_gpu_post(project,operation,producer,preference):
        if operation!="refine" or producer!="gpu" or preference not in ("auto","gpu"):
            return None
        from ..project import _read_project_manifest
        from ..refinement import checked_seed
        from ..saved_post import gpu
        project=Path(project)
        manifest,forward=_read_project_manifest(project)
        need(not forward,"LIGHT_SAVED_POST_FORWARD_SCHEMA")
        binding=gpu(project/"refinement",checked_seed(project,manifest))
        return binding[0] if binding is not None else None

    @staticmethod
    def _check_fast_cpu_plan(project,view,source,reference,quality,operation,producer,environment):
        from . import portable
        if not portable.enabled() or (quality,operation,producer)!=("fast","replay","cpu"):
            return
        if (Path(project)/"cpu-compatibility.json").is_file():
            return  # The existing legacy flow checks its own exact record.
        from ..preview.service import recipe
        from .fixed_c_bridge import check_plan
        directory=Path(view["light_recipe"])
        check_plan(directory,recipe(directory),source,reference,environment)

    def plan_project(self,project,operation):
        view,_,_,a,b,quality,operation,producer=self.inspect(project,operation)
        env=rt.snapshot()
        self._check_fast_cpu_plan(project,view,a,b,quality,operation,producer,env)
        preference=self.preference()
        value=planner.plan(a,b,quality,operation,env,producer,prefer=preference,
            saved_gpu_post=self._verified_saved_gpu_post(project,operation,producer,preference))
        return value

    def _execute(self,project,store,output,key,operation):
        need(not self.runtime_problem,"LIGHT_APPLICATION_RUNTIME_UNVERIFIED",self.runtime_problem)
        preflight_started=time.perf_counter()
        project,store,output=map(lambda p:Path(p).resolve(),(project,store,output))
        root=rt.data_root()
        need(store.is_relative_to(root) and output.is_relative_to(root),"LIGHT_WORKSPACE_SCOPE")
        store.mkdir(parents=True,exist_ok=True)
        with locked(root/"execution-admit.lock",timeout=1):
            marker=store/"light-route.json"
            retry_from=self._retry_sources.pop(key,None)
            if marker.is_file():
                saved=strict_load(marker)
                need(saved["key"]==key and saved["operation"]==operation and saved["project"]==str(project)
                     and saved["output"]==str(output),
                     "LIGHT_KEY_CONFLICT")
                if (store/retry.RECEIPT).is_file():
                    raise retry.ResourceChoice(strict_load(store/retry.RECEIPT))
                if self.validation is not None:
                    return self.validation.reuse(store,saved)
                return self._reuse(store,saved)
            view,src,ref,a,b,quality,op,producer=self.inspect(project,operation)
            env=rt.snapshot()
            self._check_fast_cpu_plan(project,view,a,b,quality,op,producer,env)
            prefer=self._operation_preferences.pop(key,None) or self.preference()
            qualification=getattr(self.validation,"capacity_qualification",None)
            from .resources import candidate_backend
            need(qualification is None or prefer==(candidate_backend(qualification) or "cpu"),
                 "LIGHT_FIRST_CAPACITY_SCOPE")
            plan=planner.plan(a,b,quality,op,env,producer,prefer=prefer,qualification=qualification,
                saved_gpu_post=self._verified_saved_gpu_post(project,op,producer,prefer))
            if op=="fit" and prefer=="mixed":
                from .recovery import mixed_seed
                from ..saved_post import mixed
                retained=mixed(project,mixed_seed(project))
                if retained is not None:plan["saved_mixed_post"]=retained
            self.last_plan=plan
            if self.validation is not None:
                self.validation.admit(plan,env,project,store,key)
                plan=dict(plan,validation_evidence=True)
            try:
                selected=planner.freeze(plan,env,project,store,key)
            except planner.RouteRefusal as error:
                error.routing.update(project=str(project),request_key=key,operation=operation)
                raise
            backend=selected["selection"]["backend"]
            from .recovery import mixed_seed
            if mixed_seed(project) and op=="fit":
                need(backend=="mixed","MIXED_RECOVERY_BACKEND_LOCK")
            route=dict(backend=backend,key=key,operation=operation,project=str(project),output=str(output),
                       plan_path=selected["plan_path"],plan_id=selected["plan_id"])
            self._shared_available(backend)
            if retry_from is not None:
                route["retry"]=retry.prior_intent(retry_from,root,store,key,project,operation,selected)
            if backend=="cpu":
                route["cpu_key"]=str(uuid.uuid5(uuid.NAMESPACE_URL,str(store)+"|"+key))
                if quality=="fine" and op=="fit":
                    route["cpu_segment"]="fast"
            route["preflight_seconds"]=time.perf_counter()-preflight_started
            atomic_json(marker,route)
            boundary=getattr(self.validation,"before_science",None)
            with self._inner_refusal(selected,route,store,env),rt.scope(selected,before_science=boundary):
                if backend=="cpu":
                    from ..preview import service
                    output.mkdir(parents=True,exist_ok=True)
                    from ..cpu_replay.contract import binding
                    reference=dict(file=binding(ref),header={n:b[n] for n in ("width","height","bits")})
                    need(reference["file"]["sha256"]==view["reference"]["byte_sha256"],"LIGHT_REFERENCE_CHANGED")
                    target=project/"grade-cpu.grademirror"
                    compat=project/"cpu-compatibility.json"
                    if quality=="fine" and op=="fit":
                        suffix="-fast-"+selected["plan_id"][:12] if "retry" in route else "-fast"
                        fast=project.with_name(project.stem+suffix+".grademirror")
                        super().create_project(src,ref,fast,mode="fast")
                        fast_recipe=fast/"grade-cpu.grademirror"
                        first=service.submit("fit",src,ref,fast_recipe,route["cpu_key"],display_reference=reference)
                        need(first.get("state")=="SUCCEEDED" and first.get("binding"),"LIGHT_CPU_FINE_FAST_INCOMPLETE")
                        self._remember_timing(store,first,segment="fast")
                        try:
                            if self.validation is not None:
                                self.validation.capture_parent(fast,route,first)
                        finally:
                            release=service.consume(route["cpu_key"],first,"release")
                        if self.validation is not None:
                            self.validation.parent_released(fast,route,first,release)
                        atomic_json(store/"cpu-fine-parent.json",dict(project=str(fast),job_id=first["job_id"],
                                    attempt_id=first["attempt_id"],cpu_key=route["cpu_key"]))
                        self._copy_cpu_bundle(fast_recipe,project/"cpu-parent.grademirror")
                        route["cpu_key"]=str(uuid.uuid5(uuid.NAMESPACE_URL,str(store)+"|"+key+"|fine"))
                        route["cpu_segment"]="fine"
                        atomic_json(marker,route)
                        need(not (store/"light-cancel.json").exists(),"CANCELLED",exit_code=8)
                        op="refine"
                    cpu_op="fine-replay" if quality=="fine" and op=="replay" else op
                    reply=service.submit(cpu_op,src,ref if op in ("fit","refine") else None,target,
                                         route["cpu_key"],display_reference=reference,
                                         parent=project/"cpu-parent.grademirror" if op=="refine" else None,
                                         compatibility=compat if compat.is_file() else None)
                    reply=self._cpu_fine_address(store,route,key,reply)
                    return dict(reply,output_root=str(output),source_image_metadata=a,light_backend="cpu")
                if backend=="mixed":
                    from ..hybrid_replay import BACKEND_ID
                    from .. import gm004a_api
                    if op=="fit":
                        reply=gm004a_api.fit_new_pair(project,store,output,request_key=key,
                            client_identity=self._client_identity(),execution_backend=BACKEND_ID,allow_experimental=True)
                        if reply.get("state")=="SUCCEEDED":
                            self._publish_mixed(project,output,key=key)
                    else:
                        reply=gm004a_api.replay(None,store,output,request_key=key,
                            client_identity=self._client_identity(),execution_backend=BACKEND_ID,allow_experimental=True,
                            execution_manifest=Path(view["light_recipe"]),source_path=src,reference_path=ref)
                    return dict(self._with_source_metadata(store,reply),output_root=str(output),light_backend="mixed")
                if operation=="fit" and op!="replay":
                    return dict(super().fit_new_pair(project,store,output,key)["result"],light_backend="gpu")
                return dict(super().replay(project,store,output,key),light_backend="gpu")

    def _shared_available(self,backend):
        # CPU and GPU each retain their own native release proofs. Cross-path
        # starts share one admission lock and refuse the other pool if occupied.
        root=rt.data_root()
        path=(root/"jobs/gm004a-resident/reservation.json" if backend=="cpu" else
              root/"cpu/pool/reservation.json")
        if path.is_file():
            if backend=="cpu":
                from ..gm004a_resident import ResidentAdmission
                state=ResidentAdmission(path.parent).inspect()
            else:
                from ..preview.service import reconcile_pool
                state=reconcile_pool(path.parent.parent)
            need(state is None or state["state"] in ("FREE","RELEASED"),"LIGHT_OTHER_BACKEND_RESIDENT",
                 "Release the result from the other preview backend first.",5)

    def _reuse(self,store,route):
        from ..gm004a_operations import OperationStore
        route_store=store
        if route["backend"]=="cpu":
            from ..preview.service import run_dir
            store=run_dir(route["cpu_key"])/"store"
            key=route["cpu_key"]
        else:
            key=route["key"]
        ops=OperationStore(store)
        saved=ops.lookup_request(key,saved_only=True)
        need(saved is not None,"LIGHT_PREVIOUS_PLAN_INTERRUPTED")
        _,spec=saved
        job,attempt=spec["job_id"],spec["attempt_id"]
        if route["backend"]=="cpu":
            try:
                plan=self._cpu_plan(route)
            except GMError as error:
                if error.code!="LIGHT_PLAN_CLIENT_LOST":
                    raise
                from ..gm004a_operations import _journal
                path,_=ops.path(job,attempt)
                with locked(path/"record.lock",timeout=.25):
                    status=_journal(path)[-1]["snapshot"]
                    need(status["state"] in ("SUCCEEDED","FAILED","CANCELLED"),"LIGHT_PREVIOUS_PLAN_INTERRUPTED")
                    if status["state"]=="SUCCEEDED":
                        need(strict_load(path/"session/lease.json")["state"]=="RELEASED","LIGHT_PREVIOUS_PLAN_INTERRUPTED")
                status=dict(status,binding=None,resident=False)
            else:
                with rt.scope(plan):
                    status=ops.status(job,attempt)
        else:
            status=ops.status(job,attempt)
            if route["backend"]=="mixed" and route["operation"]=="fit" and status["state"]=="SUCCEEDED":
                self._publish_mixed(Path(route["project"]),Path(route["output"]),key=key)
                if status.get("resident") and status.get("result_lease"):
                    from ..gm004a_api import _binding
                    status = dict(status, binding=_binding(status["result_lease"]))
        if route["backend"]=="gpu" and spec["semantics"].get("saved_recovery") and status["state"]=="SUCCEEDED":
            from .recovery import finish_publication
            path,_=ops.path(job,attempt)
            finish_publication(Path(route["project"]),path,key)
            if status.get("resident") and status.get("result_lease"):
                from ..gm004a_api import _binding
                status=dict(status,binding=_binding(status["result_lease"]))
        if route["backend"]=="cpu":
            status=self._cpu_fine_address(route_store,route,route["key"],status)
        return dict(status,output_root=route["output"],idempotent_hit=True)

    @staticmethod
    def _publish_mixed(project,output,*,key):
        from ..hybrid_fit.publication import publish_project
        try:
            return publish_project(project, output/"execution")
        except (OSError, ValueError, GMError) as error:
            if getattr(error, "code", None) in ("MIXED_PUBLICATION_CONFLICT", "MIXED_PREPARED_MEMBER_CONFLICT"):
                raise
            pending = GMError("MIXED_RESULT_PENDING_PUBLICATION",
                "Mixed result is ready. Retry publication with the same request key.", 7)
            pending.add_note(str(error))
            pending.request_key = key
            raise pending from error

    def fit_new_pair(self,project,store,output,key):
        reply=self._execute(project,store,output,key,"fit")
        self._remember_timing(store,reply)
        return dict(result=reply,project=self.open_project(project))

    def replay(self,project,store,output,key):
        reply=self._execute(project,store,output,key,"replay")
        self._remember_timing(store,reply)
        return reply

    @staticmethod
    def route(store):
        p=Path(store)/"light-route.json"
        return strict_load(p) if p.is_file() else None

    def _cpu(self,store):
        row=self.route(store)
        return row if row and row["backend"]=="cpu" else None

    def _resident_plan(self,store):
        row=self.route(store)
        if row and row["backend"] in ("mixed","gpu"):
            plan=rt.read_plan(row["plan_path"])
            need(plan["plan_id"]==row["plan_id"] and plan["selection"]["backend"]==row["backend"]
                 and plan["request_key"]==row["key"],"LIGHT_RESIDENT_PLAN_MISMATCH")
            return plan
        return None

    @staticmethod
    def _cpu_plan(row):
        plan=rt.read_plan(row["plan_path"])
        need(plan["plan_id"]==row["plan_id"] and plan["selection"]["backend"]=="cpu"
             and plan["request_key"]==row["key"], "LIGHT_CPU_PLAN_MISMATCH")
        return plan

    def _consume(self,row,job,attempt,binding,action,**kw):
        from ..preview.service import consume
        with rt.scope(self._cpu_plan(row)):
            return consume(row["cpu_key"],dict(job_id=job,attempt_id=attempt,binding=binding),action,**kw)

    def preview(self,store,job,attempt,binding,*,view,alpha,request_id,roi=None,max_side=1024):
        row=self._cpu(store)
        if not row:
            with rt.scope(self._resident_plan(store)):
                return super().preview(store,job,attempt,binding,view=view,alpha=alpha,
                    request_id=request_id,roi=roi,max_side=max_side)
        from ..preview.service import run_dir
        destination=run_dir(row["cpu_key"])/"previews"/(uuid.uuid4().hex+".rgb")
        reply=self._consume(row,job,attempt,binding,"preview",destination=destination,
                            view=view,alpha=alpha,request_id=request_id,roi=roi,max_side=max_side)
        try:
            raw=destination.read_bytes()
            need(len(raw)==reply["file"]["bytes"] and hashlib.sha256(raw).hexdigest()==reply["preview"]["pixel_sha256"],
                 "LIGHT_PREVIEW_BYTES")
            return dict(reply,pixels=raw)
        finally:
            destination.unlink(missing_ok=True)

    def preview_many(self,store,job,attempt,binding,requests,*,superseded=None):
        if not self._cpu(store):
            with rt.scope(self._resident_plan(store)):
                return super().preview_many(store,job,attempt,binding,requests,superseded=superseded)
        from ..desktop.native_preview import PreviewSuperseded
        replies=[]
        for req in requests:
            if superseded and superseded():
                raise PreviewSuperseded()
            replies.append(self.preview(store,job,attempt,binding,**req))
        return replies

    def export(self,store,job,attempt,binding,destination,alpha,key):
        row=self._cpu(store)
        if not row:
            with rt.scope(self._resident_plan(store)):
                result=super().export(store,job,attempt,binding,destination,alpha,key)
        else:
            atomic_json(Path(store)/"light-export.json",dict(key=key,job=job,attempt=attempt,binding=binding,
                                                              started=time.monotonic()))
            result=self._consume(row,job,attempt,binding,"export",destination=str(destination),
                                 alpha=alpha,request_key=key)
        try:
            from .eta import new_export_timings, save_history
            timings=new_export_timings(result)
            plan=self._read_eta_plan(self.route(store)) if timings else None
            if plan:
                save_history(rt.data_root()/"phase-history.json",self._phase_group(plan,"export"),
                             timings,self._eta_warning)
        except (GMError,OSError,ValueError,KeyError,TypeError) as error:
            self._eta_warning(str(error))
        return result

    def cancel_operation(self,store,key,job=None,attempt=None):
        row=self._cpu(store)
        if not row:
            with rt.scope(self._resident_plan(store)):
                return super().cancel_operation(store,key,job,attempt)
        from ..preview import service
        if key!=row["key"]:
            path=Path(store)/"light-export.json"
            if not path.is_file():
                return dict(cancel_disposition="NOT_ACCEPTED_YET")
            export=strict_load(path)
            need(export["key"]==key,"LIGHT_CANCEL_EXPORT_KEY")
            need((job,attempt)==(None,None) or (job,attempt)==(export["job"],export["attempt"]),
                 "ADDRESS_MISMATCH", "Cancel address differs from the active export", 2)
            with rt.scope(self._cpu_plan(row)):
                return service.cancel_export(row["cpu_key"],dict(job_id=export["job"],attempt_id=export["attempt"],
                    binding=export["binding"]),key)
        latest=self._cpu(store)
        need(latest and latest["key"]==key,"LIGHT_CANCEL_KEY_CHANGED")
        if (job,attempt)!=(None,None):
            expected={service.saved_address(latest["cpu_key"])}
            if latest.get("cpu_segment")=="fine":
                parent=strict_load(Path(store)/"cpu-fine-parent.json")
                original=str(uuid.uuid5(uuid.NAMESPACE_URL,str(Path(store).resolve())+"|"+key))
                need(parent["cpu_key"]==original,"LIGHT_CPU_FINE_HANDOFF")
                parent_address=(parent["job_id"],parent["attempt_id"])
                need(service.saved_address(original)==parent_address,"LIGHT_CPU_FINE_HANDOFF")
                expected.add(parent_address)
            need(all((job,attempt)) and (job,attempt) in expected,"ADDRESS_MISMATCH",
                 "Cancel address differs from the active CPU operation",2)
        plan=self._cpu_plan(latest)
        atomic_json(Path(store)/"light-cancel.json",dict(key=key))
        latest=self._cpu(store)
        need(latest and latest["key"]==key and latest["plan_id"]==plan["plan_id"],"LIGHT_CANCEL_KEY_CHANGED")
        with rt.scope(plan):
            reply = service.cancel(latest["cpu_key"])
        if not reply:
            return dict(cancel_disposition="REQUESTED")
        reply=self._cpu_fine_address(store, latest, key, reply)
        if latest.get("cpu_segment")=="fast" and reply.get("cancel_disposition")=="ALREADY_COMMITTED":
            reply=dict(reply,cancel_disposition="REQUESTED")
        return reply

    def abort_operation(self,store,key,job=None,attempt=None):
        if not self._cpu(store):
            return super().abort_operation(store,key,job,attempt)
        reply=self.cancel_operation(store,key,job,attempt)
        cleanup=reply.get("cleanup") or {}
        complete=cleanup.get("complete") is True and reply.get("state") in ("CANCELLED","FAILED")
        return dict(reply,cleanup_state="COMPLETE" if complete else "STARTUP_PENDING")

    def keep_alive(self,store,job,attempt,binding):
        row=self._cpu(store)
        if not row:
            with rt.scope(self._resident_plan(store)):
                return super().keep_alive(store,job,attempt,binding)
        return self._consume(row,job,attempt,binding,"keep_alive")

    def release(self,store,job,attempt,binding):
        row=self._cpu(store)
        if not row:
            with rt.scope(self._resident_plan(store)):
                return super().release(store,job,attempt,binding)
        return self._consume(row,job,attempt,binding,"release")

    def status(self,store,job,attempt):
        row=self._cpu(store)
        if row:
            from ..preview.service import run_dir
            from ..cpu_replay.api import result_status
            with rt.scope(self._cpu_plan(row)):
                return result_status(run_dir(row["cpu_key"])/"store",job,attempt)
        with rt.scope(self._resident_plan(store)):
            return super().status(store,job,attempt)

    def _phase_group(self,plan,action):
        import os
        import platform
        from .eta import history_group
        return history_group(plan,action,build=plan["build"],
                             hardware=[os.environ.get("PROCESSOR_IDENTIFIER",platform.machine()),os.cpu_count()])

    def _read_eta_plan(self,route):
        try:
            return rt.read_plan(route["plan_path"])
        except (GMError,OSError,ValueError,KeyError,TypeError):
            return None

    def _remember_timing(self,store,reply,*,segment=None):
        if reply.get("state")!="SUCCEEDED" or reply.get("idempotent_hit"):
            return
        from ..common import GMError
        from ..gm004a_operations import OperationStore
        from .eta import save_history
        try:
            route=self.route(store)
            plan=self._read_eta_plan(route)
            if not plan:
                return
            if route["backend"]=="cpu":
                from ..preview.service import run_dir
                store=run_dir(route["cpu_key"])/"store"
            path,_=OperationStore(store).path(reply["job_id"],reply["attempt_id"])
            from .eta import collect_timings
            timings=collect_timings(path,route["backend"])
            if segment=="fast" or not route.get("cpu_segment"):
                if route.get("preflight_seconds"):
                    timings["preflight"]=route["preflight_seconds"]
            if route["backend"]=="cpu" and plan["operation"]=="fit" and plan["quality"]=="fine":
                parent=Path(route["output"])/"eta-fast-parent.json"
                if segment=="fast":
                    if timings:
                        atomic_json(parent,timings)
                    return
                timings={"fine:"+name: value for name,value in timings.items()}
                if parent.is_file():
                    timings.update({"fast:"+name: value for name,value in strict_load(parent).items()})
            save_history(rt.data_root()/"phase-history.json",self._phase_group(plan,plan["operation"]),timings,
                         self._eta_warning)
        except (GMError,OSError,ValueError,KeyError,TypeError) as error:
            self._eta_warning(str(error))

    @staticmethod
    def _eta_warning(message):
        try:
            atomic_json(rt.data_root()/"phase-history-warning.json",dict(code="ETA_HISTORY_ISOLATED",message=message))
        except (OSError,ValueError):
            pass

    def preview_progress(self,store,job,attempt):
        from ..gm004a_operations import OperationStore
        from ..live_json import read_live_json
        row=self._cpu(store)
        if row:
            from ..preview.service import run_dir
            store=run_dir(row["cpu_key"])/"store"
        path,_=OperationStore(store).path(job,attempt)
        return read_live_json(path/"session/light-preview-progress.json")

    @staticmethod
    def _cpu_fine_address(store, route, key, row):
        if (key != route["key"] or route.get("cpu_segment") != "fine"
                or not row.get("job_id") or not row.get("attempt_id")):
            return row
        store = Path(store).resolve()
        parent = strict_load(store/"cpu-fine-parent.json")
        expected = str(uuid.uuid5(uuid.NAMESPACE_URL, str(store)+"|"+key))
        child = str(uuid.uuid5(uuid.NAMESPACE_URL, str(store)+"|"+key+"|fine"))
        need(parent["cpu_key"]==expected and route["cpu_key"]==child
             and parent["job_id"] and parent["attempt_id"]
             and (parent["job_id"],parent["attempt_id"])!=(row["job_id"],row["attempt_id"]),
             "LIGHT_CPU_FINE_HANDOFF")
        return dict(row,cpu_fine_handoff=dict(request_key=key,
                    parent_job_id=parent["job_id"],parent_attempt_id=parent["attempt_id"]))

    def status_key(self,store,key):
        row=self._cpu(store)
        if not row:
            with rt.scope(self._resident_plan(store)):
                state=super().status_key(store,key)
        else:
            from ..preview.service import status
            if key!=row["key"]:
                export_path=Path(store)/"light-export.json"
                if not export_path.is_file() or strict_load(export_path)["key"]!=key:
                    return None
            with rt.scope(self._cpu_plan(row)):
                state=status(row["cpu_key"])
            if key!=row["key"] and state:
                start=strict_load(export_path).get("started",0)
                if (state.get("gm066_monotonic") or 0)<start:
                    state=dict(stage="preflight",gm066_monotonic=start)
                else:
                    state={k:v for k,v in state.items() if k!="state"}
        if state:
            route=row if row else self.route(store)
            if route:
                if row:
                    state=self._cpu_fine_address(store,row,key,state)
                result=dict(state,light_backend=route["backend"],light_plan=route["plan_path"],
                            light_segment=route.get("cpu_segment"))
                if route["backend"]=="mixed" and key==route["key"] and state.get("detail_stage"):
                    from ..gm004a_operations import OperationStore
                    from .eta import mixed_progress
                    path,_=OperationStore(store).path(state["job_id"],state["attempt_id"])
                    result["light_progress"]=mixed_progress(path,state)
                return result
            return state
        return state

    def eta_text(self,active,row):
        from .eta import Estimate, normalized, phase_chain, read_history
        from ..desktop.i18n import tr
        if row.get("state") in ("FAILED","CANCELLED","CANCEL_REQUESTED","SUCCEEDED"):
            return None
        key=(str(active.store),active.request_key)
        plan_path=row.get("light_plan")
        if getattr(self,"_eta_key",None)!=key:
            self._eta_key=key
            self._eta_plan_path=None
            self._eta=Estimate()
        if plan_path and self._eta_plan_path!=plan_path:
            route=self.route(active.store)
            plan=self._read_eta_plan(route) if route and route["plan_path"]==plan_path else None
            if plan:
                operation="export" if active.kind=="export" else plan["operation"]
                group=self._phase_group(plan,operation)
                rows=read_history(rt.data_root()/"phase-history.json",self._eta_warning)
                phases=phase_chain(route["backend"],operation,plan["quality"],
                                   saved_post=bool(plan.get("saved_gpu_post") or plan.get("saved_mixed_post")))
                self._eta.set_context([entry["phases"] for entry in rows if entry["group"]==group],phases)
                self._eta_plan_path=plan_path
        stage=normalized(row.get("detail_stage") or row.get("stage"))
        if stage=="gpu_wait":
            self._eta.observe(stage,None,None,None)
            return None  # The desktop's resource-wait text includes the actual wait evidence.
        if row.get("light_backend")=="cpu" and stage=="H_area":
            stage="P3_CONSTRUCTION_AND_SOLVE"
        segment=row.get("light_segment")
        if segment in ("fast","fine") and stage and active.kind!="export":
            stage=segment+":"+stage
        progress=row.get("stage_progress") or {}
        stamp=row.get("gm066_monotonic")
        mixed=row.get("light_progress")
        if mixed:
            self._eta.observe(mixed.get("stage"),mixed.get("done"),mixed.get("total"),mixed.get("stamp"))
        elif stage in ("fit","fast:fit") and "update" in row:
            step=row.get("update")
            done=step-1 if row.get("stage")=="sampling" and type(step) is int else step
            self._eta.observe(stage,done,1000,stamp)
        elif stage in ("render","fast:render") and "total_tiles" in row:
            self._eta.observe(stage,row.get("completed_tiles"),row.get("total_tiles"),stamp)
        else:
            self._eta.observe(stage,progress.get("done",row.get("done")),progress.get("total",row.get("total")),
                              progress.get("monotonic",stamp))
        estimate=self._eta.text()
        if estimate and row.get("progress_warning"):
            estimate+=tr(" · 计数暂不可读取，可取消等待")
        return estimate

    def end_eta(self,active):
        if getattr(self,"_eta_key",None)==(str(active.store),active.request_key):
            self._eta_key=self._eta_plan_path=self._eta=None
