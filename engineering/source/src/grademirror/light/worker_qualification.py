"""D2 qualification checks attached only to a caller-authorized frozen plan."""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import time
from pathlib import Path

from ..adaptive_execution.memory_snapshot import ram_facts
from ..common import atomic_json, strict_load
from ..cpu_replay.contract import need

_MIB = 2**20
_pending = ContextVar("qualification", default=None)


@contextmanager
def scope(authority):
    token = _pending.set(authority)
    try:
        yield
    finally:
        _pending.reset(token)


def plan_binding(plan, project, store, key):
    authority = _pending.get()
    if authority is None:
        return None
    contract = authority["contract"]
    identity = authority["identity"]
    from . import system
    from ..hybrid_replay.contract import execution_contract
    need(plan["selection"]["backend"] == "mixed" and plan["operation"] == "replay"
         and str(project) == identity["project"] and str(store) == identity["store"]
         and key == identity["key"] and plan["source"]["sha256"] == identity["source"]
         and plan["reference"]["sha256"] == identity["reference"]
         and system.manifest()["build"]["sha256"] == identity["candidate"]
         and execution_contract()[1] == identity["execution_contract"]
         and plan["device"]["uuid"] == identity["uuid"] and plan["device"]["driver_version"] == identity["driver"]
         and plan["selection"]["admission"]["startup"] == contract["startup"],
         "QUALIFICATION_PLAN_CHANGED")
    assets = identity["assets"]
    from ..common import sha_file
    for name, fingerprint in assets.items():
        path = Path(project)/name
        need(path.resolve().is_relative_to(Path(project)) and path.is_file()
             and sha_file(path)==fingerprint, "QUALIFICATION_INPUT_CHANGED")
    need(assets and type(identity["observer"]) is str, "QUALIFICATION_OBSERVER_BINDING")
    return authority


def effective_gpu_ready(proof, expected_uuid, minimum):
    from ..resource_policy import available_gpu_bytes
    free = available_gpu_bytes(proof["raw_free"], proof["nvml"])
    correct = proof["raw_uuid"] == proof["nvml"]["uuid"] == expected_uuid
    return correct and free >= minimum, free, correct


def _host_facts(limits):
    snapshot = ram_facts()
    ram = snapshot["physical"]["available_bytes"]
    commit = snapshot["commit"]["headroom_bytes"]
    need(type(ram) is int and type(commit) is int and ram >= limits["ram"]
         and commit >= limits["commit"], "QUALIFICATION_HOST_RECHECK")
    return dict(ram=ram, commit=commit)


@contextmanager
def worker(role, plan, args):
    """Check host before codec work and same-device effective GPU before model load."""
    qualification = (plan or {}).get("gm086_qualification")
    relevant = role == "host" or role == "mixed" and args[0] == "R0"
    if qualification is None or not relevant:
        yield
        return
    from .resources import demand
    deadline = qualification["expires"]
    need(type(deadline) in (int, float) and math.isfinite(deadline)
         and time.time() < deadline, "QUALIFICATION_TICKET_EXPIRED")
    contract = qualification["contract"]
    startup = contract["startup"]
    cancel = Path(qualification["identity"]["store"]).parent/"cancel.signal"
    need(not cancel.exists(), "CANCELLED", exit_code=8)
    if role == "host":
        facts = _host_facts(dict(ram=startup["ram"] + contract["ram_margin"], commit=startup["commit"]))
        lo, hi = contract["commit_band"]
        need(lo <= facts["commit"] <= hi, "QUALIFICATION_COMMIT_WINDOW_CHANGED")
        yield
        return
    payload = Path(args[-1])
    run = payload.parent
    need(not cancel.exists() and not (run/"cancel.signal").exists(), "CANCELLED", exit_code=8)
    # The existing worker constructs its own context in gpu(); wrap the result
    # so the original GPU guard still runs once, before the model is loaded.
    from ..hybrid_fit import worker as mixed
    original = mixed.gpu

    def verify(bound, out):
        facts = _host_facts(demand(plan, "R0"))
        atomic_json(out/"qualification-recheck.json", dict(state="CONTEXT_ENTRY", facts=facts,
                    context_count="UNKNOWN_UNTIL_ADMISSION", model_load=0))
        original(bound, out)
        proof = strict_load(out/"gpu-admission.json")
        ready, free, correct = effective_gpu_ready(proof, plan["device"]["uuid"],
                                                   startup["vram"] + contract["vram_margin"])
        atomic_json(out/"qualification-recheck.json",dict(state="PASS" if ready else "REFUSED",
                    facts=facts,context_count=1,model_load=0,effective_free=free,device_match=correct))
        need(ready, "QUALIFICATION_EFFECTIVE_VRAM")
        need(not (run/"cancel.signal").exists(), "CANCELLED", exit_code=8)

    mixed.gpu = verify
    try:
        yield
    finally:
        mixed.gpu = original
