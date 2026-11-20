"""Evaluate versioned preview candidates with original stage envelopes."""
import math
import os
import time
import uuid
from pathlib import Path

from ..common import GMError, atomic_json, strict_load
from ..cpu_replay.contract import digest, need
from ..adaptive_execution.memory_snapshot import ram_facts
from . import resources
from . import runtime as rt
from .system import signed, system_scope, windows_root, manifest


def nvidia_tool():
    windows=windows_root()
    path=windows/"System32/nvidia-smi.exe"
    if not path.is_file():
        root=windows/"System32/DriverStore/FileRepository"
        candidates=sorted(p for p in root.glob("nv*/nvidia-smi.exe") if p.is_file())
        need(len(candidates)==1,"LIGHT_NVIDIA_TOOL_UNKNOWN")
        path=candidates[0].resolve()
    need(system_scope(path,windows)=="nvidia" and signed(path,"nvidia"),"LIGHT_NVIDIA_TOOL_UNVERIFIED")
    return str(path)


def candidates(source,reference,quality,operation,env,facts,device,producer=None,*,qualification=None):
    """Read-only, finite and deterministic. UNKNOWN remains a refusal."""
    resources.candidate_backend(qualification)
    physical=facts["physical"].get("available_bytes")
    commit=facts["commit"].get("headroom_bytes")
    rows=[]
    for kind in ("gpu","cpu","mixed"):
        reasons=[]
        profile="cpu" if kind=="cpu" else "cu124"
        role=env.get(profile)
        if not role:
            reasons.append("MISSING_"+profile.upper())
        if kind!="cpu" and (not role or not (role.get("gpu") or {}).get("available") or device is None):
            reasons.append("NVIDIA_CAPABILITY_UNAVAILABLE")
        if kind=="mixed" and (quality!="fine" or operation=="refine"):
            reasons.append("MIXED_OPERATION_UNQUALIFIED")
        if kind=="mixed" and not (producer=="mixed" or operation=="fit"):
            reasons.append("MIXED_RECIPE_REQUIRED")
        if quality=="fine" and operation!="replay" and "solver" not in env:
            reasons.append("MISSING_FINE_SOLVER")
        if (operation in ("replay","refine") or producer is not None) and producer!=kind:
            reasons.append("RECIPE_BACKEND_MISMATCH")
        # A mixed new fit is a distinct result contract. Explicit preference is
        # required before selecting it in place of the single-backend fine path.
        cap = None
        stages = []
        try:
            if not reasons:
                cap = resources.capacity(kind, source, reference, quality, operation)
                stages = cap["stages"]
        except (GMError, ValueError, KeyError) as error:
            reasons.append(getattr(error, "code", "GEOMETRY_OR_CAPACITY_UNQUALIFIED"))
        for row in stages:
            if type(physical) is not int or type(commit) is not int:
                reasons.append("RAM_OR_COMMIT_UNKNOWN")
            if type(physical) is int and physical<row["ram"]:
                reasons.append("RAM:"+row["stage"])
            if type(commit) is int and commit<row["commit"]:
                reasons.append("COMMIT:"+row["stage"])
            if row["vram"] and (device["free_mib"]*1024**2<row["vram"] or
                                row["reserved"]*5>device["total_mib"]*1024**2*4):
                reasons.append("VRAM:"+row["stage"])
        rows.append(dict(backend=kind, profile=profile, stages=stages,
                         admission=resources.envelope(cap) if cap else None,
                         geometry=cap["geometry"] if cap else None,
                         objects=cap["objects"] if cap else None,
                         jobs=resources.job_limits(cap) if cap else None,
                         reasons=sorted(set(reasons)), comparable_eta=None,
                         effect_bridge="PRODUCER_COMPATIBLE_ONLY" if producer else "EXTERNAL_BRIDGE_PENDING"))
    return rows


def saved_timing_scope(source, rows, env):
    """Audit each saved GM063 component scope before ranking preview routes."""
    from .system import packaged
    if rt.portable.enabled():
        for row in rows:
            row["saved_timing"] = dict(status="PORTABLE_RUNTIME_UNQUALIFIED")
        return
    value=strict_load(packaged(rt.ROOT/"eta-components.json"))
    labels={"cpu":"C","gpu":"G","mixed":"H"}
    for row in rows:
        matched=next((prior for prior in value["rows"] if prior["backend"]==labels[row["backend"]]
                      and all(prior[key]==source[key] for key in ("width","height","bits"))),None)
        runtime=env.get(row["profile"],{})
        if not matched:
            status="NO_MATCHING_SAVED_GEOMETRY"
        elif runtime.get("inventory_sha256")!=matched["runtime"]:
            status="SAVED_RUNTIME_UNBRIDGED"
        else:
            status="SCOPED_COMPONENT_ONLY"
        row["saved_timing"]=dict(status=status,source_sha256=value["source_sha256"])

def device_status(env):
    from .devices import present_nvidia, snapshot
    if "cu124" not in env:
        return dict(state="CPU_EDITION", device=None, source="bundle manifest")
    try:
        if not present_nvidia():
            return dict(state="NO_NVIDIA", device=None, source="Windows SetupAPI")
        if not (env["cu124"].get("gpu") or {}).get("available"):
            return dict(state="LIGHT_NVIDIA_NO_SUPPORTED_DEVICE", device=None, source="bundle manifest")
        devices=snapshot(nvidia_tool(),env["cu124"],all_devices=True)
        device=min(devices,key=lambda row:(-row["free_mib"],-row["total_mib"],row["uuid"].lower()))
        return dict(state="NVIDIA_DEVICE_SELECTED", device=device, devices=devices, source="Windows SetupAPI + signed NVIDIA query")
    except (GMError,OSError) as error:
        return dict(state=getattr(error,"code","LIGHT_DEVICE_ENUMERATION_FAILED"),
                    device=None, message=str(error), source="Windows SetupAPI / signed NVIDIA query")


def _gpu_shortfall(row, device):
    """Rank cards by the stage envelope, then by currently available headroom."""
    unit = 1024**2
    peak = (row.get("admission") or {}).get("startup", {})
    free = device["free_mib"] * unit
    cap = device["total_mib"] * unit * 4 // 5
    gap = max(0, peak.get("vram", 0) - free, peak.get("reserved", 0) - cap)
    return (gap, -free, -cap, device["uuid"].lower())


def route_decision(rows, detection, producer, prefer):
    by_backend={row["backend"]:row for row in rows}
    if producer and prefer not in ("auto",producer):
        return None,"RECIPE_BACKEND_MISMATCH"
    if producer or prefer!="auto":
        requested=by_backend[producer or prefer]
        if not requested["reasons"]:
            return requested,"SELECTED"
        return None, refusal_reason(requested, detection)
    gpu,cpu=by_backend["gpu"],by_backend["cpu"]
    if not gpu["reasons"]:
        return gpu,"GPU_PREFERRED"
    if detection not in ("CPU_EDITION","NO_NVIDIA","LIGHT_NVIDIA_NO_SUPPORTED_DEVICE","NVIDIA_DEVICE_SELECTED"):
        return None,"GPU_DETECTION_FAILED"
    if gpu["stages"] and all(reason.startswith(("RAM:","COMMIT:","VRAM:")) or reason=="RAM_OR_COMMIT_UNKNOWN"
                              for reason in gpu["reasons"]):
        return None,"GPU_RESOURCES"
    return (None,refusal_reason(gpu if detection=="NVIDIA_DEVICE_SELECTED" else cpu,detection)) if cpu["reasons"] else (cpu,detection if detection!="NVIDIA_DEVICE_SELECTED" else "GPU_OPERATION_UNAVAILABLE")


def refusal_reason(row, detection):
    reasons=row["reasons"]
    if "RECIPE_BACKEND_MISMATCH" in reasons:
        return "RECIPE_BACKEND_MISMATCH"
    if row["backend"]!="cpu" and detection not in ("CPU_EDITION","NO_NVIDIA","LIGHT_NVIDIA_NO_SUPPORTED_DEVICE","NVIDIA_DEVICE_SELECTED"):
        return "GPU_DETECTION_FAILED"
    if "RAM_OR_COMMIT_UNKNOWN" in reasons:
        return "RESOURCE_DETECTION_UNKNOWN"
    if any(reason.startswith(("RAM:","COMMIT:","VRAM:")) for reason in reasons):
        return "ROUTE_RESOURCES"
    return "ROUTE_UNSUPPORTED"


class RouteRefusal(GMError):
    def __init__(self, value):
        state=value["selection_basis"]
        code="NEEDS_RESOURCE_CHOICE" if state in ("GPU_RESOURCES","GPU_DETECTION_FAILED") else "LIGHT_NO_COMPATIBLE_ROUTE"
        super().__init__(code, code, 5)
        self.routing=dict(reason=state, device=value["device_state"], candidates=value["candidates"],
                          resources=value["resources"], can_choose_cpu=value["can_choose_cpu"])


def require_selection(value):
    if value["selection"] is None:
        raise RouteRefusal(value)


def plan(source,reference,quality,operation,env,producer=None,*,prefer="auto",qualification=None,saved_gpu_post=None):
    need(prefer in ("auto","gpu","cpu","mixed"),"LIGHT_BACKEND_PREFERENCE")
    status=device_status(env)
    device,detection=status["device"],status["state"]
    facts=ram_facts()
    sampled_at=time.time()
    if saved_gpu_post is not None:
        need((operation,quality,producer)==("refine","fine","gpu") and prefer in ("auto","gpu"),"LIGHT_SAVED_POST_SCOPE")
    devices=status.get("devices") or [device]
    by_device=[candidates(source,reference,quality,operation,env,facts,card,producer,qualification=qualification)
               for card in devices]
    rows=by_device[0]
    if device is not None:
        for index in (0,2):  # GPU and mixed have separate stage envelopes.
            row,card=min(((options[index],card) for options,card in zip(by_device,devices)),
                         key=lambda item:_gpu_shortfall(*item))
            rows[index]=dict(row,device=card)
    if device is None and detection not in ("CPU_EDITION","NO_NVIDIA"):
        for row in rows:
            if row["backend"]!="cpu":
                row["reasons"]=sorted({*row["reasons"],detection})
    saved_timing_scope(source,rows,env)
    selected,basis=route_decision(rows,detection,producer,prefer)
    report_row=selected if selected and selected.get("device") else rows[2 if (producer or prefer)=="mixed" else 0]
    device=report_row.get("device",device)
    status=dict(status,device=device)
    available=dict(ram=facts["physical"].get("available_bytes"),commit=facts["commit"].get("headroom_bytes"),
                   vram=device["free_mib"]*1024**2 if device else None)
    for row in rows:
        row["shortages"]=[]
        card=row.get("device",device)
        row_available=dict(available,vram=card["free_mib"]*1024**2 if card else None)
        for phase in row["stages"]:
            for kind,current in row_available.items():
                required=phase[kind]
                if required and (type(current) is not int or current<required):
                    row["shortages"].append(dict(resource=kind,stage=phase["stage"],available=current,
                                                   required=required,gap=required-current if type(current) is int else None,
                                                   sampled_at=sampled_at))
            if card and phase["reserved"]*5>card["total_mib"]*1024**2*4:
                cap=card["total_mib"]*1024**2*4//5
                row["shortages"].append(dict(resource="vram_cap",stage=phase["stage"],available=cap,
                                               required=phase["reserved"],gap=phase["reserved"]-cap,sampled_at=sampled_at))
    return dict(schema="gm066-preview-plan-v1",version=rt.PLAN_VERSION,quality=quality,operation=operation,
                source=source,reference=reference,selection=selected,candidates=rows,device=device,device_detection=detection,
                device_state=status,resources=available,preference=prefer,producer=producer,capacity_qualification=qualification,
                saved_gpu_post=saved_gpu_post,can_choose_cpu=producer is None and prefer!="cpu" and any(row["backend"]=="cpu" and not row["reasons"] for row in rows),
                selection_basis=basis,created=time.monotonic())


def freeze(value,env,project,store,key):
    from ..winproc import current_identity
    require_selection(value)
    row=value["selection"]
    resources.selected(value)
    need(0<=time.monotonic()-value["created"]<=10,"LIGHT_PLAN_STALE")
    profiles={row["profile"]}
    if value["quality"]=="fine" and value["operation"]!="replay":
        profiles.add("solver")
    for profile in profiles:
        rt.verify_role(env[profile],profile)
    base=rt.data_root()/"plans";base.mkdir(parents=True,exist_ok=True)
    p=base/(uuid.uuid4().hex+".json")
    context=dict(value,environment={profile:env[profile] for profile in profiles},plan_path=str(p),
                 client=current_identity(),project=str(project),store=str(store),request_key=key,
                 numeric_profile=row["profile"],build=manifest()["build"]["sha256"])
    try:
        from .resource_shadow import from_plan
        context["resource_shadow"] = from_plan(context)
    except Exception:
        # Diagnostic-only. A shadow calculation must not change admission.
        context["resource_shadow"] = dict(schema="gm084-resource-shadow-v2", state="UNAVAILABLE")
    from .worker_qualification import plan_binding
    qualification = plan_binding(context, project, store, key)
    if qualification is not None:
        need(context["resource_shadow"]["model_id"] == qualification["identity"]["model"],
             "QUALIFICATION_MODEL_CHANGED")
        context["gm086_qualification"] = qualification
    context["plan_id"]=digest(context)
    atomic_json(p,context)
    return context
