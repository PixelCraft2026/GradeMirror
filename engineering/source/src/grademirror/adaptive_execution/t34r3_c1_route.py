"""Versioned same-key route for the one pre-dispatch GM064 T4 continuation."""
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from ..common import canonical_json
from ..cpu_replay.contract import ROOT, binding, need
from ..store import locked
from . import portable_contract as portable
from .intent import digest

KEY = "GM064-T4"
SESSION = "3a0e7df26bb1438197b6dfb40b3c640e"
QUALIFICATION = "T4-C1.json"
AUTHORITY = "validation/t34r3-c1-route.json"
CONTROLLER = "validation/t34r3-c1-unit.py"
RESUME_QUALIFICATION = "T4-C2.json"
POOL_RESUME_QUALIFICATION = "T4-C3.json"
RESUME_QUALIFICATIONS = (RESUME_QUALIFICATION, POOL_RESUME_QUALIFICATION)
QUALIFICATIONS = (QUALIFICATION, *RESUME_QUALIFICATIONS)
RESUME_AUTHORITY = "validation/t34r3-c2-route.json"
RESUME_CONTROLLER = "validation/t34r3-c2-unit.py"
POOL_RESUME_AUTHORITY = "validation/t34r3-c3-route.json"
POOL_RESUME_CONTROLLER = "validation/t34r3-c3-unit.py"


def route_workspace(root, qualification):
    return root / ("w/C4" if Path(qualification).name in RESUME_QUALIFICATIONS else "w/T4")


def route_prefix(qualification):
    name = Path(qualification).name
    if name == POOL_RESUME_QUALIFICATION:
        return "c3"
    return "c2" if name == RESUME_QUALIFICATION else "c1"


FIRST_WALL = 1793788456.605
LAST_WALL = FIRST_WALL + 168 * 3600
MIN_START = 14850


def _read_bound(row, path):
    portable.ancestors(path)
    need(row == binding(path), "C1_ROUTE_BOUND_FILE", str(path))
    return portable.read(path)


def _new_file(path, value):
    with path.open("xb") as stream:
        stream.write(canonical_json(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def selected(q, workspace, key):
    if q.get("task") != "GM-064" or Path(q["manifest_path"]).name not in QUALIFICATIONS:
        return None
    return Route(q, Path(workspace), key)


class Route:
    def __init__(self, q, workspace, key):
        self.q = q
        self.root = portable.session(q["manifest_path"])
        self.workspace = workspace
        self.key = key
        self.resuming = Path(q["manifest_path"]).name in RESUME_QUALIFICATIONS
        self.prefix = route_prefix(q["manifest_path"])
        self.qualification = self.root / "qualifications" / Path(q["manifest_path"]).name
        need(self.root.name == SESSION and workspace == route_workspace(self.root, self.qualification) and key == KEY
             and Path(q["manifest_path"]) == self.qualification
             and q["phase"] == "native" and q["domain"] == "24mp"
             and q["allocations"] == {"refine": "T4"}, "C1_ROUTE_SCOPE")
        authorities = {"c1": AUTHORITY, "c2": RESUME_AUTHORITY, "c3": POOL_RESUME_AUTHORITY}
        controllers = {"c1": CONTROLLER, "c2": RESUME_CONTROLLER, "c3": POOL_RESUME_CONTROLLER}
        manifest_authority = portable.package_member(authorities[self.prefix])
        self.authority = binding(manifest_authority)
        need(q["recovery"] == self.authority, "C1_ROUTE_AUTHORITY")
        a = _read_bound(self.authority, manifest_authority)
        need(set(a) == {"schema", "old_qualification", "old_request", "old_selection",
                        "old_context", "old_wait", "old_window", "science_budget",
                        "science_events", "reservation", "new_context_id"} |
             ({"resume"} if self.resuming else set()) |
             ({"pool_lock"} if self.prefix == "c3" else set())
             and a["schema"] == f"gm064-t34r3-{self.prefix}-route-v1", "C1_ROUTE_AUTHORITY_FIELDS")
        self.a = a
        self.directory = portable.ancestors(workspace / f".adaptive/{self.prefix}")
        self.selection = self.directory / "selection.json"
        self.request = self.directory / "request.json"
        self.claim = self.directory / "claim.json"
        self.result = self.directory / "result.json"
        self.controller = binding(portable.package_member(controllers[self.prefix]))
        slot = digest(KEY) + ".json"
        old_qualification = self.root / "qualifications/T4.json"
        old_q = _read_bound(a["old_qualification"], old_qualification)
        self.old_qualification = old_q
        original_work = self.root / "w/T4"
        old_request = _read_bound(a["old_request"], original_work / ".adaptive/requests" / slot)
        old_selection = _read_bound(a["old_selection"], original_work / ".adaptive/selections" / slot)
        old_context_path = Path(a["old_context"]["path"])
        need(old_context_path.parent == original_work / ".adaptive/contexts", "C1_ROUTE_OLD_CONTEXT_PATH")
        old_c = _read_bound(a["old_context"], old_context_path)
        old_index = portable.read(original_work / ".adaptive/context-index.json")
        need(old_index.get(old_c["id"]) == a["old_context"], "C1_ROUTE_OLD_CONTEXT_INDEX")
        changed = {"manifest_path", "runtime", "executor_build", "recovery"}
        need(set(q) == set(old_q) and all(q[k] == old_q[k] for k in old_q.keys() - changed)
             and old_request == dict(request_key=KEY, intent_id=digest(q["request"]),
                                     context=str(old_context_path))
             and old_selection["request_key"] == KEY and old_selection["request"] == q["request"]
             and old_selection["qualification"] == old_c["qualification"] == a["old_qualification"]
             and old_c["request"] == q["request"] and old_c["resource_selection"] == a["old_selection"]
             and old_c["science_allocation"] == "T4", "C1_ROUTE_INTENT")
        identifier = uuid.UUID(a["new_context_id"])
        need(identifier.version == 4 and str(identifier) == a["new_context_id"]
             and str(identifier) != old_c["id"], "C1_ROUTE_CONTEXT_ID")
        self.context_id = str(identifier)
        self.old_id = old_c["id"]
        need(a["reservation"]["budget"] == old_c["science"], "C1_ROUTE_BUDGET")
        self.old_window = _read_bound(a["old_window"], self.root / "science/t34r3-window.json")
        need(self.old_window["session"] == str(self.root)
             and self.old_window["deadline_at"] == 1794086482.185, "C1_ROUTE_OLD_WINDOW")
        self.window_path = self.science_path("window")
        self.qualification_index = self.science_path("qualification-index")
        self.runtime_inventory = self.science_path("runtime-inventory")

    def science_path(self, leaf):
        return self.root / f"science/t34r3-{self.prefix}-{leaf}.json"

    def run_directory(self, leaf):
        return self.root / "runs" / f"{self.prefix.upper()}-{leaf}" / "managed"

    def verify_inventory(self):
        """The continuation can replace only exact package members in the old inventory."""
        prior = _read_bound(self.old_qualification["runtime"]["inventory"],
                            self.root / "runtime-inventory.json")
        current = _read_bound(self.q["runtime"]["inventory"], self.runtime_inventory)
        manifest_path = ROOT / "portable-manifest.json"
        manifest = portable.read(manifest_path)
        expected = {os.path.normcase(row["path"]): row for row in prior["files"]}
        need(len(expected) == len(prior["files"]) and prior["package_manifest"] == self.old_window["manifest"],
             "C1_ROUTE_OLD_RUNTIME")
        for item in manifest["files"]:
            leaf = ROOT / item["path"]
            expected[os.path.normcase(str(leaf))] = dict(path=str(leaf), bytes=item["bytes"], sha256=item["sha256"])
        actual = {os.path.normcase(row["path"]): row for row in current["files"]}
        need(set(current) == {"schema", "package_manifest", "parent_inventory", "files"}
             and current["schema"] == f"gm064-t34r3-{self.prefix}-runtime-inventory-v1"
             and current["parent_inventory"] == self.old_qualification["runtime"]["inventory"]
             and current["package_manifest"] == binding(manifest_path)
             and len(actual) == len(current["files"]) and actual == expected
             and actual.get(os.path.normcase(self.controller["path"])) == self.controller,
             "C1_ROUTE_RUNTIME_INVENTORY")

    def clock(self, now, minimum=0):
        portable.ancestors(self.window_path)
        v = portable.read(self.window_path)
        need(set(v) == {"schema", "authority", "qualification", "controller", "check", "nonce",
                        "started_at", "deadline_at"}
             and v["schema"] == f"gm064-t34r3-{self.prefix}-window-v1"
             and v["authority"] == self.authority and v["controller"] == self.controller
             and v["qualification"] == binding(self.qualification)
             and isinstance(v["nonce"], str) and re.fullmatch(r"[0-9a-f]{32}", v["nonce"])
             and v["nonce"] != self.old_window["nonce"]
             and (not self.resuming or v["nonce"] != _read_bound(
                 self.a["resume"]["files"]["window"], self.root/"science/t34r3-c1-window.json")["nonce"])
             and FIRST_WALL <= v["started_at"] <= now <= v["deadline_at"]
             and v["deadline_at"] == v["started_at"] + 28800
             and min(LAST_WALL, v["deadline_at"]) - now >= minimum, "C1_ROUTE_WINDOW")
        check = _read_bound(v["check"], self.science_path("check"))
        self.verify_check(check)
        return min(LAST_WALL, v["deadline_at"])

    def verify_check(self, check):
        need(set(check) == {"schema", "manifest", "qualification", "controller", "wait",
                            "entry", "result", "launch", "science"}
             and check["schema"] == f"gm064-t34r3-{self.prefix}-target-check-v1"
             and check["manifest"] == binding(ROOT / "portable-manifest.json")
             and check["qualification"] == binding(self.qualification) and check["controller"] == self.controller
             and type(check["science"]) is int and check["science"] == 0, "C1_ROUTE_TARGET_CHECK")
        check_dir = self.run_directory("Check")
        wait = _read_bound(check["wait"], check_dir / "wait.json")
        entry = _read_bound(check["entry"], check_dir / "entry.json")
        result = _read_bound(check["result"], check_dir / "result.json")
        launch = _read_bound(check["launch"], check_dir / "launch.json")
        interpreter = self.q["interpreters"]["control"]
        argv = [interpreter["path"], "-I", "-B", self.controller["path"],
                str(check_dir), str(self.qualification), "--check"]
        need(entry["role"] == "GM064_fixed_unit_controller" and entry["unit"] == "T4"
             and result["status"] == "GM064_C1_CHECK_PENDING_ACTUAL_WAIT"
             and all(result[k] == check[k] for k in ("manifest", "qualification", "controller"))
             and result["argv"] == argv[3:] and result["interpreter"] == interpreter
             and result["science"] == result["dispatch"] == 0 and len(result["dlls"]) == 3
             and launch["argv"] == argv and launch["before_CreateProcess"] is True
             and launch["diagnostic_only"] is True, "C1_ROUTE_TARGET_CHECK_ENTRY")
        who = entry["identity"]
        need(wait["root_exit"] == 0 and wait["error"] is None and wait["cleanup_errors"] == []
             and all(wait[k] is True for k in ("actual_all_wait", "actual_root_wait", "exact", "tree_empty"))
             and wait["actual_members"] == len(wait["records"]) > 0
             and set(who) == {"pid", "creation_time", "image_path"}
             and len([r for r in wait["records"] if all(r[k] == who[k] for k in who)]) == 1
             and Path(who["image_path"]) == Path(interpreter["path"])
             and all(r["actual_handle_wait"] is True and r["job_member_verified"] is True
                     and r["exit_code"] == 0 for r in wait["records"]), "C1_ROUTE_TARGET_CHECK_WAIT")

    def _saved_claim(self, c):
        need(c["id"] == self.context_id and c["qualification"] == binding(self.qualification)
             and c["request"] == self.q["request"] and c["workspace"] == str(self.workspace)
             and Path(c["manifest_path"]) == self.workspace / ".adaptive/contexts" / (self.context_id + ".json")
             and c["science_allocation"] == "T4" and c["science"] == self.a["reservation"]["budget"],
             "C1_ROUTE_DISPATCH_CONTEXT")
        row = dict(schema=f"gm064-t34r3-{self.prefix}-supersedes-before-dispatch-v1",
                   request_key=self.key, authority=self.authority, old_context=self.a["old_context"],
                   new_context=binding(c["manifest_path"]), controller=self.controller,
                   window=binding(self.window_path), science_budget=self.a["science_budget"])
        if self.resuming:
            row["previous_dispatch"] = self.a["resume"]["files"]["dispatch"]
            row["previous_science_claim"] = self.a["resume"]["files"]["claim"]
        return row

    def verify_unclaimed(self):
        """Caller holds the science ledger lock until it records its claim."""
        book = portable.ancestors(self.root / "science-budget.jsonl")
        events = portable.ancestors(self.root / "science/events.jsonl")
        for path, pin in ((book, "science_budget"), (events, "science_events")):
            need(binding(path) == self.a[pin], "C1_ROUTE_PRIOR_LEDGER", path.name)
        rows = [json.loads(line) for line in book.read_bytes().splitlines()]
        need([row["id"] for row in rows] == ["T3", "T4"] and rows[-1] == self.a["reservation"]
             and rows[-1]["kind"] == "unit_reserve" and rows[-1]["no_refund"] is True
             and rows[-1]["before_any_science_or_IO"] is True
             and len(rows[-1]["full_31_counters"]) == 31, "C1_ROUTE_T4_RESERVATION")
        if self.resuming:
            from .t34r3_c2_history import verify_history
            return verify_history(self)
        need(all(json.loads(line)["allocation"] == "T3" for line in events.read_bytes().splitlines()),
             "C1_ROUTE_PRIOR_SCIENCE")

    def before_dispatch(self, c):
        """Return a verified saved replay, or latch the only dispatch before worker launch."""
        with locked(self.root / "science/ledger.lock"):
            expected = self._saved_claim(c)
            portable.ancestors(self.claim)
            portable.ancestors(self.result)
            if self.claim.exists():
                need(portable.read(self.claim) == expected and self.result.is_file(),
                     "C1_ROUTE_DISPATCH_ALREADY_CLAIMED")
                return self._replay(c)
            need(not self.result.exists(), "C1_ROUTE_RESULT_WITHOUT_CLAIM")
            main_file = getattr(sys.modules["__main__"], "__file__", None)
            need(main_file is not None and binding(main_file) == self.controller, "C1_ROUTE_MAIN_ENTRY")
            self.clock(time.time(), MIN_START)
            self.verify_unclaimed()
            wait = _read_bound(self.a["old_wait"], self.root / "runs/T4/managed/wait.json")
            from ..winproc import identity_alive
            need(wait["root_exit"] == 1 and wait["error"] == "ROOT_EXIT:1"
                 and all(wait[k] is True for k in ("actual_all_wait", "actual_root_wait", "exact", "tree_empty"))
                 and wait["actual_members"] == len(wait["records"]) > 0
                 and all(r["actual_handle_wait"] is True and r["job_member_verified"] is True and
                         identity_alive(r) is False for r in wait["records"]), "C1_ROUTE_OLD_WAIT")
            old_output = self.root / "w/T4/operations" / self.old_id
            portable.ancestors(old_output)
            for p in (self.workspace / "store", self.workspace / "pool", self.workspace / "exports",
                      self.root / ("x5" if self.resuming else "x4"),
                      self.root / ("r5" if self.resuming else "r4")):
                portable.ancestors(p)
                need(not p.exists(), "C1_ROUTE_PRIOR_BUSINESS_STATE", str(p))
            need(old_output.is_dir() and not any(old_output.iterdir()), "C1_ROUTE_PRIOR_OUTPUT")
            _new_file(self.claim, expected)
            return None

    def remember(self, c, result):
        if result.get("state") != "SUCCEEDED" or result.get("resident") is not True:
            return
        from ..gm004a_operations import OperationStore
        found = OperationStore(self.workspace / "store", read_only=True).lookup_request(self.key, saved_only=True)
        need(found is not None, "C1_ROUTE_RESULT_OPERATION")
        path, spec = found
        need(spec["semantics"]["instance_context"] == binding(c["manifest_path"])
             and all(result[k] == spec[k] for k in ("job_id", "attempt_id")),
             "C1_ROUTE_RESULT_CONTEXT")
        _new_file(self.result, dict(claim=self._saved_claim(c), commit=binding(path / "commit.json"), result=result))

    def verify_dispatch(self, c, now):
        portable.ancestors(self.claim)
        need(portable.read(self.claim) == self._saved_claim(c), "C1_ROUTE_CLAIM_REQUIRED")
        return self.clock(now)

    def _replay(self, c):
        from ..gm004a_operations import OperationStore
        from ..cpu_replay.api import check_binding
        saved = portable.read(self.result)
        need(set(saved) == {"claim", "commit", "result"} and saved["claim"] == self._saved_claim(c),
             "C1_ROUTE_RESULT_BINDING")
        found = OperationStore(self.workspace / "store", read_only=True).lookup_request(self.key, saved_only=True)
        need(found is not None, "C1_ROUTE_RESULT_OPERATION")
        path, spec = found
        result = saved["result"]
        need(saved["commit"] == binding(path / "commit.json")
             and spec["semantics"]["instance_context"] == binding(c["manifest_path"])
             and all(result[k] == spec[k] for k in ("job_id", "attempt_id")),
             "C1_ROUTE_RESULT_CONTEXT")
        check_binding(path, spec, result["binding"])
        return dict(result, idempotent_hit=True)
