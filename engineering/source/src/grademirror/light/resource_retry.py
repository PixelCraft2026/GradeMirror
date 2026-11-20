"""An explicit second attempt after a proved pre-science resource refusal."""
import time
from pathlib import Path

from ..common import GMError, atomic_json, strict_load
from ..cpu_replay.contract import need


RECEIPT = "light-resource-refusal.json"
CLAIM = "light-resource-retry.json"


def pre_probe(facts, ram_floor, commit_floor, active, work, *, locked, sampled_at, sampled_monotonic):
    ram=facts["physical"]["available_bytes"]
    commit=facts["commit"]["headroom_bytes"]
    if type(ram) is int and type(commit) is int and ram>=ram_floor and commit>=commit_floor:
        return
    error=InnerResource("PREVIEW_PROBE_MEMORY_NOT_READY","读取照片前的内存或提交余量未达要求。",
                        "pre_probe",[("ram",ram,ram_floor),("commit",commit,commit_floor)],
                        reason="RESOURCE_UNKNOWN" if type(ram) is not int or type(commit) is not int else "CAPACITY_CHANGED")
    proof=error.inner_resource
    proof.update(sampled_at=sampled_at,sampled_monotonic=sampled_monotonic,decision_monotonic=time.monotonic(),
                 cpu_key=work.name,request_key=active["request_key"],project=active["project"],
                 plan_id=active["plan_id"],intent=intent(active),operation=active["operation"],
                 qualification=bool(active.get("validation_evidence")),pool_token=None)
    missing=False
    if locked:
        try:
            work.lstat()
        except FileNotFoundError:
            missing=True
        except OSError:
            pass
    if missing:
        proof.update(science="NOT_STARTED",cleanup="COMPLETE",attempt_id=None,job_id=None,
                     lifecycle=dict(terminal="NOT_CREATED",probe="NOT_CREATED",owner="NOT_CREATED",
                                    reservation="NOT_CREATED",pool="NOT_CLAIMED",lease="NOT_CREATED",wait=None))
    path=Path(active["store"])/f"pre-probe-{work.name}.json"
    proof["evidence"]=str(path)
    atomic_json(path,proof)
    raise error


class ResourceChoice(GMError):
    def __init__(self, receipt):
        routing=receipt.get("routing")
        code="NEEDS_RESOURCE_CHOICE" if routing is not None else receipt["inner_resource"]["code"]
        super().__init__(code,receipt["message"],5)
        self.inner_resource=receipt["inner_resource"]
        if routing is not None:
            self.routing=routing


class InnerResource(GMError):
    def __init__(self, code, message, stage, samples, *, reason="CAPACITY_CHANGED"):
        super().__init__(code, message, 5)
        self.inner_resource = dict(code=code, stage=stage, sampled_at=time.time(), reason=reason,
                                   shortages=[dict(stage=stage, resource=resource, available=available,
                                                   required=required, gap=max(0, required-available)
                                                   if type(available) is int else None)
                                              for resource, available, required in samples],
                                   science="UNKNOWN", cleanup="UNKNOWN")

    def stopped(self, path, spec, **lifecycle):
        self.inner_resource.update(science="NOT_STARTED", cleanup="COMPLETE", operation_path=str(path),
                                   job_id=spec["job_id"], attempt_id=spec["attempt_id"], lifecycle=lifecycle)


def cpu_stopped(error, path, spec, process, state):
    """The synchronous admission branch is before reserve, pool claim and owner launch."""
    if not isinstance(error, InnerResource) or error.inner_resource["stage"] != "cpu_inner_admission":
        return
    startup = strict_load(path/"startup.json")
    if (process is None and startup["phase"]=="NOT_STARTED" and not startup.get("pool_token")
            and state["state"]=="FAILED" and state["cleanup"]==dict(complete=True,no_reservation_created=True)
            and not (path/"scientific-reservation.json").exists()
            and not (path/"session/owner-launch.json").exists()
            and not (path/"session/compute-go.json").exists()):
        error.stopped(path,spec,owner_created=False,tree_empty=True,lease_created=False,
                      pool="NOT_CLAIMED",terminal=state["state"])


def gpu_stopped(error, path, spec, session, owner, host, released, state):
    """Called only after terminal transition and normal claim/lock unwinding."""
    if (isinstance(error, InnerResource) and session is None and owner is None and host is None
            and state["state"]=="FAILED" and (released is None or released["state"]=="FREE")
            and not (path/"session").exists() and not (path/"compute-result.json").exists()):
        error.stopped(path,spec,owner_created=False,tree_empty=True,lease_created=False,
                      pool="NOT_CLAIMED" if released is None else "FREE",
                      token=released["token"] if released else None,terminal=state["state"])


def intent(plan):
    return dict(build=plan["build"],source=plan["source"]["sha256"],reference=plan["reference"]["sha256"],
                quality=plan["quality"],operation=plan["operation"],producer=plan["producer"])


def prior_intent(path, root, store, key, project, operation, plan):
    prior = Path(path).resolve()
    need(prior.name==RECEIPT and prior.parent.is_relative_to(root/"jobs") and prior.parent!=store,
         "LIGHT_RETRY_SCOPE")
    previous = strict_load(prior.parent/"light-route.json")
    result = strict_load(prior)
    proof = result["inner_resource"]
    need("routing" in result and result["plan_id"]==previous["plan_id"] and result["intent"]==intent(plan)
         and previous["project"]==str(project) and previous["operation"]==operation and previous["key"]!=key
         and proof["science"]=="NOT_STARTED" and proof["cleanup"]=="COMPLETE"
         and not (prior.parent/"light-cancel.json").exists(), "LIGHT_RETRY_PREDECESSOR_UNPROVED")
    if proof["stage"]=="pre_probe":
        evidence=prior.parent/f"pre-probe-{previous['cpu_key']}.json"
        saved=strict_load(evidence)
        need(proof["attempt_id"] is None and proof["job_id"] is None and proof["evidence"]==str(evidence)
             and saved["plan_id"]==previous["plan_id"] and saved["request_key"]==previous["key"]
             and saved["intent"]==result["intent"] and saved["lifecycle"]==proof["lifecycle"]
             and saved["lifecycle"]["terminal"]=="NOT_CREATED" and saved["science"]=="NOT_STARTED"
             and saved["cleanup"]=="COMPLETE", "LIGHT_RETRY_PREPROBE_UNPROVED")
    claim = prior.parent/CLAIM
    need(not claim.exists(), "LIGHT_RETRY_ALREADY_USED")
    atomic_json(claim,dict(previous_key=previous["key"],next_key=key,next_store=str(store),at=time.time()))
    return dict(previous_store=str(prior.parent),previous_key=previous["key"],previous_attempt=proof["attempt_id"])
