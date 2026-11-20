"""GM-005 fit publication. Standard library only; never evaluates a model.

Lock order: project.lock -> operation record.lock. The complete publication
marker is the single commit decision; pointers and operation status follow it.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import time
import zipfile

from .common import GMError, atomic_json, canonical_json, sha_bytes, sha_file, strict_load, writable_path
from .engine.independent_r0.profile import CORE_SHA256, PROFILE_ID, PROFILE_CONTRACT_SHA256, SETTINGS
from .historical_transactions import transaction_profile,parent_profile,transaction_settings
from .engine import profiles
from .project import _canonical_project_root, _read_project_manifest, _resolve_project_relative_file
from .store import locked
from .winproc import current_identity, identity_alive

CONTRACT = "gm007-fit-v1"
REVISION = "r000002"
ASSETS = ("recipe.json", "parameters.npz", "project-revision.json", "artifacts.json")
# Frozen data layout only, independent of importing the torch adapter.
SHAPES = profiles.get_profile(profiles.R0)["shapes"]

def assets(spec):
    return ASSETS + (("p3.npz",) if spec["semantics"].get("profile_id")==profiles.FINE else ())

def operation_assets(spec):
    base=("compute-result.json","cleanup.json","fit-identity.json","fit-parent.json","fit-target.json","fit-ownership.json")
    if spec["semantics"].get("profile_id")!=profiles.FINE:return base
    p3_receipt="saved-p3-consumption.json" if "saved_p3" in spec["semantics"] else "solver-cleanup.json"
    return base+(p3_receipt,"fine-proof.json","fine-counts.json")


def _fail(message, code="FIT_RECOVERY_CONFLICT"):
    raise GMError(code, message, 7)


def is_current(spec):
    return spec.get("kind") == "fit" and spec.get("semantics", {}).get("fit_contract") in {CONTRACT, "gm005-fit-v1", "gm006-fit-v1", "gm008-fit-v1"}


@contextmanager
def operation_lock(path, spec):
    if is_current(spec):
        root = writable_path(Path(spec["semantics"]["project_path"]))
        with locked(root / "project.lock"), locked(path / "record.lock"):
            yield
    else:
        with locked(path / "record.lock"):
            yield


def intent(project, output):
    root = _canonical_project_root(writable_path(Path(project)))
    manifest, forward = _read_project_manifest(root)
    if forward or manifest["schema_version"] not in {"0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"}:
        _fail("Unsupported project schema is read-only", "PROJECT_READ_ONLY")
    parent = _resolve_project_relative_file(root, "revisions/r000001/recipe.json")
    parent_recipe = strict_load(parent)
    if parent_recipe.get("artifact_kind") != "metadata_only":
        _fail("Project has no new-pair parent", "PROJECT_NOT_NEW_PAIR")
    try:
        profile = profiles.project_profile(parent_recipe,manifest['extensions'].get('algorithm_contract_sha256'))
    except GMError as exc:
        if exc.code=="ALGORITHM_RETIRED":raise
        _fail("Unknown required runtime is read-only", "PROJECT_READ_ONLY")
    result = {"fit_contract":profile["fit_contract"],"project_path":os.path.normcase(str(root)),
            "project_id":manifest["project_id"],"parent_revision":"r000001",
            "parent_recipe_sha256":sha_file(parent),"revision":REVISION,
            "source_sha256":manifest["source"]["byte_sha256"],
            "reference_sha256":manifest["reference"]["byte_sha256"],
            "profile_id":profile["id"],"profile_contract_sha256":profile["contract_sha256"],
            "core_sha256":profile["core_sha256"],"output_root":os.path.normcase(str(output))}
    if profile["id"] != "independent-b0-v1":
        if manifest["extensions"].get("algorithm_contract_sha256") != profile["contract_sha256"]:
            _fail("R0 project profile contract differs")
        io=manifest["extensions"].get("algorithm_io")
        if not isinstance(io,dict) or set(io)!={"training_hw","input_normalization"}:
            _fail("R0 project has no frozen input header contract")
        norm=io["input_normalization"]
        if (not isinstance(norm,dict) or set(norm)!={"source","reference"} or
                any(not isinstance(norm[k],dict) or norm[k].get("dtype") not in {"uint8","uint16"} for k in norm) or
                norm!=profiles.normalization(norm["source"],norm["reference"])):
            _fail("R0 frozen normalization differs")
        result.update(settings=profiles.settings_for(profile["id"],io["training_hw"]),input_normalization=norm)
    from .refinement import checked_seed
    seed=checked_seed(root,manifest)
    if seed is not None:
        result['refinement_seed']=seed
        from .saved_post import gpu
        retained=gpu(root/"refinement",seed)
        if retained is not None:result['saved_p3']=retained[0]
    return result


def _ownership(path, spec):
    return {"job_id":spec["job_id"],"attempt_id":spec["attempt_id"],
            "intent_sha256":spec["intent_sha256"],"operation_path":str(path)}


def _same_claim(claim, path, spec):
    return isinstance(claim, dict) and all(claim.get(k) == v for k,v in _ownership(path,spec).items())


def _clear_claim_locked(root, path, spec, reason):
    claim_path=root/"fit-claim.json"
    if not claim_path.is_file(): return
    claim=strict_load(claim_path)
    if _same_claim(claim,path,spec):
        atomic_json(path/"fit-claim-closed.json", {**claim,"closed_at":time.time(),"reason":reason})
        claim_path.unlink()


def claim_project(ops, path, spec):
    root=writable_path(Path(spec["semantics"]["project_path"]))
    # Reconcile a previous crashed operation without holding project.lock twice.
    claim_path=root/"fit-claim.json"
    prior=strict_load(claim_path) if claim_path.is_file() else None
    if prior and not _same_claim(prior,path,spec) and identity_alive(prior.get("owner",{})) is False:
        prior_path=writable_path(Path(prior["operation_path"]))
        from .gm004a_operations import OperationStore
        old=OperationStore(prior_path.parents[2])
        check, old_spec=old.path(prior["job_id"],prior["attempt_id"])
        if check != prior_path or not _same_claim(prior,check,old_spec): _fail("Stale claim binding differs")
        old.status(prior["job_id"],prior["attempt_id"])
    with locked(root/"project.lock"):
        if claim_path.is_file():
            prior=strict_load(claim_path)
            if _same_claim(prior,path,spec): _fail("A fit cannot restart its compute", "FIT_ALREADY_STARTED")
            live=identity_alive(prior.get("owner",{}))
            _fail("This project already has a fit operation", "FIT_PROJECT_BUSY" if live is True and
                  prior.get("expires_at",0)>time.time() else "FIT_CLAIM_REVIEW_REQUIRED")
        manifest, forward=_read_project_manifest(root)
        if forward or manifest["schema_version"] != "0.1.0" or manifest["current_revision"] != "r000001":
            _fail("A new metadata-only pair is required", "PROJECT_NOT_NEW_PAIR")
        if intent(root,Path(spec["semantics"]["output_root"])) != spec["semantics"]:
            _fail("Fit input or parent identity changed")
        parent_hash=sha_bytes(canonical_json(manifest))
        atomic_json(path/"fit-parent.json",manifest)
        owner=current_identity()
        if not isinstance(owner,dict) or owner.get("unknown"): _fail("Fit owner identity unavailable")
        claim={**_ownership(path,spec),"owner":owner,"expires_at":spec["operation_deadline_at"],
               "parent_manifest_sha256":parent_hash,"project_id":manifest["project_id"]}
        atomic_json(path/"fit-ownership.json",claim)
        atomic_json(claim_path,claim)
        return manifest


def abandon_claim(path,spec):
    if not is_current(spec): return
    root=writable_path(Path(spec["semantics"]["project_path"]))
    with locked(root/"project.lock"):
        if not (path/"fit-publication.json").exists():
            _clear_claim_locked(root,path,spec,"UNPUBLISHED_OPERATION_TERMINAL")


def validate_archive(file, expected_values, *, shapes=None):
    """Bounded ZIP/NPY structural and byte checks; no np.load/pickle/model."""
    if file.stat().st_size > 256*1024: _fail("Parameter archive exceeds quota")
    shapes=SHAPES if shapes is None else shapes
    values=hashlib.sha256()
    try:
        with zipfile.ZipFile(file) as archive:
            entries=archive.infolist()
            if (len(entries)!=len(shapes) or {e.filename for e in entries}!={k+".npy" for k in shapes}
                    or sum(e.file_size for e in entries)>256*1024
                    or any(e.flag_bits&1 or e.file_size>64*1024 for e in entries)):
                _fail("Parameter archive member layout differs")
            for key,shape in sorted(shapes.items()):
                data=archive.read(key+".npy")
                if data[:8]!=b"\x93NUMPY\x01\x00" or len(data)<10: _fail("Unsupported NPY header")
                length=struct.unpack("<H",data[8:10])[0]
                if length>2048 or 10+length>len(data): _fail("NPY header quota")
                header=ast.literal_eval(data[10:10+length].decode("ascii").strip())
                payload=data[10+length:]
                if header!={"descr":"<f4","fortran_order":False,"shape":shape} or len(payload)!=math.prod(shape)*4:
                    _fail("NPY shape, dtype or payload differs")
                if any(not math.isfinite(v[0]) for v in struct.iter_unpack("<f",payload)):
                    _fail("Parameter payload is nonfinite")
                values.update(key.encode()+b"\0"); values.update(payload)
    except (OSError,ValueError,SyntaxError,zipfile.BadZipFile,KeyError,struct.error) as exc:
        _fail("Unreadable parameter archive: "+str(exc))
    if values.hexdigest()!=expected_values: _fail("Parameter values digest differs")


def prepare(path,spec,recipe_dir,compute,half_hash):
    from .portable_project import promote_fitted_recipe
    parent=strict_load(path/"fit-parent.json")
    target=promote_fitted_recipe(spec["semantics"]["project_path"],recipe_dir,
        result_float32_sha256=compute["float32_sha256"],half_q16_sha256=half_hash,
        _staging_root=path/"fit-prepared",_expected_parent_sha256=sha_bytes(canonical_json(parent)))
    atomic_json(path/"fit-target.json",target)
    from .gm004a_operations import artifact
    files=[path/name for name in operation_assets(spec)]
    files += [path/"fit-prepared/revisions"/REVISION/name for name in assets(spec)]
    return {"state":"COMPLETE",**{k:spec[k] for k in ("job_id","attempt_id","intent_sha256","kind")},
            "artifacts":[artifact(p) for p in files],"result_lease":compute["lease"],
            "fit_publication":{"schema_version":"1","core_sha256":spec["semantics"]["core_sha256"],
                "parent_manifest_sha256":sha_bytes(canonical_json(parent)),
                "target_manifest_sha256":sha_bytes(canonical_json(target))}}


def verify(manifest,spec,path,by_path):
    from .portable_project import _read_portable_revision, _canonical_digest
    from .gm004a_operations import OperationStore
    semantic=spec["semantics"]
    profile=transaction_profile(semantic)
    if (semantic.get("fit_contract")!=profile["fit_contract"] or semantic.get("core_sha256")!=profile["core_sha256"]
            or semantic.get("profile_contract_sha256")!=profile["contract_sha256"]
            or semantic.get("profile_id")!=profile["id"] or semantic.get("revision")!=REVISION):
        _fail("Unsupported fit contract")
    exact={os.path.normcase(str((path/name).resolve())) for name in operation_assets(spec)}
    exact.update(os.path.normcase(str((path/"fit-prepared/revisions"/REVISION/name).resolve())) for name in assets(spec))
    if set(by_path)!=exact: _fail("Fit publication artifact set differs")
    read=lambda name:OperationStore._receipt(by_path,path/name)
    parent,target=read("fit-parent.json"),read("fit-target.json")
    pub=manifest.get("fit_publication")
    if pub!={"schema_version":"1","core_sha256":spec["semantics"]["core_sha256"],
             "parent_manifest_sha256":sha_bytes(canonical_json(parent)),
             "target_manifest_sha256":sha_bytes(canonical_json(target))}: _fail("Fit snapshot binding differs")
    if (parent.get("project_id")!=semantic.get("project_id") or parent.get("schema_version")!="0.1.0"
            or parent.get("current_revision")!=semantic.get("parent_revision")
            or parent.get("source",{}).get("byte_sha256")!=semantic.get("source_sha256")
            or parent.get("reference",{}).get("byte_sha256")!=semantic.get("reference_sha256")):
        _fail("Fit parent or input binding differs")
    expected={**parent,"schema_version":profile["version"],"current_revision":REVISION,
              "revisions":[*parent["revisions"],REVISION],
              "extensions":{**parent["extensions"],"import_origin":"new-pair-fit"}}
    if target!=expected: _fail("Target manifest is not the bound parent successor")
    own=read("fit-ownership.json")
    if not _same_claim(own,path,spec) or own.get("parent_manifest_sha256")!=pub["parent_manifest_sha256"]:
        _fail("Fit ownership differs")
    root=writable_path(Path(semantic["project_path"]))
    from .refinement import checked_seed,expected_fit_count
    seed=checked_seed(root,parent)
    if seed!=semantic.get('refinement_seed')or(seed is not None and (profile['id']!=profiles.FINE or read('compute-result.json').get('refinement_seed')!=seed)):_fail('Refinement parent proof differs')
    fitted=expected_fit_count(semantic)
    if sha_file(_resolve_project_relative_file(root,"revisions/r000001/recipe.json"))!=semantic["parent_recipe_sha256"]:
        _fail("Immutable parent recipe differs")
    revision=_read_portable_revision(path/"fit-prepared",target,REVISION)
    recipe=read("fit-prepared/revisions/r000002/recipe.json")
    parent_recipe=strict_load(_resolve_project_relative_file(root,"revisions/r000001/recipe.json"))
    if parent_profile(parent_recipe)["id"] != profile["id"]:
        _fail("Publication profile differs from frozen project")
    if (recipe.get("core_sha256")!=profile["core_sha256"] or recipe.get("profile_id")!=profile["id"]
            or recipe.get("profile_contract_sha256")!=profile["contract_sha256"]
            or recipe.get("settings")!=transaction_settings(profile["id"],recipe.get("training_shape",[])[:2]) or recipe.get("recipe_kind")!="development_image"
            or recipe.get("recipe_schema_version")!=profile["version"] or recipe.get("production_qualified") is not False
            or recipe.get("recipe_sha256")!=_canonical_digest(recipe)):
        _fail("Recipe profile or core binding differs")
    validate_archive(path/"fit-prepared/revisions/r000002/parameters.npz",recipe.get("parameter_sha256"), shapes=profile["shapes"])
    if profile["id"]==profiles.FINE:
        from .fine_contract import validate_recipe
        validate_recipe(recipe,path/"fit-prepared/revisions/r000002/p3.npz")
    if profile["id"] != "independent-b0-v1":
        if (recipe.get("settings") != semantic.get("settings") or recipe.get("input_normalization") != semantic.get("input_normalization")
                or recipe.get("input_normalization") != profiles.normalization(recipe["source_identity"],recipe["reference_identity"])):
            _fail("R0 fixed settings or I/O binding differs")
    compute,cleanup,identity=read("compute-result.json"),read("cleanup.json"),read("fit-identity.json")
    if profile["id"]==profiles.FINE:
        from .fine_contract import validate_compute
        saved_p3=semantic.get('saved_p3')
        validate_compute(compute,recipe['postprocess']['hashes'],replay=saved_p3 is not None,ranges=recipe['postprocess']['ranges'])
        proof=read('fine-proof.json');counts=read('fine-counts.json')
        if saved_p3 is not None:
            retained=read('saved-p3-consumption.json')
            if retained!=dict(binding=saved_p3,scalar_CG=0,range_discovery=0) or recipe['postprocess']['coefficient_sha256']!=saved_p3['coefficient_sha256']:_fail('Saved P3 proof differs')
        else:
            child=read('solver-cleanup.json')
            if (child.get('actual_wait')is not True or child.get('owned_tree_empty')is not True or child.get('identity_exited')is not True or child.get('forced')is not False or child.get('exit_code')!=0):_fail('Fine CPU child exit proof differs')
        if proof.get('status')!='PASS'or proof.get('hashes')!=recipe['postprocess']['hashes']or proof.get('witnesses')!=recipe['postprocess']['witnesses']or counts.get('status')!='PASS'or counts.get('actual')!=compute['postprocess_counts']:_fail('Fine completed stage proof differs')
    if profile["id"] != "independent-b0-v1" and (compute.get("profile_id") != profile["id"] or
            compute.get("profile_contract_sha256") != profile["contract_sha256"]):
        _fail("Compute profile proof differs")
    lease=manifest.get("result_lease")
    if (compute.get("status")!="PASS" or compute.get("mode")!="development_pair"
            or compute.get("fit_count")!=fitted or compute.get("render_count")!=1
            or compute.get("recipe_sha256")!=recipe["recipe_sha256"] or compute.get("lease")!=lease
            or not isinstance(lease,dict) or lease.get("project_revision")!=REVISION
            or lease.get("recipe_sha256")!=recipe["recipe_sha256"] or lease.get("state")!="RESIDENT"):
        _fail("Fit compute proof differs")
    for name in ("source","reference"):
        if (compute.get(name+"_identity")!=recipe.get(name+"_identity")
                or recipe.get(name+"_sha256")!=semantic[name+"_sha256"]
                or identity.get(name+"_sha256")!=semantic[name+"_sha256"]): _fail("Fit input proof differs")
    if (cleanup.get("mode")!="development_pair" or cleanup.get("worker_exit_code")!=0
            or cleanup.get("actual_wait") is not True or cleanup.get("owned_processes_exited") is not True
            or identity.get("worker_cleanup")!=cleanup or identity.get("fit_count")!=fitted or identity.get("render_count")!=1
            or identity.get("recipe_sha256")!=recipe["recipe_sha256"]
            or identity.get("float32_sha256")!=compute.get("float32_sha256")):
        _fail("Fit identity or actual worker wait proof differs")
    for value in (compute.get("float32_sha256"),identity.get("half_q16_sha256")):
        if not isinstance(value,str) or len(value)!=64 or any(c not in "0123456789abcdef" for c in value): _fail("Invalid result digest")
    index=read("fit-prepared/revisions/r000002/artifacts.json")
    if index.get("result_float32_sha256")!=compute["float32_sha256"] or index.get("half_q16_sha256")!=identity["half_q16_sha256"]:
        _fail("Immutable result proof differs")


def publish(ops,path,spec,manifest):
    from .gm004a_operations import _journal
    with operation_lock(path,spec):
        current=_journal(path)[-1]["snapshot"]
        if current["state"]!="RUNNING" or (path/"cancel.signal").exists():
            raise GMError("CANCELLED","Cancellation precedes fit publication",8)
        ops._verify_manifest(manifest,spec,path)
        parent=strict_load(path/"fit-parent.json")
        root=writable_path(Path(spec["semantics"]["project_path"]))
        if strict_load(root/"project.json")!=parent: _fail("Project changed before publication")
        claim=strict_load(root/"fit-claim.json")
        if not _same_claim(claim,path,spec) or claim["expires_at"]<=time.time(): _fail("Fit claim expired or changed")
        if (path/"fit-publication.json").exists(): _fail("Duplicate fit publication")
        atomic_json(path/"fit-publication.json",manifest)  # Linearization point.


def _install_member_state(file, source, expected, *, partial):
    """Only a complete bound file or its exact short prefix is a known remnant."""
    writable_path(file)  # Check the original entry and all parents before reads.
    if not file.is_file(): _fail("Installation member is not a regular file")
    size=file.stat().st_size
    if size==expected["bytes"] and sha_file(file)==expected["sha256"]:
        return True
    if not partial or size>=expected["bytes"]:
        _fail("Installation member conflicts with publication")
    with file.open("rb") as fragment,source.open("rb") as original:
        for block in iter(lambda:fragment.read(64*1024),b""):
            if block!=original.read(len(block)):
                _fail("Installation fragment is not a source prefix")
    return False


def _install_directory(directory, sources, *, partial):
    writable_path(directory)
    if not directory.is_dir(): _fail("Installation path is not a directory")
    allowed=set(sources)
    if partial:allowed.update(".gm005-"+name+".partial" for name in sources)
    entries=list(directory.iterdir())
    if any(entry.name not in allowed for entry in entries):
        _fail("Unknown installation member")
    if not partial and {entry.name for entry in entries}!=set(sources):
        _fail("Installed revision member set differs")
    for entry in entries:
        name=entry.name
        if name not in sources:name=name[len(".gm005-"):-len(".partial")]
        source,expected=sources[name]
        _install_member_state(entry,source,expected,partial=partial)


def _recover_revision_install(root,path,spec,manifest):
    """Install only this verified publication's private, bounded namespace.

    Ownership comes from the validated job/attempt/fit-ownership publication,
    exact project parent and attempt directory name, plus the member allowlist
    and prefix proof. This also admits old direct-copy short files without
    inventing ownership receipts after the crash. Foreign/full corrupt bytes
    remain conflicts. No recursive cleanup or writes to formal revision files.
    """
    revisions=writable_path(root/"revisions")
    final=writable_path(revisions/REVISION)
    temporary=writable_path(revisions/(".gm005-"+spec["attempt_id"]))
    if temporary.parent!=revisions or temporary.name!=".gm005-"+spec["attempt_id"]:
        _fail("Installation directory escapes the attempt namespace")
    for entry in revisions.iterdir():
        if entry.name.startswith(".gm005-") and entry!=temporary:
            _fail("Another attempt installation is present")
    by_path={os.path.normcase(item["path"]):item for item in manifest["artifacts"]}
    sources={}
    for name in assets(spec):
        source=path/"fit-prepared/revisions"/REVISION/name
        sources[name]=(source,by_path[os.path.normcase(str(source.resolve()))])
    if final.exists():
        _install_directory(final,sources,partial=False)
        if temporary.exists(): _fail("Formal and private installations both exist")
        return final
    if temporary.exists():
        # Validate every entry before repairing any known remnant.
        _install_directory(temporary,sources,partial=True)
    else:temporary.mkdir()
    for name,(source,expected) in sources.items():
        destination=writable_path(temporary/name)
        pending=writable_path(temporary/(".gm005-"+name+".partial"))
        if destination.exists() and _install_member_state(destination,source,expected,partial=True):
            if pending.exists():
                _install_member_state(pending,source,expected,partial=True)
                pending.unlink()  # Verified literal entry only; formal files are untouched.
            continue
        if pending.exists() and not _install_member_state(pending,source,expected,partial=True):
            pending.unlink()
        if not pending.exists():
            with source.open("rb") as src,pending.open("xb") as output:
                shutil.copyfileobj(src,output);output.flush();os.fsync(output.fileno())
        _install_member_state(pending,source,expected,partial=False)
        os.replace(pending,destination)
    _install_directory(temporary,sources,partial=False)
    os.rename(temporary,final)
    _install_directory(final,sources,partial=False)
    return final


def recover_locked(ops,path,spec,current):
    marker=path/"fit-publication.json"
    if not is_current(spec) or not marker.is_file(): return None
    from .gm004a_operations import _append
    from .portable_project import _read_portable_revision
    manifest=strict_load(marker); ops._verify_manifest(manifest,spec,path)
    root=writable_path(Path(spec["semantics"]["project_path"]))
    target=strict_load(path/"fit-target.json"); parent=strict_load(path/"fit-parent.json")
    active,forward=_read_project_manifest(root)
    if forward or active["project_id"]!=parent["project_id"]: _fail("Project identity or schema changed after publication")
    profile=transaction_profile(spec["semantics"])
    _recover_revision_install(root,path,spec,manifest)
    if active==parent:
        atomic_json(root/"project.json",target)
    elif active!=target:
        # A later legitimate revision/relink is retained; never rewind a pointer.
        if (active["schema_version"]!=profile["version"] or REVISION not in active["revisions"]
                or active["revisions"].index(active["current_revision"])<active["revisions"].index(REVISION)
                or active["source"]["byte_sha256"]!=target["source"]["byte_sha256"]
                or active["reference"]["byte_sha256"]!=target["reference"]["byte_sha256"]):
            _fail("Active project is not the committed lineage")
        _read_portable_revision(root,active,active["current_revision"])
    _read_portable_revision(root,target,REVISION)
    commit=path/"commit.json"
    if commit.is_file():
        if strict_load(commit)!=manifest: _fail("Operation commit conflicts with publication")
    else: atomic_json(commit,manifest)
    if current["state"]!="SUCCEEDED" or current.get("exit_code")!=0 or current.get("error_code") is not None:
        current.update(state="SUCCEEDED",stage="recovered",exit_code=0,error_code=None,result_lease=manifest["result_lease"])
        _append(path,current,"OPERATION_RECOVERED_FIT_PUBLICATION")
    _clear_claim_locked(root,path,spec,"FIT_COMMITTED")
    return current


def finish(ops,path,spec):
    from .gm004a_operations import _journal
    with operation_lock(path,spec):
        return recover_locked(ops,path,spec,dict(_journal(path)[-1]["snapshot"]))
