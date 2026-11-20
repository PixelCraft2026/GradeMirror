"""Shared standalone and long-lived owner CLI for normal-user preview."""
import argparse
import hashlib
import json
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path
from multiprocessing.connection import Listener

from ..common import GMError, atomic_json, strict_load, writable_path
from ..cpu_replay.contract import need
from ..gm003_ipc import bounded_call, recv_json, send_json
from ..store import locked
from ..winproc import current_identity, identity_alive
from . import runtime as rt
from .backend import Backend


ERRORS=(GMError,ValueError,OSError,KeyError,TypeError)


def _emit(value):
    print(json.dumps(value,ensure_ascii=False,allow_nan=False),flush=True)


def _failure(error):
    value=dict(state="REFUSED",code=getattr(error,"code",type(error).__name__),message=str(error),
               exit_code=getattr(error,"exit_code",2))
    if hasattr(error,"routing"):
        value["routing"]=error.routing
        if value["code"]=="NEEDS_RESOURCE_CHOICE":
            value["state"]=value["code"]
    if hasattr(error,"inner_resource"):
        value["inner_resource"]=error.inner_resource
    if getattr(error,"resource_boundary",None) is not None:
        value["resource_boundary"]=error.resource_boundary
    if value["code"]=="MIXED_RESULT_PENDING_PUBLICATION":
        value.update(state="RESULT_PENDING_PUBLICATION",request_key=error.request_key)
    return value


def _store(path):
    p=writable_path(Path(path))
    need(p.is_relative_to(rt.data_root()/"jobs"),"LIGHT_WORKSPACE_SCOPE")
    return p


def _dispatch(backend,row):
    action=row["command"]
    key=row.get("request_key") or str(uuid.uuid4())
    need(type(key) is str and 1<=len(key)<=128,"LIGHT_REQUEST_KEY")
    slot=hashlib.sha256(key.encode()).hexdigest()[:24]
    store=_store(row.get("store") or rt.data_root()/"jobs"/slot)
    output=rt.data_root()/"outputs"/slot
    retry_from=row.get("retry_from")
    if retry_from:
        need(row.get("project") and row.get("request_key"),"LIGHT_RETRY_EXPLICIT_INTENT")
    if row.get("project"):
        project=Path(row["project"])
    else:
        need(action=="fit" and row.get("source") and row.get("reference"),"LIGHT_PROJECT_REQUIRED")
        project=Path(row.get("out") or rt.data_root()/"projects"/(slot+".grademirror"))
        if not project.exists():
            backend.create_project(Path(row["source"]),Path(row["reference"]),project,mode=row.get("mode"))
        else:
            need((store/"light-route.json").is_file(),"LIGHT_PROJECT_ALREADY_EXISTS")
    if action=="refine":
        need(row.get("out"),"LIGHT_REFINEMENT_DESTINATION")
        target=Path(row["out"])
        if not retry_from and not (store/"light-route.json").is_file():
            backend.create_refinement(project,target)
        project=target
    if retry_from:
        backend.choose_retry(key,retry_from)
    try:
        reply=(backend.replay(project,store,output,key) if action=="replay" else
               backend.fit_new_pair(project,store,output,key)["result"])
    finally:
        backend.clear_choice(key)
    return dict(operation=reply,store=str(store),project=str(project),request_key=key)


def _roi(value):
    if type(value) is str:
        value=[int(n) for n in value.split(",")]
    if value is not None:
        need(type(value) in (tuple,list) and len(value)==4 and all(type(n) is int for n in value),
             "LIGHT_ROI_INVALID")
    return value


def _write_preview(reply,path):
    meta=reply["preview"]
    path=writable_path(Path(path))
    if path.suffix.lower()==".png":
        from PySide6.QtGui import QImage
        w,h=meta["shape"][1::-1]
        image=QImage(reply["pixels"],w,h,w*3,QImage.Format_RGB888).copy()
        # PNG encoding preserves the exact RGB samples. Color is carried in metadata.
        from PySide6.QtCore import QBuffer, QIODevice
        buffer=QBuffer()
        need(buffer.open(QIODevice.WriteOnly) and image.save(buffer,"PNG"),"LIGHT_PREVIEW_ENCODE")
        with path.open("xb") as stream:
            stream.write(bytes(buffer.data()))
        encoding="PNG"
    else:
        with path.open("xb") as stream:
            stream.write(reply["pixels"])
        encoding="RGB8"
    return dict(preview=meta,encoding=encoding,out=str(path))


class Session:
    def __init__(self,backend):
        self.backend=backend
        self.resident=None
        self.guard=threading.RLock()
        self.stop=threading.Event()

    def dispatch(self,row):
        with self.guard:
            return self._dispatch(row)

    def _dispatch(self,row):
        action=row["command"]
        if action in ("fit","replay","refine"):
            need(self.resident is None,"LIGHT_RELEASE_BEFORE_NEXT_OPERATION")
            selected=row.get("backend")
            if selected:
                need(self.backend.validation is None,"LIGHT_VALIDATION_BACKEND_OVERRIDE")
                need(selected in ("auto","cpu","gpu","mixed"),"LIGHT_BACKEND_PREFERENCE")
            result=_dispatch(Backend(selected) if selected else self.backend,row)
            op=result["operation"]
            if op.get("binding"):
                self.resident=(Path(result["store"]),op["job_id"],op["attempt_id"],op["binding"])
            return result
        need(self.resident is not None,"RESULT_NOT_RESIDENT",exit_code=4)
        pair=self.resident
        if row.get("job_id") is not None:
            need((row["job_id"],row.get("attempt"))==pair[1:3],"LIGHT_RESULT_ADDRESS")
        if row.get("binding") is not None:
            need(row["binding"]==pair[3],"LIGHT_RESULT_BINDING")
        if action=="preview":
            need(row.get("out"),"LIGHT_PREVIEW_OUTPUT_REQUIRED")
            reply=self.backend.preview(*pair,view=row.get("view","result"),alpha=row.get("alpha",1.),
                request_id=row.get("request_id") or str(uuid.uuid4()),roi=_roi(row.get("roi")),
                max_side=row.get("max_side",1024))
            return _write_preview(reply,row["out"])
        if action=="export":
            return self.backend.export(*pair,Path(row["out"]).absolute(),row.get("alpha",1.),
                                       row.get("request_key") or str(uuid.uuid4()))
        if action=="keep-alive":
            return self.backend.keep_alive(*pair)
        if action=="status":
            return self.backend.status(*pair[:3])
        if action=="release":
            return self.release()
        raise GMError("LIGHT_SESSION_COMMAND_UNKNOWN",str(action))

    def release(self):
        with self.guard:
            if self.resident is not None:
                result=self.backend.release(*self.resident)
                self.resident=None
                return result

    def _heartbeat(self):
        while not self.stop.wait(30):
            if not self.guard.acquire(blocking=False):
                continue
            try:
                if self.resident:
                    self.backend.keep_alive(*self.resident)
            except ERRORS as error:
                print(str(error),file=sys.stderr,flush=True)
            finally:
                self.guard.release()

    def run(self,descriptor=None):
        keeper=threading.Thread(target=self._heartbeat,name="preview-cli-heartbeat",daemon=True)
        keeper.start()
        try:
            if descriptor is None:
                for line in sys.stdin:
                    try:
                        row=json.loads(line)
                        if row["command"]=="quit":
                            break
                        _emit(self.dispatch(row))
                    except ERRORS as error:
                        _emit(_failure(error))
            else:
                self.serve(descriptor)
        finally:
            self.stop.set()
            self.release()
            keeper.join()

    def serve(self,descriptor):
        from ..engine.cpu_postprocess.transport import _bounded
        address=descriptor_path(descriptor)
        key=secrets.token_hex(32)
        with Listener(("127.0.0.1",0),family="AF_INET",authkey=bytes.fromhex(key)) as listener:
            with locked(address.with_suffix(".lock"),timeout=1):
                need(not address.exists(),"LIGHT_CLI_SESSION_EXISTS")
                atomic_json(address,dict(schema="gm066-cli-owner-v1",owner=current_identity(),
                                         address=list(listener.address),key=key))
            try:
                while True:
                    with listener.accept() as conn:
                        try:
                            row=_bounded(lambda:recv_json(conn),time.monotonic()+10,lambda:False,address.parent,conn)
                            if row["command"]=="quit":
                                send_json(conn,dict(state="CLOSED",release=self.release()))
                                break
                            result=self.dispatch(row)
                        except (*ERRORS,EOFError) as error:
                            result=_failure(error)
                        try:
                            send_json(conn,result)
                        except (OSError,EOFError):
                            pass
            finally:
                with locked(address.with_suffix(".lock"),timeout=1):
                    if address.exists() and strict_load(address).get("key")==key:
                        address.unlink()


def descriptor_path(path):
    p=writable_path(Path(path))
    need(p.is_relative_to(rt.data_root()/"cli") and p.suffix.lower()==".json","LIGHT_CLI_SESSION_SCOPE")
    return p


def _remote(path,row):
    descriptor=strict_load(descriptor_path(path))
    need(descriptor["schema"]=="gm066-cli-owner-v1" and identity_alive(descriptor["owner"]) is True,
         "LIGHT_CLI_OWNER_GONE",exit_code=4)
    def exchange(conn):
        send_json(conn,row)
        return recv_json(conn)
    return bounded_call(descriptor["address"],descriptor["key"],exchange,deadline=time.monotonic()+7320)


def _operation_store(backend,store):
    from ..gm004a_operations import OperationStore
    route=backend.route(store)
    if route and route["backend"]=="cpu":
        from ..preview.service import run_dir
        return OperationStore(run_dir(route["cpu_key"])/"store")
    return OperationStore(store)


def parser():
    from .system import manifest
    offline=manifest().get("bundled_roles") is not None
    p=argparse.ArgumentParser(prog="GradeMirror-CLI",
        epilog="Additional commands: project, recipe, schema, adaptive. Use each command with --help.")
    p.add_argument("--store")
    p.add_argument("--connect")
    sub=p.add_subparsers(dest="command",required=True)
    doctor=sub.add_parser("doctor")
    doctor.add_argument("--json",action="store_true")
    if not offline:
        doctor.add_argument("--verify-runtime",action="store_true")
    sub.add_parser("quit").add_argument("--json",action="store_true")
    if not offline:
        e=sub.add_parser("environment")
        e.add_argument("profile",choices=rt.EXTERNAL)
        e.add_argument("python",type=Path)
    sub.add_parser("session").add_argument("--listen-file")
    sub.add_parser("environment-status")
    recovery=sub.add_parser("prepare-recovery",help="Prepare a new project from saved GPU recipe and render proof")
    for field in ("source","reference","archive","proof","out"):
        recovery.add_argument("--"+field,type=Path,required=True)
    mixed=sub.add_parser("prepare-mixed-recovery")
    for field in ("source","reference","archive","proof","solver","out"):
        mixed.add_argument("--"+field,type=Path,required=True)
    mixed_post=sub.add_parser("bind-mixed-post",help="Attach a saved postprocess recipe to a prepared mixed project")
    for field in ("project","archive"):
        mixed_post.add_argument("--"+field,type=Path,required=True)
    fine_post=sub.add_parser("bind-fine-checkpoint",help="Attach saved GPU P3 coefficients to a prepared refinement")
    for field in ("project","checkpoint","archive","partial","ranges"):
        fine_post.add_argument("--"+field,type=Path,required=True)
    legacy=sub.add_parser("legacy-cpu-import")
    legacy.add_argument("--project",required=True,type=Path)
    legacy.add_argument("--recipe",required=True,type=Path)
    for action in ("plan","fit","replay","refine","run","submit"):
        c=sub.add_parser(action)
        c.add_argument("--project")
        c.add_argument("--backend",choices=("auto","cpu","gpu","mixed"))
        c.add_argument("--json",action="store_true")
        c.add_argument("--store",default=argparse.SUPPRESS)
        c.add_argument("--connect",default=argparse.SUPPRESS)
        if action in ("plan","run","submit"):
            c.add_argument("--spec")
        if action=="plan":
            c.add_argument("--operation",choices=("fit","replay"),default="fit")
            continue
        c.add_argument("--request-key")
        c.add_argument("--retry-from",help="Previous resource-refusal receipt; requires a new request key and the same project")
        c.add_argument("--out")
        c.add_argument("--export")
        c.add_argument("--alpha",type=float,default=1.)
        if action=="fit":
            c.add_argument("--source")
            c.add_argument("--reference")
            c.add_argument("--mode",choices=("fast","fine"))
    for action in ("status","cancel","events","reconcile"):
        c=sub.add_parser(action)
        c.add_argument("job_id",nargs="?")
        c.add_argument("--attempt")
        c.add_argument("--store",default=argparse.SUPPRESS)
        c.add_argument("--request-key")
        c.add_argument("--json",action="store_true")
        if action=="events":
            c.add_argument("--after-seq",type=int,default=0)
            c.add_argument("--follow",action="store_true")
    result=sub.add_parser("result").add_subparsers(dest="result_command",required=True)
    for action in ("status","preview","export","keep-alive","release"):
        c=result.add_parser(action)
        c.add_argument("job_id",nargs="?")
        c.add_argument("--attempt")
        c.add_argument("--binding",type=Path)
        c.add_argument("--connect",default=argparse.SUPPRESS)
        c.add_argument("--store",default=argparse.SUPPRESS)
        c.add_argument("--json",action="store_true")
        if action in ("preview","export"):
            c.add_argument("--out",required=True)
            c.add_argument("--alpha",type=float,default=1.)
            c.add_argument("--request-key")
        if action=="preview":
            c.add_argument("--view",choices=("source","result","reference"),default="result")
            c.add_argument("--max-side",type=int,default=1024)
            c.add_argument("--request-id")
            c.add_argument("--roi")
    return p


def adaptive(argv):
    p=argparse.ArgumentParser(prog="GradeMirror-CLI adaptive")
    actions=p.add_subparsers(dest="action",required=True)
    actions.add_parser("plan").add_argument("--request",required=True)
    actions.add_parser("open").add_argument("path")
    submit=actions.add_parser("submit")
    submit.add_argument("--plan",required=True,help="Preview fit/replay/refine intent JSON")
    submit.add_argument("--workspace")
    submit.add_argument("--connect",required=True)
    submit.add_argument("--request-key")
    for action in ("status","cancel","preview","export","keep-alive","release"):
        c=actions.add_parser(action)
        c.add_argument("--workspace",required=True)
        c.add_argument("--job-id",required=True)
        c.add_argument("--attempt",required=True)
        c.add_argument("--json",action="store_true")
        if action not in ("status","cancel"):
            c.add_argument("--connect",required=True)
            c.add_argument("--binding",required=True)
        if action in ("preview","export"):
            c.add_argument("--out",required=True)
            c.add_argument("--alpha",type=float,default=1.)
            c.add_argument("--request-key")
        if action=="preview":
            c.add_argument("--view",choices=("source","reference","result"),default="result")
            c.add_argument("--roi")
    a=p.parse_args(argv)
    if a.action=="open":
        return main(["project","show",a.path])
    if a.action=="plan":
        return main(["plan","--spec",a.request])
    if a.action=="submit":
        command=["submit","--spec",a.plan,"--connect",a.connect]
        if a.workspace:
            command.extend(("--store",a.workspace))
        if a.request_key:
            command.extend(("--request-key",a.request_key))
        return main(command)
    args=[a.job_id,"--attempt",a.attempt,"--store",a.workspace]
    if a.action in ("status","cancel"):
        return main([a.action,*args])
    args.extend(("--connect",a.connect,"--binding",a.binding))
    if a.action in ("preview","export"):
        args.extend(("--out",a.out,"--alpha",str(a.alpha)))
        if a.request_key:
            args.extend(("--request-key",a.request_key))
    if a.action=="preview":
        args.extend(("--view",a.view))
        if a.roi:
            args.extend(("--roi",a.roi))
    return main(["result",a.action,*args])


def main(argv):
    if argv and argv[0]=="adaptive":
        return adaptive(argv[1:])
    # Metadata-only historical operations are shared with the original CLI.
    if argv[:2]==["project","import"]:
        from ..portable_project import import_gm003_delivery
        p=argparse.ArgumentParser(prog="GradeMirror-CLI project import")
        for name in ("from-delivery","out","source","recovery-commit","recovery-output"):
            p.add_argument("--"+name,type=Path,required=True)
        p.add_argument("--json",action="store_true")
        args=p.parse_args(argv[2:])
        try:
            _emit(import_gm003_delivery(args.from_delivery,args.out,source_path=args.source,
                recovery_commit=args.recovery_commit,recovery_output=args.recovery_output))
            return 0
        except ERRORS as error:
            value=_failure(error)
            _emit(value)
            return value["exit_code"]
    if argv and argv[0] in ("project","schema","recipe"):
        from ..cli import main as original
        return original(argv)
    args=parser().parse_args(argv)
    backend=Backend()
    try:
        command=args.command
        if command in ("doctor","environment-status"):
            from .system import check_loaded
            from ..preview.contract import architecture
            from .system import manifest
            roles=rt.snapshot()
            for key,row in roles.items():
                rt.verify_role(row,key)
            verified=False
            if getattr(args,"verify_runtime",False):
                for key,row in roles.items():
                    rt.prepare_probe(Path(row["exe"]),key)
                verified=True
            overview=backend.device_overview()
            detection=overview["device"]
            value=dict(version=rt.VERSION,edition=manifest().get("edition"),
                       architecture=architecture(),windows_build=sys.getwindowsversion().build,
                       binary_trust=check_loaded(),runtime_verified=verified,device_detection=detection,
                       memory=overview["memory"],preference=overview["preference"],cpu=overview["cpu"],
                       environment={key:dict(versions=row["versions"],gpu=row["gpu"]) for key,row in roles.items()},
                       science=0)
        elif command=="session":
            Session(backend).run(args.listen_file)
            return 0
        elif command=="environment":
            value=rt.register(args.python,args.profile)
        elif command=="prepare-recovery":
            from .recovery import prepare
            value=prepare(args.source,args.reference,args.archive,args.proof,args.out)
        elif command=="prepare-mixed-recovery":
            from .recovery import prepare_mixed
            value=prepare_mixed(args.source,args.reference,args.archive,args.proof,args.solver,args.out)
        elif command=="bind-mixed-post":
            from ..saved_post import bind_mixed
            value=bind_mixed(args.project,args.archive)
        elif command=="bind-fine-checkpoint":
            from ..saved_post import bind_gpu
            value=bind_gpu(args.project,args.checkpoint,args.archive,args.partial,args.ranges)
        elif command=="legacy-cpu-import":
            value=backend.import_legacy_cpu(args.project,args.recipe)
        elif command=="plan":
            if args.backend:
                backend=Backend(args.backend)
            if args.spec:
                request=strict_load(Path(args.spec))
                if "project" not in request:
                    from ..resource_planning.cli import from_file
                    _emit(from_file(args.spec,None))
                    return 0
                value=backend.plan_project(Path(request["project"]),request.get("operation","fit"))
            else:
                need(args.project,"LIGHT_PROJECT_REQUIRED")
                value=backend.plan_project(Path(args.project),args.operation)
        elif command in ("result","quit"):
            row=vars(args).copy()
            row["command"]=row.pop("result_command",command)
            for field in ("connect","store","json"):
                row.pop(field,None)
            if row.get("binding"):
                row["binding"]=strict_load(row["binding"])
            if args.connect:
                value=_remote(args.connect,row)
            else:
                need(row["command"]=="status" and args.store and args.job_id and args.attempt,
                     "LIGHT_CLI_SESSION_REQUIRED",exit_code=4)
                value=backend.status(_store(args.store),args.job_id,args.attempt)
        elif command in ("status","cancel","events","reconcile"):
            need(args.store,"LIGHT_WORKSPACE_SCOPE")
            store=_store(args.store)
            if args.request_key:
                need(command in ("status","cancel","reconcile"),"LIGHT_JOB_ADDRESS")
                value=(backend.cancel_operation(store,args.request_key) if command=="cancel"
                       else backend.status_key(store,args.request_key))
            else:
                need(args.job_id and args.attempt,"LIGHT_JOB_ADDRESS")
                ops=_operation_store(backend,store)
                if command=="events":
                    after=args.after_seq
                    while True:
                        rows=ops.events(args.job_id,args.attempt,after)
                        if not args.follow:
                            value=rows
                            break
                        for event in rows:
                            _emit(event)
                            after=event["seq"]
                        state=ops.progress_snapshot(args.job_id,args.attempt)
                        if state["state"] in ("SUCCEEDED","FAILED","CANCELLED"):
                            return 0
                        time.sleep(.5)
                elif command=="cancel":
                    path,spec=ops.path(args.job_id,args.attempt)
                    route=backend.route(store)
                    key=route["key"] if route and route["backend"]=="cpu" else spec["request_key"]
                    value=backend.cancel_operation(store,key,args.job_id,args.attempt)
                else:
                    value=backend.status(store,args.job_id,args.attempt)
        else:
            row={k:v for k,v in vars(args).items() if v is not None and k not in ("connect","spec","json","export","alpha")}
            if command in ("run","submit"):
                need(args.spec,"LIGHT_SPEC_REQUIRED")
                row=strict_load(Path(args.spec))
                need(row.get("command") in ("fit","replay","refine"),"LIGHT_SPEC_OPERATION")
                if args.store:
                    row=dict(row,store=args.store)
                if args.request_key:
                    row=dict(row,request_key=args.request_key)
                if args.retry_from:
                    row=dict(row,retry_from=args.retry_from)
            if args.connect:
                value=_remote(args.connect,row)
            else:
                need(command!="submit","LIGHT_CLI_SESSION_REQUIRED",exit_code=4)
                session=Session(backend)
                try:
                    value=session.dispatch(row)
                    if args.export and session.resident:
                        value["export"]=session.dispatch(dict(command="export",out=args.export,alpha=args.alpha))
                finally:
                    if session.resident:
                        value["release"]=session.release()
        _emit(value)
        if isinstance(value,dict):
            operation=value.get("operation")
            return value.get("exit_code") or (operation.get("exit_code") if isinstance(operation,dict) else None) or 0
        return 0
    except ERRORS as error:
        value=_failure(error)
        _emit(value)
        return value["exit_code"]
