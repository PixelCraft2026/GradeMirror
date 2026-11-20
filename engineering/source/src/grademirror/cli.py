"""Public JSON CLI and private fixed supervisor entry."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from .common import GMError, VERSION, PROFILE_IDS, GM003_CAPABILITY, strict_load, normalize_request, public_backend
from .store import Store


def _parser():
    parser = argparse.ArgumentParser(prog="grademirror")
    parser.add_argument("--store", default=os.environ.get("GRADEMIRROR_STORE_DIR", str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GradeMirror" / "jobs")))
    sub = parser.add_subparsers(dest="command", required=True)
    adaptive=sub.add_parser('adaptive');ac=adaptive.add_subparsers(dest='adaptive_command',required=True)
    p=ac.add_parser('plan');p.add_argument('--request',required=True);p.add_argument('--snapshot');p.add_argument('--json',action='store_true')
    p=ac.add_parser('submit');p.add_argument('--plan',required=True);p.add_argument('--workspace',required=True);p.add_argument('--request-key',required=True);p.add_argument('--experimental',action='store_true');p.add_argument('--qualification');p.add_argument('--json',action='store_true')
    for action in ('status','cancel','preview','export','release','keep-alive'):
        p=ac.add_parser(action);p.add_argument('--workspace',required=True);p.add_argument('--job-id',required=True);p.add_argument('--attempt',required=True);p.add_argument('--json',action='store_true')
        if action=='cancel':p.add_argument('--child-job-id');p.add_argument('--child-attempt')
        if action in ('preview','export','release','keep-alive'):p.add_argument('--binding',required=True)
        if action in ('preview','export'):p.add_argument('--out',required=True);p.add_argument('--alpha',type=float,default=1.)
        if action in ('preview','export'):p.add_argument('--request-key',required=True)
    p=ac.add_parser('open');p.add_argument('path');p.add_argument('--json',action='store_true')
    recipe = sub.add_parser("recipe")
    recipe_sub = recipe.add_subparsers(dest="recipe_command", required=True)
    pack = recipe_sub.add_parser("pack")
    pack.add_argument("--cpu-fast", required=True)
    pack.add_argument("--cpu-fine")
    pack.add_argument("--out", required=True)
    pack.add_argument("--request-key", required=True)
    pack.add_argument("--json", action="store_true")
    for action in ("inspect", "verify", "relink"):
        command = recipe_sub.add_parser(action)
        command.add_argument("path")
        command.add_argument("--json", action="store_true")
        if action == "relink":
            command.add_argument("--source", required=True)
    planning = sub.add_parser("plan")
    planning.add_argument("--spec", required=True)
    planning.add_argument("--snapshot", help="Optional explicit resource metadata JSON; advisory only")
    planning.add_argument("--json", action="store_true")
    sub.add_parser("doctor").add_argument("--json", action="store_true")
    sub.add_parser("schema").add_argument("--json", action="store_true")
    for name in ("run", "submit"):
        p = sub.add_parser(name)
        p.add_argument("--spec", required=True)
        p.add_argument("--json", action="store_true")
    for name in ("status", "cancel", "reconcile", "events"):
        p = sub.add_parser(name)
        p.add_argument("job_id")
        p.add_argument("--attempt", required=True)
        p.add_argument("--json", action="store_true")
        if name == "events":
            p.add_argument("--after-seq", type=int, default=0)
            p.add_argument("--follow", action="store_true")
    for name in ("inspect", "render"):
        p = sub.add_parser(name)
        p.add_argument("--source")
        p.add_argument("--reference")
        p.add_argument("--project")
        p.add_argument("--revision")
        p.add_argument("--out")
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("project")
    psub = p.add_subparsers(dest="project_command", required=True)
    c = psub.add_parser("create")
    for arg in ("source", "reference", "out"):
        c.add_argument("--" + arg, required=True)
    c.add_argument("--json", action="store_true")
    c.add_argument("--mode", choices=("fast","fine"))
    c.add_argument("--profile", help="Frozen algorithm profile for this new project")
    s = psub.add_parser("show")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    r = psub.add_parser("relink")
    r.add_argument("path")
    r.add_argument("--source", required=True)
    r.add_argument("--json", action="store_true")
    f = sub.add_parser("fit")
    f.add_argument("--project")
    f.add_argument("--source")
    f.add_argument("--reference")
    f.add_argument("--mode", choices=('fast', 'fine'))
    f.add_argument("--cpu-recipe-out")
    f.add_argument("--execution-manifest")
    f.add_argument("--out", required=True)
    f.add_argument("--request-key")
    f.add_argument("--profile", help="Optional assertion of the project's frozen profile")
    f.add_argument("--execution-backend")
    f.add_argument("--experimental", action="store_true")
    for option in ('resource-policy','ram-limit-mib','commit-limit-mib','vram-limit-mib'):
        f.add_argument('--'+option,type=int if option!='resource-policy' else str)
    f.add_argument('--cpu-core-rows',type=int,choices=(256,512))
    f.add_argument("--json", action="store_true")
    fine=sub.add_parser('refine')
    for option in ('recipe','source','reference','cpu-recipe-out','out','request-key','execution-backend','execution-manifest'):
        fine.add_argument('--'+option,required=True)
    fine.add_argument('--experimental',action='store_true')
    fine.add_argument('--json',action='store_true')
    p = sub.add_parser("result")
    result_sub = p.add_subparsers(dest="result_command", required=True)
    for name in ("status", "preview", "export", "release"):
        r = result_sub.add_parser(name)
        r.add_argument("job_id")
        r.add_argument("--attempt", required=True)
        r.add_argument("--json", action="store_true")
        if name != "status":
            r.add_argument("--binding", required=True, help="JSON file with the complete result identity")
        if name in {"preview", "export"}:
            r.add_argument("--alpha", required=True, type=float)
        if name in {"preview", "export"}:
            r.add_argument("--out", required=name == "export")
        if name == "preview":
            r.add_argument("--view", choices=("source", "reference", "result"), default="result")
            r.add_argument("--max-side", type=int, default=1024)
            r.add_argument("--roi", help="x0,y0,x1,y1 on source grid")
            r.add_argument("--request-id")
        if name == "export":
            r.add_argument("--format", choices=("tiff", "jpeg", "png", "webp", "bmp"))
            r.add_argument("--bit-depth", type=int)
            r.add_argument("--icc-policy")
            r.add_argument("--request-key")
            r.add_argument("--export-root", help="Explicit allowed directory for the export destination")
    r = sub.add_parser("replay")
    r.add_argument("--cpu-recipe", "--recipe", dest='cpu_recipe')
    r.add_argument("job_id", nargs="?")
    r.add_argument("--attempt")
    r.add_argument("--project")
    r.add_argument("--execution-backend")
    r.add_argument("--experimental", action="store_true")
    for option in ('resource-policy','ram-limit-mib','commit-limit-mib','vram-limit-mib'):
        r.add_argument('--'+option,type=int if option!='resource-policy' else str)
    r.add_argument('--cpu-core-rows',type=int,choices=(256,512))
    r.add_argument("--execution-manifest")
    r.add_argument("--source")
    r.add_argument("--reference")
    r.add_argument("--out")
    r.add_argument("--request-key")
    r.add_argument("--json", action="store_true")
    i = psub.add_parser("import")
    i.add_argument("--from-delivery", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--source")
    i.add_argument("--json", action="store_true")
    p = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    p.add_argument("--store", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--attempt", required=True)
    return parser


def _emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def main(argv=None):
    args = _parser().parse_args(argv)
    instance_scope=None
    try:
        if args.command=='adaptive':
            from .adaptive_execution.cli import dispatch
            value=dispatch(args);_emit(value);return 0
        if os.environ.get('GM054_CONTEXT') or os.environ.get('GM053_CONTEXT'):
            from .cpu_first_fit.contract import active
            from .cpu_replay import runtime
            cleanup_only=args.command in ('status','reconcile') or (args.command=='result' and args.result_command in ('status','release'))
            instance_scope=active(os.environ.get('GM054_CONTEXT') or os.environ['GM053_CONTEXT'],cleanup=cleanup_only)
            context=instance_scope.__enter__()
            runtime.enter('public_cli', Path(context['evidence_root'])/'clients'/context['id']/str(os.getpid()),entry_only=cleanup_only,metadata_cleanup=cleanup_only)
        if args.command == "recipe":
            from .cpu_recipe.cli import dispatch
            value, code = dispatch(args)
            _emit(value)
            return code
        if args.command=='refine':
            from .cpu_refine import refine_cpu_recipe
            value=refine_cpu_recipe(Path(args.recipe),Path(args.source),Path(args.reference),Path(args.store),Path(args.out),
                bundle_directory=Path(args.cpu_recipe_out),request_key=args.request_key,
                execution_backend=args.execution_backend,allow_experimental=args.experimental,
                execution_manifest=Path(args.execution_manifest))
            _emit(value)
            return value.get('exit_code',0) or 0
        if args.command == "plan":
            from .resource_planning.cli import from_file
            _emit(from_file(args.spec, args.snapshot))
            return 0
        if args.command == "_supervise":
            from .supervisor import run_supervisor
            run_supervisor(Path(args.store), Path(sys.argv[0]), args.job, args.attempt)
            return 0
        if args.command == "doctor":
            from .engine.independent_r0.profile import PROFILE_CONTRACT_SHA256
            from .engine import profiles
            from .engine.independent_r0_p3_lld10.profile import LIMITS as fine_limits
            _emit({"app_version": __version__, "protocol_version": VERSION,
                "python": sys.version.split()[0], "executable": str(Path(sys.executable).resolve()),
                "platform": sys.platform, "profile_capabilities": {p: "NEW_PAIR_FIT_AND_SAVED_REPLAY" for p in sorted(PROFILE_IDS)},
                "mock_public": False, "result_session": "NOT_ALLOCATED", "science_imports": False,
                "independent_r0": {"profile_id": "independent-r0-lab-m05-xy001-v1",
                    "contract_sha256": PROFILE_CONTRACT_SHA256,
                    "production_qualified": False, "public_solve": "NEW_PAIR_FIT_AND_SAVED_REPLAY",
                    "fit_contract": "gm007-fit-v1"},
                "new_pair_default": profiles.DEFAULT_PROFILE_ID,
                "modes": {"fast": {"profile_id": profiles.R0, "default": True},
                    "fine": {"profile_id": profiles.FINE, "default": False,
                        "reopen": "EXPLICIT_R0_RENDER_P3_AND_TWO_LLF_NO_FIT_NO_CG",
                        "limits": fine_limits}},
                "project_profiles": {p: {"version": v["version"], "label": v["label"],
                    "contract_sha256": v["contract_sha256"], "fit_contract": v["fit_contract"],
                    "production_qualified": False} for p,v in profiles.PROFILES.items()},
                "commands": {"project create": "IMPLEMENTED", "fit": "NEW_PAIR", "replay --project": "SAVED_RECIPE",
                    "result preview/export/release": "IMPLEMENTED", "inspect": "NOT_IMPLEMENTED", "render": "NOT_IMPLEMENTED"},
                "runtime_distribution": "EXISTING_EXTERNAL_INTERPRETERS_NOT_PACKAGED"})
            return 0
        if args.command == "schema":
            schema_path = Path(sys.argv[0]).resolve().parents[1] / "schemas/job-request-0.1.0.json"
            schema = strict_load(schema_path)
            from .engine import profiles
            schema["x-project-workflow"] = {"create_profile_option": "--profile", "fit_profile_option": "optional frozen-profile assertion",
                "new_pair_default": profiles.DEFAULT_PROFILE_ID, "profiles": profiles.runtimes(),
                "metadata_parent_version": "0.1.0", "fitted_versions": ["0.4.0","0.5.0"], "create_mode_option":"--mode fast|fine",
                "legacy_run_submit": "RETIRED_USE_R0_PROJECT_WORKFLOW", "independent_inspect_render": "NOT_IMPLEMENTED"}
            _emit(schema)
            return 0
        if args.command in {"inspect", "render"}:
            raise GMError("BACKEND_NOT_IMPLEMENTED", args.command, 4)
        if args.command == "project":
            from . import project
            from .engine.profiles import runtimes
            try:
                if args.project_command == "create":
                    result = project.create_project(Path(args.source), Path(args.reference), Path(args.out), profile=args.profile, mode=args.mode)
                elif args.project_command == "show":
                    result = project.show_project(Path(args.path), available_runtimes=runtimes())
                elif args.project_command == "import":
                    from .portable_project import import_gm003_delivery
                    result = import_gm003_delivery(Path(args.from_delivery), Path(args.out),
                        source_path=Path(args.source) if args.source else None)
                else:
                    result = project.relink_project(Path(args.path), Path(args.source), available_runtimes=runtimes())
            except project.ProjectError as exc:
                raise GMError(exc.code, str(exc), 3 if exc.code == "INPUT_IDENTITY_MISMATCH" else 2)
            _emit(result)
            return 0
        if args.command in {"run", "submit"}:
            request = strict_load(Path(args.spec))
            public_backend(normalize_request(request))
            store = Store(Path(args.store), Path(sys.argv[0]))
            result = store.run(request) if args.command == "run" else store.submit(request)
            _emit(result)
            return result.get("exit_code", 0) or 0
        if args.command == "replay" and args.cpu_recipe:
            if (args.project or args.job_id or args.attempt or args.reference or args.resource_policy or
                    any(v is not None for v in (args.ram_limit_mib, args.commit_limit_mib, args.vram_limit_mib)) or
                    not args.source or not args.out or not args.request_key or not args.execution_manifest):
                raise GMError("CPU_REPLAY_ARGS_INVALID", "Explicit CPU bundle, source, output, key and manifest required", 2)
            from .cpu_replay import replay_cpu_recipe
            result = replay_cpu_recipe(Path(args.cpu_recipe), Path(args.source), Path(args.store), Path(args.out),
                request_key=args.request_key, execution_backend=args.execution_backend,
                allow_experimental=args.experimental, execution_manifest=Path(args.execution_manifest),
                cpu_core_rows=args.cpu_core_rows)
            _emit(result)
            return result.get("exit_code", 0) or 0
        if args.command == "fit":
            if args.cpu_recipe_out or args.source or args.reference or args.execution_manifest or args.mode:
                if (args.project or args.profile or args.resource_policy or args.cpu_core_rows is not None or
                    any(v is not None for v in (args.ram_limit_mib,args.commit_limit_mib,args.vram_limit_mib)) or
                    not all((args.cpu_recipe_out,args.source,args.reference,args.execution_manifest,args.request_key)) or args.mode != 'fast'):
                    raise GMError('CPU_FIRST_FIT_ARGS_INVALID', 'Explicit CPU source/reference/bundle/manifest required', 2)
                from .cpu_first_fit import fit_cpu_recipe
                result = fit_cpu_recipe(Path(args.source),Path(args.reference),Path(args.store),Path(args.out),
                    bundle_directory=Path(args.cpu_recipe_out),request_key=args.request_key,mode=args.mode,
                    execution_backend=args.execution_backend,allow_experimental=args.experimental,
                    execution_manifest=Path(args.execution_manifest))
                _emit(result)
                return result.get('exit_code',0) or 0
            if not args.project:
                raise GMError('FIT_PROJECT_REQUIRED', 'Use --project for the existing fit path', 2)
        store = Store(Path(args.store), Path(sys.argv[0]))
        if args.command == "fit":
            from .gm004a_api import fit_new_pair
            from .resource_execution.api import cli_request
            result = fit_new_pair(Path(args.project), store.root, Path(args.out),
                                  request_key=args.request_key, profile=args.profile,
                                  execution_backend=args.execution_backend,allow_experimental=args.experimental,
                                  resource_request=cli_request(args.resource_policy,args.ram_limit_mib,args.commit_limit_mib,args.vram_limit_mib,args.cpu_core_rows))
            _emit(result)
            return result.get("exit_code", 0) or 0
        if args.command in {"result", "replay"}:
            from .gm004a_operations import OperationStore
            operations = OperationStore(store.root)
            if args.command == "replay" and (args.project or args.execution_manifest or args.execution_backend or args.experimental or args.resource_policy or
                    any(v is not None for v in (args.ram_limit_mib,args.commit_limit_mib,args.vram_limit_mib))):
                if args.job_id is not None or args.attempt is not None or not args.out:
                    raise GMError("REPLAY_ARGS_INVALID", "Project replay requires --project and --out only", 2)
                from .gm004a_api import replay
                from .resource_execution.api import cli_request
                resource_request=cli_request(args.resource_policy,args.ram_limit_mib,args.commit_limit_mib,args.vram_limit_mib,args.cpu_core_rows)
                if args.execution_backend is not None or args.execution_manifest is not None or args.experimental or resource_request is not None:
                    result = replay(Path(args.project) if args.project else None,store.root,Path(args.out),
                        request_key=args.request_key,execution_backend=args.execution_backend,
                        allow_experimental=args.experimental,
                        execution_manifest=Path(args.execution_manifest) if args.execution_manifest else None,
                        source_path=Path(args.source) if args.source else None,
                        reference_path=Path(args.reference) if args.reference else None,resource_request=resource_request)
                else:
                    if args.source or args.reference:
                        raise GMError('REPLAY_ARGS_INVALID','Locators require an execution manifest',2)
                    result = replay(Path(args.project),store.root,Path(args.out),request_key=args.request_key)
                _emit(result)
                return result.get("exit_code", 0) or 0
            if args.command == "replay" and (not args.job_id or not args.attempt):
                raise GMError("REPLAY_ARGS_INVALID", "Legacy replay requires job and attempt", 2)
            operation = False
            if args.command == "result":
                try:
                    operations.path(args.job_id, args.attempt)
                    operation = True
                except GMError as exc:
                    if exc.code != "ADDRESS_NOT_FOUND":
                        raise
            if operation:
                from . import gm004a_api
                if args.result_command == "status":
                    result = gm004a_api.result_status(store.root, args.job_id, args.attempt)
                else:
                    binding = strict_load(Path(args.binding))
                    if args.result_command == "preview":
                        if not args.out:
                            raise GMError("PREVIEW_OUTPUT_REQUIRED", "Use --out for an RGB8 JPG preview", 2)
                        roi = tuple(int(x) for x in args.roi.split(",")) if args.roi else None
                        result = gm004a_api.save_preview(store.root, args.job_id,
                            args.attempt, binding, Path(args.out), view=args.view,
                            alpha=args.alpha, max_side=args.max_side, roi=roi,
                            request_id=args.request_id)
                    elif args.result_command == "export":
                        result = gm004a_api.export(store.root, args.job_id,
                            args.attempt, binding, Path(args.out), alpha=args.alpha,
                            format=args.format, bit_depth=args.bit_depth,
                            icc_policy=args.icc_policy, request_key=args.request_key,
                            export_root=Path(args.export_root) if args.export_root else None)
                    else:
                        result = gm004a_api.release(store.root, args.job_id,
                            args.attempt, binding)
                _emit(result)
                return result.get("exit_code", 0) or 0
            from . import gm003_api
            if args.command == "replay":
                result = gm003_api.replay(store, args.job_id, args.attempt)
            elif args.result_command == "status":
                result = gm003_api.status(store, args.job_id, args.attempt)
            else:
                binding = strict_load(Path(args.binding))
                if args.result_command == "preview":
                    result = gm003_api.preview(store, args.job_id, args.attempt, binding, alpha=args.alpha)
                elif args.result_command == "export":
                    result = gm003_api.export(store, args.job_id, args.attempt, binding,
                                              alpha=args.alpha, output_path=Path(args.out))
                else:
                    result = gm003_api.release(store, args.job_id, args.attempt, binding)
            _emit(result)
            return result.get("exit_code", 0) or 0
        if args.command in {"status", "reconcile", "cancel", "events"}:
            from .gm004a_operations import OperationStore
            operations = OperationStore(store.root)
            try:
                operations.path(args.job_id, args.attempt)
            except GMError as exc:
                if exc.code != "ADDRESS_NOT_FOUND":
                    raise
            else:
                if args.command in {"status", "reconcile"}:
                    _emit(operations.status(args.job_id, args.attempt)); return 0
                if args.command == "cancel":
                    _emit(operations.cancel(args.job_id, args.attempt)); return 0
                after = args.after_seq
                while True:
                    for event in operations.events(args.job_id, args.attempt, after):
                        _emit(event); after = event["seq"]
                    if not args.follow or operations.status(args.job_id, args.attempt)["state"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                        break
                    time.sleep(.1)
                return 0
        if args.command in {"status", "reconcile"}:
            _emit(store.reconcile(args.job_id, args.attempt))
            return 0
        if args.command == "cancel":
            _emit(store.cancel(args.job_id, args.attempt))
            return 0
        if args.command == "events":
            if args.after_seq < 0:
                raise GMError("AFTER_SEQ_INVALID", "after-seq must be nonnegative")
            after = args.after_seq
            while True:
                for event in store.events(args.job_id, args.attempt, after):
                    _emit(event)
                    after = event["seq"]
                if not args.follow:
                    break
                status = store.reconcile(args.job_id, args.attempt)
                if status["state"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    for event in store.events(args.job_id, args.attempt, after):
                        _emit(event)
                    break
                time.sleep(.1)
            return 0
        raise GMError("COMMAND_INVALID", args.command)
    except GMError as e:
        if getattr(e, 'execution_backend', None) == 'r0-cpu-portable-v1':
            from .cpu_replay.runtime import stderr_error
            stderr_error(e)
        output={"error_code": e.code, "message": str(e), "exit_code": e.exit_code}
        if getattr(e,"adaptive_first",False):output.update(execution_stage=e.execution_stage,preserved_recipes=e.preserved_recipes,next_step=e.next_step)
        _emit(output)
        return e.exit_code
    except (OSError, ValueError) as e:
        if getattr(e, 'execution_backend', None) == 'r0-cpu-portable-v1':
            from .cpu_replay.runtime import stderr_error
            stderr_error(e)
        _emit({"error_code": "IO_ERROR", "message": str(e), "exit_code": 7})
        return 7
    finally:
        if instance_scope is not None:instance_scope.__exit__(None,None,None)
