"""Read-only authorization for the one claimed, unstarted C1 T4 operation."""
import hashlib
import json
from pathlib import Path

from ..cpu_replay.contract import need
from ..winproc import identity_alive
from . import portable_contract as portable
from .t34r3_c1_route import _read_bound

RESERVATION = "c975cc03-1370-4e66-ae89-abf78e945d75"
OPERATION = ("w/T4/store/gm004a-operations/7b82c6f1-6a6a-4f04-9642-fdaa58f85afe/"
             "3df1c0a5-e5dd-4db6-b947-66810392243f")
CONTEXT = "w/T4/.adaptive/contexts/4712beae-058b-4b47-a9d0-e5d67c017d31.json"
CONTROL = "w/T4/.adaptive/control/07c50c7ccb674d8db78cf1b429f881bb"
OUTPUT = "w/T4/operations/4712beae-058b-4b47-a9d0-e5d67c017d31"
FILES = {
    "window": "science/t34r3-c1-window.json",
    "check": "science/t34r3-c1-check.json",
    "ticket": "science/t34r3-c1-run-ticket-r1.json",
    "dispatch": "w/T4/.adaptive/c1/claim.json",
    "claim": OUTPUT + "/science-claim.json",
    "op_claim": OPERATION + "/scientific-reservation.json",
    "startup": OPERATION + "/startup.json",
    "spec": OPERATION + "/spec.json",
    "status": OPERATION + "/status.json",
    "op_events": OPERATION + "/events.jsonl",
    "failure": OPERATION + "/error.json",
    "wait": "runs/C1/T4/managed/wait.json",
    "control_wait": CONTROL + "/actual-wait.json",
    "control_failure": CONTROL + "/error.json",
    "outer_failure": "runs/C1/T4/managed/unit-failure.json",
}


def verify_frozen_pool_lock(root, pin):
    pool = portable.ancestors(root / "w/T4/pool")
    lock = portable.ancestors(pool / "admission.lock")
    need(pool.is_dir() and [child.name for child in pool.iterdir()] == ["admission.lock"] and lock.is_file(),
         "C3_HISTORY_POOL_FOOTPRINT")
    from ..cpu_replay.contract import binding
    need(binding(lock) == pin and lock.read_bytes() == b"0", "C3_HISTORY_POOL_LOCK")


def verify_history(route):
    root = route.root
    resume = route.a["resume"]
    need(set(resume) == {"files", "prior_events", "reservation"}
         and resume["reservation"] == RESERVATION
         and set(resume["files"]) == set(FILES), "C2_HISTORY_SCOPE")
    pins = resume["files"]
    rows = {key: _read_bound(pins[key], root / leaf)
            for key, leaf in FILES.items() if key != "op_events"}
    op_events = portable.ancestors(root / FILES["op_events"])
    from ..cpu_replay.contract import binding
    need(binding(op_events) == pins["op_events"], "C2_HISTORY_OPERATION_EVENTS")
    need([json.loads(line)["state"] for line in op_events.read_bytes().splitlines()]
         == ["QUEUED", "FAILED"], "C2_HISTORY_OPERATION_NOT_STARTED")
    raw = (root / "science/events.jsonl").read_bytes()
    prefix = resume["prior_events"]
    old, tail = raw[:prefix["bytes"]], raw[prefix["bytes"]:]
    need(prefix["path"] == str(root / "science/events.jsonl")
         and hashlib.sha256(old).hexdigest() == prefix["sha256"]
         and all(json.loads(line)["allocation"] == "T3" for line in old.splitlines())
         and len(tail.splitlines()) == 1, "C2_HISTORY_SCIENCE_PREFIX")
    event = json.loads(tail)
    claim = rows["claim"]
    need(claim == rows["op_claim"] ==
         {k: v for k, v in event.items() if k not in ("wall", "monotonic")}
         and claim["kind"] == "claim" and claim["allocation"] == "T4"
         and claim["phase"] == "refine" and claim["reservation"] == RESERVATION
         and claim["before_any_science"] is True and claim["budget"] == route.a["reservation"]["budget"]
         and claim["context"] == rows["dispatch"]["new_context"] == binding(portable.ancestors(root / CONTEXT))
         and claim["operation_path"] == str(root / OPERATION)
         and rows["dispatch"]["request_key"] == route.key
         and rows["dispatch"]["window"] == pins["window"]
         and rows["window"]["check"] == pins["check"]
         and rows["dispatch"]["science_budget"] == route.a["science_budget"],
         "C2_HISTORY_SINGLE_UNSTARTED_CLAIM")
    spec, start, status = rows["spec"], rows["startup"], rows["status"]
    who = spec["startup_owner"]
    wait, control = rows["wait"], rows["control_wait"]
    need(event["wall"] > spec["start_deadline_at"]
         and spec["semantics"]["instance_context"] == claim["context"]
         and spec["request_key"] == route.key
         and start == {"phase": "NOT_STARTED", "claimant": who}
         and status["state"] == "FAILED" and status["error_code"] == "INTERRUPTED"
         and status["exit_code"] == 9
         and rows["outer_failure"]["code"] == "OPERATION_OWNER_MISMATCH"
         and rows["failure"]["error"] == rows["control_failure"]["error"] == "Startup owner or deadline differs"
         and control["actual_control_handle_wait"] is True and control["tree_empty"] is True
         and control["exit_code"] == 9 and control["owner_handoff"] is None
         and all(control["identity"][k] == v for k, v in who.items()),
         "C2_HISTORY_STARTUP_FAILURE")
    need(wait["root_exit"] == 1 and wait["actual_members"] == len(wait["records"]) == 4
         and all(wait[key] is True for key in ("actual_all_wait", "actual_root_wait", "exact", "tree_empty"))
         and sum(all(row[k] == v for k, v in who.items()) and row["exit_code"] == 9
                 for row in wait["records"]) == 1
         and all(row["actual_handle_wait"] is True and row["job_member_verified"] is True
                 and identity_alive(row) is False for row in wait["records"]),
         "C2_HISTORY_ACTUAL_WAIT")
    if "pool_lock" in route.a:
        verify_frozen_pool_lock(root, route.a["pool_lock"])
    else:
        need(not portable.ancestors(root / "w/T4/pool").exists(), "C2_HISTORY_NEW_ARTIFACT", "w/T4/pool")
    for leaf in ("w/T4/exports", "x4", "r4", "w/T4/.adaptive/c1/result.json",
                 "runs/C1/T4/managed/result.json", OPERATION + "/session/session-spec.json",
                 OPERATION + "/session/owner.json", OPERATION + "/session/host-process.json",
                 OPERATION + "/commit.json"):
        need(not portable.ancestors(root / leaf).exists(), "C2_HISTORY_NEW_ARTIFACT", leaf)
    return claim
