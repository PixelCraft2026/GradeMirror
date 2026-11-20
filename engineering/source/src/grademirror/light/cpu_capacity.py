"""Portable CPU geometry and conservative, phase-specific memory envelopes."""
from ..cpu_replay.contract import need
from ..image_limits import GIB, MIB

BASE_PIXELS = 24_000_000
BASE_AXIS = 8192
NATIVE_PIXELS = 50_118_276
NATIVE_AXIS = 16_384
REFERENCE_PIXELS = 1_572_864
REFERENCE_AXIS = 4096
FIRST_QUALIFICATION = "gm072-first-fixed-jpeg8-v1"
FIXED_INPUTS = ("a93d26aef32cdd18fdaf02208f6bda97d52373e95efbadd2af2765a9f522beae",
                "40b8d4e4358caffdb51f7e1a4e20fc93f16f0c97aa57dc4529e9cc0cf9fcc280")


def extended(source):
    w, h = source["width"], source["height"]
    return w * h > BASE_PIXELS or max(w, h) > BASE_AXIS


def check_pair(source, reference):
    w, h = source["width"], source["height"]
    rw, rh = reference["width"], reference["height"]
    if not extended(source):
        need(rw * rh <= BASE_PIXELS and max(rw, rh) <= BASE_AXIS, "CPU_EXISTING_REFERENCE_SCOPE")
        return
    need(w * h <= NATIVE_PIXELS and max(w, h) <= NATIVE_AXIS and min(w, h) >= 512,
         "CPU_NATIVE_GEOMETRY_SCOPE")
    need(rw * rh <= REFERENCE_PIXELS and max(rw, rh) <= REFERENCE_AXIS,
         "CPU_EXTENDED_REFERENCE_SCOPE")
    # This preview has only a defensible new-domain envelope for the bounded
    # eight-bit JPEG source. Existing RGB16 and other formats keep their domain.
    source_format = source.get("format")
    need(isinstance(source_format, str) and source_format.casefold() == "jpeg"
         and source["bits"] == reference["bits"] == 8,
         "CPU_EXTENDED_CODEC_CAPACITY_UNQUALIFIED")


def stage_requirements(source, reference, quality, operation, *, qualification=None):
    check_pair(source, reference)
    w, h = source["width"], source["height"]
    rw, rh = reference["width"], reference["height"]
    if qualification is not None:
        need(qualification == FIRST_QUALIFICATION and operation == "fit"
             and (w, h, rw, rh) == (5484, 9139, 922, 1536)
             and tuple(row.get("sha256") for row in (source, reference)) == FIXED_INPUTS,
             "LIGHT_FIRST_CAPACITY_SCOPE")
    pixels, ref_pixels = w * h, rw * rh
    integer_bytes = 3 * (source["bits"] * pixels + reference["bits"] * ref_pixels) // 8
    native_float = 12 * pixels
    # Conservative concurrent bytes from the allocated source, O/Y and host
    # copies, plus fixed process/library allowances. Commit remains separate.
    additional = {
        "decode": integer_bytes * 2 + GIB,
        "r0": integer_bytes + native_float * 2 + 2 * GIB,
        "host_handoff": integer_bytes + native_float * 2 + 1536 * MIB,
        "resident": integer_bytes + native_float + GIB,
        "export": integer_bytes * 3 + native_float + 1536 * MIB,
    }
    if operation == "fit":
        additional["training"] = integer_bytes + 3 * GIB
    if quality == "fine":
        from ..fine_contract import validate_limits
        from ..fine_resources import solver_job_bytes
        from ..engine.cpu_postprocess.geometry import plan, workspace_bound
        validate_limits(source, reference)
        workspace = max(workspace_bound(core)["total_bytes"] for core in plan(h, w, 256))
        need(workspace <= 512 * MIB, "CPU_FINE_WORKSPACE")
        additional["postprocess"] = integer_bytes + native_float * 2 + workspace + 2 * GIB
        if operation != "replay":
            solver = solver_job_bytes((w, h), (rw, rh))
            need(solver <= 3 * GIB, "CPU_FINE_SOLVER_JOB_LIMIT")
            additional["solver"] = integer_bytes + native_float + 120 * ref_pixels + solver + GIB
    commit = {stage: value + GIB for stage, value in additional.items()}
    if qualification is not None:
        if quality == "fast":
            # GM069 first-fit/R0 tree samples plus fixed allowances. Export remains at its old bound.
            additional["r0"] -= GIB
            additional["training"] -= GIB
            additional["host_handoff"] -= 512 * MIB
        else:
            from ..fine_resources import solver_memory_bytes
            # Keep the solver Job and its separate commit reservation unchanged.
            additional["solver"] += solver_memory_bytes((w,h),(rw,rh)) - solver
    rows = []
    for stage, value in additional.items():
        need(value <= 6 * GIB, "CPU_FINE_TREE_ENVELOPE", stage)
        margin = min(1_000_000_000, max(512 * MIB, (value + 4) // 5))
        rows.append(dict(stage=stage, ram=value + margin, commit=commit[stage], vram=0,
                         reserved=0, additional_bytes=value, safety_bytes=margin))
    return rows

