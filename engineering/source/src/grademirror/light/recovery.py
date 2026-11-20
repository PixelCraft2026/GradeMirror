"""Restore a saved, rendered GPU recipe into a separate project."""
from pathlib import Path
import shutil

from ..common import atomic_json, reject_reparse, sha_file, strict_load, writable_path
from ..cpu_replay.contract import need
from ..engine.profiles import FINE, R0, recipe_profile, runtimes
from ..fit_publication import validate_archive

MARKER = "saved-result-recovery.json"
SCHEMA = "gm076-saved-result-recovery-v1"


def _digest(value):
    return isinstance(value,str) and len(value)==64 and all(c in "0123456789abcdef" for c in value)


def _bounded(path, maximum):
    reject_reparse(path)
    need(path.is_file() and 0 < path.stat().st_size <= maximum, "RECOVERY_SMALL_ARTIFACT_REQUIRED", path.name)


def _recipe(folder):
    from ..portable_project import _canonical_digest
    _bounded(folder/"recipe.json",256*1024)
    _bounded(folder/"parameters.npz",256*1024)
    recipe = strict_load(folder/"recipe.json")
    profile = recipe_profile(recipe)
    need(profile["id"] in (R0,FINE) and recipe.get("device",{}).get("id")=="cuda:0"
         and recipe.get("recipe_kind")=="development_image"
         and recipe.get("production_qualified") is False
         and _canonical_digest(recipe)==recipe["recipe_sha256"]
         and sha_file(folder/"parameters.npz")==recipe["archive_sha256"], "RECOVERY_RECIPE_IDENTITY")
    names=["recipe.json","parameters.npz"]
    validate_archive(folder/"parameters.npz",recipe["parameter_sha256"],shapes=profile["shapes"])
    if profile["id"]==FINE:
        from ..fine_contract import validate_recipe
        _bounded(folder/"p3.npz",4*1024**2)
        validate_recipe(recipe,folder/"p3.npz")
        names.append("p3.npz")
    return recipe, {name:sha_file(folder/name) for name in names}


def _proof(recipe,proof):
    width=recipe["source_identity"]["shape"][1]
    height=recipe["source_identity"]["shape"][0]
    tiles=((width+511)//512)*((height+511)//512)
    need(proof.get("profile_id")==recipe["profile_id"] and proof.get("recipe_sha256")==recipe["recipe_sha256"]
         and proof.get("render_count")==1 and proof.get("render_tiles")==tiles
         and _digest(proof.get("float32_sha256"))
         and (recipe["profile_id"]!=FINE or proof["float32_sha256"]==recipe["postprocess"]["hashes"]["Y"]),
         "RECOVERY_RENDER_PROOF")


def prepare(source,reference,archive,proof,destination):
    from ..desktop.backend import EngineBackend
    folder=writable_path(Path(destination))
    archive=Path(archive).absolute()
    proof=Path(proof).absolute()
    _bounded(proof,64*1024)
    recipe,files=_recipe(archive)
    witness=strict_load(proof)
    _proof(recipe,witness)
    inputs={"source":Path(source).absolute(),"reference":Path(reference).absolute()}
    for name,path in inputs.items():
        reject_reparse(path)
        expected=recipe[name+"_identity"]
        need(path.is_file() and path.stat().st_size==expected["file_size_bytes"]
             and sha_file(path)==expected["file_sha256"]==recipe[name+"_sha256"], "RECOVERY_INPUT_CHANGED",name)
    need(not folder.exists() and not folder.is_symlink(), "RECOVERY_REQUIRES_NEW_PROJECT")
    EngineBackend().create_project(inputs["source"],inputs["reference"],folder,
                                   mode="fine" if recipe["profile_id"]==FINE else "fast")
    store=folder/"recovery-input"
    store.mkdir()
    for name in files:
        shutil.copyfile(archive/name,store/name)
    shutil.copyfile(proof,store/"pre-handoff.json")
    marker=dict(schema=SCHEMA,state="PENDING",backend="gpu",recipe_sha256=recipe["recipe_sha256"],
                float32_sha256=witness["float32_sha256"],files=files,
                proof_sha256=sha_file(store/"pre-handoff.json"))
    atomic_json(folder/MARKER,marker)
    check(folder)
    return {"state":"RECOVERY_PREPARED","project":str(folder),"recipe_sha256":marker["recipe_sha256"],"fit_count":0}


def check(project):
    from ..project import _read_project_manifest
    root=Path(project)
    if not (root/MARKER).is_file():
        return None
    _bounded(root/MARKER,64*1024)
    marker=strict_load(root/MARKER)
    need(marker.get("schema")==SCHEMA and marker.get("state") in ("PENDING","PUBLISHED")
         and marker.get("backend")=="gpu","RECOVERY_MARKER_INVALID")
    folder=root/"recovery-input"
    recipe,files=_recipe(folder)
    _bounded(folder/"pre-handoff.json",64*1024)
    proof=strict_load(folder/"pre-handoff.json")
    _proof(recipe,proof)
    manifest,forward=_read_project_manifest(root)
    need(not forward and files==marker["files"] and recipe["recipe_sha256"]==marker["recipe_sha256"]
         and proof["float32_sha256"]==marker["float32_sha256"]
         and sha_file(folder/"pre-handoff.json")==marker["proof_sha256"]
         and all(recipe[name+"_sha256"]==manifest[name]["byte_sha256"] for name in ("source","reference"))
         and manifest["extensions"]["algorithm_profile"]==recipe["profile_id"], "RECOVERY_PROJECT_BINDING")
    return marker,recipe


def publish(project, identity):
    from ..portable_project import open_portable_project, promote_fitted_recipe
    project=Path(project)
    marker,recipe=check(project)
    need(identity.get("float32_sha256")==marker["float32_sha256"]
         and identity.get("recipe_sha256")==marker["recipe_sha256"]
         and identity.get("fit_count")==0 and _digest(identity.get("half_q16_sha256")),
         "RECOVERY_RESULT_CHANGED")
    from ..project import _read_project_manifest
    manifest,forward=_read_project_manifest(project)
    need(not forward,"RECOVERY_PROJECT_BINDING")
    if manifest["current_revision"]=="r000001":
        promote_fitted_recipe(project,project/"recovery-input",
            result_float32_sha256=identity["float32_sha256"],half_q16_sha256=identity["half_q16_sha256"])
    view=open_portable_project(project,available_runtimes=runtimes())
    index=strict_load(Path(view["artifacts_index_path"]))
    need(view["recipe_sha256"]==recipe["recipe_sha256"]
         and index["result_float32_sha256"]==identity["float32_sha256"]
         and index["half_q16_sha256"]==identity["half_q16_sha256"],"RECOVERY_PUBLISH_CONFLICT")
    if marker["state"]!="PUBLISHED":
        atomic_json(project/MARKER,dict(marker,state="PUBLISHED"))


def finish_publication(project,path,key):
    from ..common import GMError
    try:
        publish(project,strict_load(path/"replay-identity.json"))
    except (OSError, ValueError, GMError) as cause:
        if getattr(cause,"code",None)=="RECOVERY_PUBLISH_CONFLICT":
            raise
        error=GMError("MIXED_RESULT_PENDING_PUBLICATION","Saved result is ready; retry publication with the same request key",7)
        error.request_key=key
        raise error from cause


def prepare_mixed(source,reference,archive,proof,solver,destination):
    from ..desktop.backend import EngineBackend
    archive=Path(archive).absolute()
    proof=Path(proof).absolute()
    solver=Path(solver).absolute()
    _bounded(proof,64*1024)
    _bounded(solver,64*1024)
    recipe,files=_recipe(archive)
    r0=strict_load(proof)
    p3=strict_load(solver)
    folder=writable_path(Path(destination))
    need(not folder.exists() and recipe["profile_id"]==R0 and r0.get("status")=="PASS"
         and r0.get("r0_recipe_sha256")==recipe["recipe_sha256"]
         and r0.get("parameter_sha256")==recipe["parameter_sha256"]
         and all(r0.get(name+"_identity")==recipe[name+"_identity"] for name in ("source","reference"))
         and _digest(r0.get("O_sha256")) and p3.get("status")=="PASS"
         and p3.get("counts",{}).get("scalar_CG")==3 and _digest(p3.get("coefficient_sha256")),
         "MIXED_RECOVERY_EVIDENCE")
    inputs=[Path(source).absolute(),Path(reference).absolute()]
    for name,path in zip(("source","reference"),inputs,strict=True):
        reject_reparse(path)
        need(sha_file(path)==recipe[name+"_sha256"],"RECOVERY_INPUT_CHANGED",name)
    EngineBackend().create_project(*inputs,folder,mode="fine")
    seed=folder/"mixed-recovery"
    seed.mkdir()
    for name in files:
        shutil.copyfile(archive/name,seed/name)
    marker=dict(schema="gm076-mixed-r0-recovery-v1",files=files,recipe_sha256=recipe["recipe_sha256"],
                O_sha256=r0["O_sha256"],coefficient_sha256=p3["coefficient_sha256"])
    atomic_json(seed/"seed.json",marker)
    return dict(state="MIXED_RECOVERY_PREPARED",project=str(folder),fit_count=0)


def mixed_seed(project):
    from ..project import _read_project_manifest
    directory=Path(project)/"mixed-recovery"
    path=directory/"seed.json"
    if not path.is_file():
        return None
    _bounded(path,64*1024)
    marker=strict_load(path)
    recipe,files=_recipe(directory)
    project_manifest,forward=_read_project_manifest(Path(project))
    need(not forward and marker.get("schema")=="gm076-mixed-r0-recovery-v1"
         and marker.get("recipe_sha256")==recipe["recipe_sha256"] and marker.get("files")==files
         and all(_digest(marker.get(name)) for name in ("O_sha256","coefficient_sha256"))
         and all(recipe[name+"_sha256"]==project_manifest[name]["byte_sha256"] for name in ("source","reference"))
         and project_manifest["extensions"]["algorithm_profile"]==FINE, "MIXED_RECOVERY_PROJECT_BINDING")
    return marker
