"""Validate immutable recovery assets without a numeric worker."""
import hashlib
from pathlib import Path

from .common import reject_reparse, sha_file, strict_load
from .cpu_replay.contract import need
from .fine_contract import coefficient_shape, read_coefficients


def mixed(project, seed):
    folder=Path(project)/"mixed-recovery"/"saved-post"
    if not folder.exists():
        return None
    reject_reparse(folder)
    need(seed is not None,"MIXED_SAVED_POST_REQUIRES_R0")
    from .hybrid_fit.contract import read_recipe
    recipe,assets=read_recipe(folder)
    post=recipe["postprocess"]
    need(recipe["r0_recipe_sha256"]==seed["recipe_sha256"]
         and post["O_sha256"]==seed["O_sha256"]
         and post["coefficient_sha256"]==seed["coefficient_sha256"]
         and all(assets["r0/"+name]["sha256"]==digest for name,digest in seed["files"].items()),
         "MIXED_SAVED_POST_BINDING")
    return dict(recipe_sha256=recipe["recipe_sha256"],assets=assets,ranges=post["ranges"])


def gpu(folder, seed, expected=None):
    folder=Path(folder)
    path=folder/"saved-p3.json"
    if not path.is_file():
        need(expected is None and not (folder/"saved-p3.npz").exists(),"GPU_SAVED_P3_MISSING")
        return None
    need(seed is not None,"GPU_SAVED_P3_REQUIRES_R0")
    p3=folder/"saved-p3.npz";base=folder/"recipe.json"
    for file in (path,p3,base):reject_reparse(file)
    need(0<path.stat().st_size<=64*1024 and sha_file(base)==seed["recipe_file_sha256"],
         "GPU_SAVED_P3_RECIPE_BINDING")
    marker=strict_load(path);r0=strict_load(base);solver=marker["solver"]
    from .hybrid_fit.contract import ranges
    from .engine.independent_r0_p3_lld10.profile import RUNTIME
    ranges(marker["ranges"])
    need(marker["version"]=="gm076-gpu-saved-p3-v1"
         and marker["r0_recipe_sha256"]==seed["recipe_sha256"]==r0["recipe_sha256"]
         and marker["O"]==seed["float32_sha256"]
         and all(isinstance(marker[k],str) and len(marker[k])==64
                 and all(c in "0123456789abcdef" for c in marker[k]) for k in ("O","OP"))
         and solver["shape"]==coefficient_shape(r0["reference_identity"]["shape"])
         and solver["status"]=="PASS" and solver["runtime"]==RUNTIME["solver"]
         and solver["counts"]["scalar_CG"]==3,"GPU_SAVED_P3_BINDING")
    payload=read_coefficients(p3,solver["shape"],marker["p3_sha256"])
    need(hashlib.sha256(payload).hexdigest()==solver["coefficient_sha256"],"GPU_SAVED_P3_COEFFICIENTS")
    binding=dict(marker_sha256=sha_file(path),p3_sha256=marker["p3_sha256"],
                 r0_recipe_sha256=seed["recipe_sha256"],coefficient_sha256=solver["coefficient_sha256"])
    need(expected is None or binding==expected,"GPU_SAVED_P3_CHANGED")
    return binding,marker,payload

def bind_mixed(project, archive):
    """Attach the validated postprocess recipe from an interrupted mixed run."""
    import shutil
    import uuid
    from .hybrid_fit.contract import read_recipe
    from .light.recovery import mixed_seed
    project=Path(project).resolve();archive=Path(archive).resolve()
    seed=mixed_seed(project)
    need(seed is not None,"MIXED_SAVED_POST_REQUIRES_R0")
    recipe,assets=read_recipe(archive)
    post=recipe["postprocess"]
    need(recipe["r0_recipe_sha256"]==seed["recipe_sha256"]
         and post["O_sha256"]==seed["O_sha256"] and post["coefficient_sha256"]==seed["coefficient_sha256"]
         and all(assets["r0/"+name]["sha256"]==digest for name,digest in seed["files"].items()),
         "MIXED_SAVED_POST_BINDING")
    root=project/"mixed-recovery";target=root/"saved-post"
    need(not target.exists(),"MIXED_SAVED_POST_ALREADY_BOUND")
    prepared=root/(".saved-post-"+uuid.uuid4().hex)
    prepared.mkdir()
    for name in assets:
        dest=prepared/name;dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(archive/name,dest)
    need(read_recipe(prepared)[1]==assets,"MIXED_SAVED_POST_CHANGED")
    prepared.rename(target)
    return dict(project=str(project),binding=mixed(project,seed),fit_count=0,scalar_CG=0)


def bind_gpu(project, checkpoint, archive, partial, ranges_path):
    """Attach small checked GPU coefficients to a prepared refinement project."""
    import shutil
    import uuid
    from .common import atomic_json
    from .light.recovery import _bounded
    from .project import _read_project_manifest
    from .refinement import checked_seed
    project=Path(project).resolve()
    checkpoint,archive,partial,ranges_path=map(Path,(checkpoint,archive,partial,ranges_path))
    for f in (checkpoint,partial,ranges_path):_bounded(f,64*1024)
    _bounded(archive,4*1024**2)
    manifest,forward=_read_project_manifest(project)
    need(not forward and manifest["current_revision"]=="r000001","GPU_SAVED_P3_REQUIRES_NEW_PROJECT")
    seed=checked_seed(project,manifest)
    need(seed is not None,"GPU_SAVED_P3_REQUIRES_R0")
    folder=project/"refinement"
    need(not (folder/"saved-p3.json").exists(),"GPU_SAVED_P3_ALREADY_BOUND")
    saved=strict_load(checkpoint)
    r0=strict_load(folder/"recipe.json")
    fields=strict_load(partial);hashes=fields["hashes"];prepay=strict_load(ranges_path)
    need(saved["schema"]=="gm076-incomplete-checkpoint-v1" and saved["state"]=="INCOMPLETE"
         and saved["phase"]=="P3_SAVED" and saved["backend"]=="gpu" and fields.get("failed") is True
         and saved["recipe_sha256"]==seed["recipe_sha256"] and saved["parameter_sha256"]==r0["parameter_sha256"]
         and saved["files"]["parameters"]["sha256"]==seed["parameters_file_sha256"]
         and saved["files"]["recipe"]["sha256"]==seed["recipe_file_sha256"]
         and saved["training_grid"]==r0["training_shape"]
         and all(saved[k]==r0[k+"_identity"] for k in ("source","reference"))
         and saved["core"]==r0["core_sha256"] and saved["runtime"]==r0["runtime"] and saved["device"]==r0["device"]
         and hashes["O"]==seed["float32_sha256"] and set(hashes)=={"O","OP"}
         and prepay["native_RGB_LLF"]==2 and prepay["native_scalar_LLF"]==6
         and prepay["native_remap_pyramids"]==2*sum(r["count"] for r in prepay["ranges"])
         and saved["p3"]["file"]==dict(bytes=archive.stat().st_size,sha256=sha_file(archive)),
         "GPU_SAVED_P3_PROVENANCE")
    marker=dict(version="gm076-gpu-saved-p3-v1",r0_recipe_sha256=seed["recipe_sha256"],
        O=hashes["O"],OP=hashes["OP"],p3_sha256=saved["p3"]["file"]["sha256"],
        solver=saved["p3"]["solver"],ranges=prepay["ranges"])
    temp=folder/(".saved-p3-"+uuid.uuid4().hex)
    temp.mkdir()
    shutil.copyfile(folder/"recipe.json",temp/"recipe.json")
    shutil.copyfile(archive,temp/"saved-p3.npz")
    atomic_json(temp/"saved-p3.json",marker)
    binding,_,_=gpu(temp,seed)
    # Publish the JSON marker last. An interrupted copy cannot authorize a run.
    target=folder/"saved-p3.npz"
    if target.exists():
        reject_reparse(target)
        need(target.stat().st_size==archive.stat().st_size and sha_file(target)==binding["p3_sha256"],
             "GPU_SAVED_P3_INCOMPLETE_BINDING")
    else:
        (temp/"saved-p3.npz").rename(target)
    (temp/"saved-p3.json").rename(folder/"saved-p3.json")
    for staged in ("recipe.json","saved-p3.npz"):
        asset=temp/staged
        if asset.is_file():asset.unlink()
    temp.rmdir()
    return dict(project=str(project),binding=binding,fit_count=0,scalar_CG=0)
