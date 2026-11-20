"""Geometry-derived capacity ledger. Never used to select or admit a route."""
from dataclasses import asdict, dataclass

from ..cpu_replay.contract import digest
from .dynamic_admission import GPU_GUARD, MIB, Photo

SCHEMA = "gm086-resource-shadow-v3"
METRICS = ("ram", "commit", "vram")
CONSUMPTION = {"blend", "roi", "reference_roi", "export", "readback"}


def sparse_geometry(w, h, rw, rh):
    """Positive separable area/hat support; no matrix or image allocation."""
    def axis(native, reference):
        count, gram, next_index = 0, 0, 0
        for q in range(reference):
            left = q * native // reference
            right = ((q + 1) * native - 1) // reference
            first = ((2 * left + 1) * reference) // (16 * native)
            last = ((2 * right + 1) * reference + 16 * native - 1) // (16 * native)
            count += last - first + 1
            # Monotone intervals: each newly covered column adds its diagonal
            # and both triangles of its overlap with this interval.
            start = max(first, next_index)
            width = last - start + 1
            if width > 0:
                gram += width * (start + last - 2 * first + 1)
                next_index = last + 1
        return count, gram

    ax, gx = axis(w, rw)
    ay, gy = axis(h, rh)
    nx, ny = (rw + 7) // 8 + 1, (rh + 7) // 8 + 1
    n, k = rw * rh, nx * ny
    edges = (nx - 1) * ny + (ny - 1) * nx
    nnz = dict(A=ax * ay, D=2 * edges, Q_upper=min(k * k, gx * gy + 2 * edges + k))
    index_bytes = 8 if max(n, k, edges, *nnz.values()) >= 2**31 else 4
    rows = dict(A=n, D=edges, Q_upper=k)
    csr = {name: count * (8 + index_bytes) + (rows[name] + 1) * index_bytes
           for name, count in nnz.items()}
    return dict(N=n, K=k, E=edges, nnz=nnz, index_bytes=index_bytes, csr=csr)


def layout(header):
    photo = Photo.from_header(header)
    fields = ("rows_per_strip", "tile_width", "tile_length", "compression", "planar_configuration")
    ifds = header.get("ifds") or ()
    ifd = next((row for row in ifds if row.get("ifd") == header.get("selected_ifd", 0)), {})
    return dict(**asdict(photo), channels=3, dtype=f"uint{photo.bits}",
                decoded_row_bytes=photo.rgb // photo.height,
                codec_layout={name: ifd.get(name) for name in fields})


@dataclass(frozen=True)
class Backing:
    id: str
    owner: str
    ram: int = 0
    commit: int = 0
    vram: int = 0
    shared: bool = False


def _backing_id(name, stage, backend, quality):
    if name in ("windows_cuda_backing", "sparse_A_D_Q_CG"):
        return None
    if backend == "cpu" and quality == "fast":
        if stage == "handoff" and name == "O":
            # Fast CPU renders directly into the host's shared output.
            return None
        if name == "Y":
            return "O"
    if backend == "mixed" and stage == "R0":
        if name == "O":
            return "mixed_private_O"
        if name == "mixed_handoff_O":
            return "mixed_destination_O"
    if backend == "gpu":
        if stage == "handoff" and name == "Y_private":
            return "LO_or_Y"
        if name == "Y":
            return "published_Y"
    return name


def _backings(objects, stage, backend, quality):
    for item in objects:
        name = _backing_id(item["name"], stage, backend, quality)
        if name is not None:
            yield Backing(name, item["owner"], item["ram"], item["commit"], item["cuda"],
                          item["shared"] or name == "published_Y")


def _totals(backings):
    unique = {}
    for item in backings:
        if item.id in unique and unique[item.id] != item:
            raise ValueError("conflicting shadow backing")
        unique[item.id] = item
    return {key: sum(getattr(item, key) for item in unique.values()) for key in METRICS}


def _row(name, backings, base, *, backing=0, sparse_extra=0, cache=0, inherited_cache=0):
    predicted = _totals(backings)
    # No fitting to this machine. These intervals carry forward existing
    # assumptions and record which parts need attribution.
    runtime = {key: base.get("runtime_" + key, 0) for key in METRICS}
    residual = {key: base.get("uncertainty_" + key, 0) for key in METRICS}
    terms = {
        "runtime_unattributed": runtime,
        "inherited_model_residual": residual,
        "windows_backing_unattributed": dict(ram=0, commit=backing, vram=0),
        "allocator_cache": dict(ram=0, commit=0, vram=cache + inherited_cache),
        "sparse_build_vectors_allocator": dict(ram=sparse_extra, commit=sparse_extra, vram=0),
    }
    upper = {key: sum(term[key] for term in terms.values()) for key in METRICS}
    safety = dict(ram=base.get("external_ram_guard", 0), commit=base.get("external_commit_guard", 0),
                  vram=GPU_GUARD if predicted["vram"] or inherited_cache else 0)
    capacity = {key: predicted[key] + upper[key] for key in METRICS}
    return dict(stage=name, backings=[asdict(x) for x in backings], predicted_usage=predicted,
                uncertainty_bound=dict(state="UNCALIBRATED", lower=dict.fromkeys(METRICS, 0), upper=upper,
                                       terms=terms, source="existing_envelopes_and_structural_graph"),
                safety_margin=safety, capacity_limit=capacity,
                required_additional={key: capacity[key] + safety[key] for key in METRICS})


def peaks(group):
    """Use the same rows for every metric. Ties select the last stage by name."""
    rows = tuple(group)
    return {key: max(((row["required_additional"][key], row["stage"]) for row in rows),
                     default=(0, None)) for key in METRICS}


def evaluate(source, reference, cap, *, fast_parent=None):
    """Project the validated, authoritative geometry into a separate graph."""
    s, r = layout(source), layout(reference)
    backend, quality, operation = (cap[k] for k in ("backend", "quality", "operation"))
    sparse = (sparse_geometry(s["width"], s["height"], r["width"], r["height"])
              if "P3" in cap["objects"] else None)
    rows = []
    if fast_parent is not None:
        for row in fast_parent["stages"]:
            if row["stage"] not in CONSUMPTION:
                rows.append(dict(row, stage="fast_parent_" + row["stage"],
                                 backings=[dict(obj, id="fast_parent_" + obj["id"]) for obj in row["backings"]]))
        rows.append(_row("fast_parent_release", [], {}))
    llf_cache = next((row["reserved"] for row in cap["stages"] if row["stage"] == "GPU_LLF"), 0)
    for base in cap["stages"]:
        stage = base["stage"]
        original = cap["objects"][stage]
        objects = list(_backings(original, stage, backend, quality))
        if (backend, operation, stage) == ("mixed", "replay", "R0"):
            # Saved mixed R0 passes the host O mapping as out=target.
            # Tile scratch remains charged; there is no second native O.
            objects = [obj for obj in objects if obj.id != "mixed_private_O"]
        cache = max(0, base["reserved"] - base["allocated"])
        if stage == "P3_prepare":
            # H precedes T; its scratch and T conversion do not overlap.
            without_t = [obj for obj in objects if obj.id != "T64"]
            rows.append(_row("H_area", without_t, base))
            rows.append(_row("T_float64", objects, base))
        elif stage == "P3":
            if backend == "cpu":
                size = r["width"] * r["height"] * 3 * (r["bits"] // 8)
                objects.append(Backing("reference_rgb", "io", size, size, shared=True))
            wire = [obj for obj in objects if obj.id != "b64"]
            rows.append(_row("P3_IPC", wire, base))
            final_csr = sum(sparse["csr"].values())
            objects = [obj for obj in objects if obj.id != "solver_wire_frame"]
            objects.append(Backing("P3_CSR", "solver", final_csr, final_csr))
            original_sparse = next(item["commit"] for item in original if item["name"] == "sparse_A_D_Q_CG")
            extra = max(0, original_sparse - final_csr)
            # The coefficients are formed by the later solve loop, after geometry returns.
            constructing = [obj for obj in objects if obj.id != "b64"]
            rows.append(_row("P3_sparse_construct", constructing, base, sparse_extra=extra))
            rows.append(_row("P3_CG", objects, base, sparse_extra=extra))
            # Identified vectors are a subset of the existing unknown bound.
            rows[-1]["solver_explicit_vectors"] = dict(residual=8 * sparse["N"],
                rhs=8 * sparse["K"], x0=8 * sparse["K"], diagonal=8 * sparse["K"])
        elif stage == "GPU_LLF":
            scan = [obj for obj in objects if obj.id in ("source_rgb", "O", "P_or_LP", "b64")]
            expansion_cache = next(row["reserved"] for row in cap["stages"] if row["stage"] == "P3_expand")
            rows.append(_row("range_scan", scan, base, backing=expansion_cache, inherited_cache=expansion_cache))
            for name in ("LLF_O", "LLF_P"):
                rows.append(_row(name, objects, base, backing=llf_cache, cache=cache))
                rows[-1]["cache_at_entry"] = ("clear_unused_before_pass" if name == "LLF_P"
                                              else "previous_stage_cache_may_remain")
        else:
            carried = llf_cache if stage in ("compose", "handoff") else 0
            rows.append(_row(stage, objects, base, backing=carried, cache=cache, inherited_cache=carried))
    previous = set()
    for row in rows:
        current = {obj["id"] for obj in row["backings"]}
        row["introduced"] = sorted(current - previous)
        row["retired"] = sorted(previous - current)
        previous = current
    aliases = {"LP": "P_or_LP", "Y_before_publish": "LO_or_Y"} if llf_cache else {}
    if backend == "cpu" and quality == "fast":
        aliases["Y"] = "O"
    value = dict(schema=SCHEMA, state="UNCALIBRATED", source=s, reference=r,
                 backend=backend, quality=quality, operation=operation, geometry=cap["geometry"],
                 sparse=sparse, stages=rows, aliases=aliases,
                 capacity_limit=cap["proposed_job_caps"],
                 startup_to_safe_boundary=peaks(row for row in rows if row["stage"] not in CONSUMPTION),
                 later_peak=peaks(row for row in rows if row["stage"] in CONSUMPTION),
                 terminal=dict(cancel="wait_for_owned_exit", release="wait_for_mapping_settlement"),
                 applied=False)
    value["model_id"] = digest(value)
    return value


def from_plan(plan):
    """Use the existing geometry gate; the shadow cannot authorize a route."""
    from .dynamic_admission import model
    source, reference = plan["source"], plan["reference"]
    backend = plan["selection"]["backend"]
    quality, operation = plan["quality"], plan["operation"]
    cap = model(source, reference, backend, quality, operation)
    parent = None
    if (backend, quality, operation) == ("cpu", "fine", "fit"):
        parent = evaluate(source, reference, model(source, reference, "cpu", "fast", "fit"))
        cap = model(source, reference, "cpu", "fine", "refine")
    result = evaluate(source, reference, cap, fast_parent=parent)
    result.update(operation=operation, capacity_limit=plan["selection"]["jobs"],
                  authority_capacity_id=plan["selection"]["admission"]["capacity_id"])
    result["model_id"] = digest({k: v for k, v in result.items() if k != "model_id"})
    return result


POINTS = {"LLF-O": "LLF_O", "LLF-P": "LLF_P", "P3_prepare": "T_float64",
          "P3": "P3_sparse_construct"}


def _credits(plan, stage, row, credited):
    """Match only names authorized by the current authoritative stage."""
    selection = plan["selection"]
    objects = {obj["name"]: obj for obj in selection["objects"].get(stage, ())}
    known = {obj["id"]: obj for obj in row["backings"]}
    accepted, conflicts = {}, set()
    rejected = 0
    for name, credit in credited.items():
        obj = objects.get(name)
        target = _backing_id(name, stage, selection["backend"], plan["quality"]) if obj else None
        if target not in known:
            rejected += 1
            # A caller-supplied canonical alias cannot stand in for the guard's
            # key. If both names are present, provenance is ambiguous.
            if name in known:
                conflicts.add(name)
            continue
        backing = known[target]
        commit, ram = credit.get("commit"), credit.get("ram")
        if (type(commit) is not int or commit <= 0 or commit != obj["commit"]
                or commit != backing["commit"] or type(ram) is not int
                or not 0 <= ram <= min(commit, backing["ram"])
                or not isinstance(credit.get("mapping"), str) or not credit["mapping"]):
            rejected += 1
            conflicts.add(target)
            continue
        if target in accepted:
            conflicts.add(target)
        accepted[target] = dict(ram=ram, commit=commit)
    for target in conflicts:
        accepted.pop(target, None)
    return accepted, rejected, len(conflicts)


def boundary(plan, stage, point, credited, sample):
    """Only consume credits produced by the same live guard and sample."""
    model = plan.get("resource_shadow")
    if model is None:
        return None
    if (model.get("schema") != SCHEMA or not model.get("model_id")
            or model["model_id"] != digest({k: v for k, v in model.items() if k != "model_id"})
            or any(model.get(k) != plan.get(k) for k in ("quality", "operation"))
            or model.get("backend") != plan["selection"]["backend"]
            or model.get("authority_capacity_id") != plan["selection"]["admission"]["capacity_id"]):
        return dict(schema=SCHEMA, state="UNAVAILABLE")
    name = POINTS.get(point.removesuffix("-host"), POINTS.get(stage, stage))
    if stage in ("startup", "cpu-child") and "startup_to_safe_boundary" in model:
        peak = model["startup_to_safe_boundary"]
        return dict(schema=SCHEMA, model_id=model["model_id"], state=model["state"], stage=stage,
                    required_additional={key: peak[key][0] for key in METRICS})
    row = next((item for item in model.get("stages", ()) if item["stage"] == name), None)
    if row is None:
        return dict(schema=SCHEMA, model_id=model.get("model_id"), state="NO_STAGE_OBSERVATION")
    accepted, rejected, conflicts = _credits(plan, stage, row, credited)
    required = dict(row["required_additional"])
    applied = dict(ram=0, commit=0)
    for key in ("ram", "commit"):
        if type(sample.get(key)) is int:
            applied[key] = sum(obj[key] for obj in accepted.values())
            required[key] = max(row["safety_margin"][key], required[key] - applied[key])
    return dict(schema=SCHEMA, model_id=model["model_id"], state=model["state"], stage=name,
                predicted_usage=row["predicted_usage"], uncertainty_upper=row["uncertainty_bound"]["upper"],
                safety_margin=row["safety_margin"], required_additional=required,
                capacity_limit=row["capacity_limit"], applied_credit=applied,
                rejected_credit_count=rejected, ambiguous_backing_count=conflicts)
