"""Small, verifiable artifacts from interrupted GPU and mixed operations."""
from pathlib import Path

from .common import atomic_json, reject_reparse, sha_file, strict_load
from .cpu_replay.contract import need
from .fit_publication import validate_archive


def _file(path, limit):
    reject_reparse(path)
    need(path.is_file() and 0 < path.stat().st_size <= limit, "CHECKPOINT_FILE_SIZE", path.name)
    return dict(bytes=path.stat().st_size, sha256=sha_file(path))


def _r0(folder):
    from .engine.profiles import recipe_profile
    files = dict(recipe=_file(folder/"recipe.json", 128*1024),
                 parameters=_file(folder/"parameters.npz", 256*1024))
    from .hybrid_replay.contract import digest
    value = strict_load(folder/"recipe.json")
    need(value["recipe_sha256"] == digest({k:v for k,v in value.items()
                                            if k not in ("archive_sha256","recipe_sha256")})
         and value["source_sha256"] == value["source_identity"]["file_sha256"]
         and value["reference_sha256"] == value["reference_identity"]["file_sha256"],
         "CHECKPOINT_RECIPE_DIGEST")
    profile = recipe_profile(value)
    need(profile["id"]=="independent-r0-lab-m05-xy001-v1"
         and value["archive_sha256"]==files["parameters"]["sha256"],
         "CHECKPOINT_R0_BINDING")
    validate_archive(folder/"parameters.npz", value["parameter_sha256"], shapes=profile["shapes"])
    return value, files


def record(run, r0_directory, counts, *, p3_path=None, solver=None):
    from .light.runtime import operation
    plan = operation()
    if plan is None:
        return
    value, files = _r0(r0_directory)
    need(value["source_sha256"] == plan["source"]["sha256"]
         and value["reference_sha256"] == plan["reference"]["sha256"], "CHECKPOINT_INPUT_BINDING")
    p3 = None
    if p3_path is not None:
        from .fine_contract import read_coefficients
        import hashlib
        archive = _file(p3_path, 4*1024**2)
        data = read_coefficients(p3_path, solver["shape"], archive["sha256"])
        need(hashlib.sha256(data).hexdigest()==solver["coefficient_sha256"], "CHECKPOINT_P3_BINDING")
        p3 = dict(file=archive, path=str(p3_path), solver=solver)
    checkpoint = dict(schema="gm076-incomplete-checkpoint-v1", state="INCOMPLETE",
        attempt_path=str(run), plan_id=plan["plan_id"], request_key=plan["request_key"],
        build=plan["build"], backend=plan["selection"]["backend"],
        phase="P3_SAVED" if p3 is not None else "R0_PARAMETERS_SAVED", counts=counts,
        source=value["source_identity"], reference=value["reference_identity"],
        training_grid=value["training_shape"], device=value["device"],
        runtime=value["runtime"], core=value["core_sha256"], recipe_sha256=value["recipe_sha256"],
        parameter_sha256=value["parameter_sha256"], r0_directory=str(r0_directory), files=files, p3=p3)
    atomic_json(run / ("checkpoint-p3.json" if p3 is not None else "checkpoint-r0.json"), checkpoint)


def verify(path, *, backend, source_sha256, reference_sha256):
    reject_reparse(path)
    row = strict_load(path)
    need(row["schema"]=="gm076-incomplete-checkpoint-v1" and row["state"]=="INCOMPLETE"
         and row["backend"]==backend and Path(row["attempt_path"]).resolve()==path.parent.resolve(),
         "CHECKPOINT_ATTEMPT_BINDING")
    folder = Path(row["r0_directory"])
    value, files = _r0(folder)
    need(files==row["files"] and value["recipe_sha256"]==row["recipe_sha256"]
         and value["parameter_sha256"]==row["parameter_sha256"]
         and value["source_sha256"]==source_sha256 and value["reference_sha256"]==reference_sha256
         and all(value[old]==row[new] for old,new in (("source_identity","source"),
                ("reference_identity","reference"),("device","device"),("runtime","runtime"),
                ("core_sha256","core"),("training_shape","training_grid"))), "CHECKPOINT_R0_BINDING")
    p3 = row["p3"]
    if p3 is not None:
        from .fine_contract import read_coefficients
        import hashlib
        archive = Path(p3["path"])
        need(_file(archive, 4*1024**2)==p3["file"], "CHECKPOINT_P3_ARCHIVE")
        solver = p3["solver"]
        data = read_coefficients(archive, solver["shape"], p3["file"]["sha256"])
        need(hashlib.sha256(data).hexdigest()==solver["coefficient_sha256"], "CHECKPOINT_P3_BINDING")
    return row
