"""Read bounded, observational progress. Never recover or publish a result."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from .engine.profiles import PROFILES
from .live_json import read_live_json

MAX_TRACE_TAIL = 16 * 1024
COMPUTE_STAGES = {"fit", "render", "put_y", "fetch_source", "fetch_reference",
                  "input_prepare", "H_area", "T_float64", "P3_solve", "P3_expand",
                  "pair_range_scan", "LLF_O", "LLF_P", "compose", "host_handoff", "reuse_fast_parameters",
                  "save_fast_recipe", "save_fine_recipe", "publish"}


def _training_progress(path: Path, total: int):
    """Only complete JSONL records count; a concurrently written last line waits.

    A trace records before_fit=0, update 1, each 100 updates, and the final
    update. These are completed training updates, not whole-operation progress.
    """
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            offset = max(0, size - MAX_TRACE_TAIL)
            stream.seek(offset)
            raw = stream.read(MAX_TRACE_TAIL)
    except FileNotFoundError:
        return None
    if offset:
        raw = raw.partition(b"\n")[2]  # The first line may start mid-record.
    lines = raw.split(b"\n")[:-1]  # Never accept an unterminated last write.
    for line in reversed(lines):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("Training progress record must be an object")
        done = row.get("update")
        if type(done) is not int or not 0 <= done <= total:
            raise ValueError("Training progress update is outside its contract")
        # update_check is an uncompleted-loop cancellation hook, never a count.
        if row.get("stage") == "update_check":
            continue
        if done == 0 and row.get("stage") != "before_fit":
            raise ValueError("Unrecognized initial training progress")
        stamp=row.get("gm066_monotonic")
        return dict(done=done,total=total,unit="updates",scope="stage",
                    **({"monotonic":stamp} if type(stamp) in (int,float) and math.isfinite(stamp) else {}))
    return None


def compute_progress(path: Path, profile_id: str) -> dict:
    if profile_id not in PROFILES:
        return {}
    phase = read_live_json(path / "phase.json")
    stage = phase.get("stage")
    if stage not in COMPUTE_STAGES:
        return {}
    result = {"detail_stage": stage, "gm066_monotonic":phase.get("gm066_monotonic")}
    if stage=="render":
        row=read_live_json(path/"render-progress.json")
        if row.get("stage")=="render":
            result["stage_progress"]=dict(done=row.get("done"),total=row.get("total"),monotonic=row.get("stamp"))
    if stage == "fit":
        total = PROFILES[profile_id]["settings"]["updates"]
        if type(total) is int and total > 0:
            try:
                progress = _training_progress(path / "fit-progress.jsonl", total)
                if progress is not None:
                    result["stage_progress"] = progress
            except (OSError, ValueError) as exc:
                # A presentation-only counter failure is visible, not a worker
                # failure and not permission to bypass durable-state checks.
                result["progress_warning"] = type(exc).__name__
    for key in ("fit_started_wall", "render_started_wall"):
        value = phase.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value > 0:
            result["stage_started_wall"] = value
            break
    return result
