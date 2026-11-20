"""Preview-specific preparation around the established CPU workers and codec."""
import gc
import hashlib
import math
from pathlib import Path

from ..common import atomic_json, strict_load, writable_path
from ..cpu_replay.contract import binding, need
from . import contract as k


def prepare(host):
    import numpy as np
    from multiprocessing.shared_memory import SharedMemory
    from ..cpu_first_fit.host import _worker
    from ..cpu_recipe.generated import complete, publish
    from ..cpu_replay.host import array_hash
    from ..engine import group15_codec as codec

    k.runtime_check("io")
    c = host.context
    host.check()
    from .startup_fixture import active as startup_chain, enabled as startup_fixture_enabled
    engineering = startup_chain()
    need(not startup_fixture_enabled() or engineering, "GM080_FIXTURE_REQUIRES_FROZEN_PLAN")
    host.sem = dict(host.sem)
    identities = {}
    from ..light.eta import PhaseDurations
    clock = PhaseDurations() if c.get("light_plan") else None

    def phase(stage):
        if clock:
            clock.mark(stage)

    def decode(name):
        host.check()
        host.progress("read_" + name)
        phase("read_" + name)
        reader = codec.read_source if name == "source" else codec.read_reference
        from ..adaptive_execution.c2_runtime_pins import RuntimePins
        from os.path import normcase
        source = c[name]
        build = c["executor_build"]["sha256"]
        pin = RuntimePins({normcase(source["path"]): source["sha256"]}, build, c["package"])
        try:
            pin.begin(build, "entry")
            need(pin.bind(source["path"]) == source, "PREVIEW_PHOTO_PIN")
            data, info = reader(source["path"], strict_group15=False, development_pair=True)
        finally:
            pin.close()
        actual, expected = k.photo_identity(info), c[name+"_identity"]
        need(dict(actual, pixel_sha256=expected["pixel_sha256"]) == expected
             and (expected["pixel_sha256"] is None or actual["pixel_sha256"] == expected["pixel_sha256"])
             and list(data.shape) == expected["shape"] and str(data.dtype) == expected["dtype"],
             "PREVIEW_DECODE_IDENTITY")
        identities[name+"_identity"] = actual
        host.count(**{name+"_decode": 1})
        return data, info

    from ..light.resources import host_boundary, shared_object
    host_boundary(host.session, "cpu", "decode", point="cpu-decode")
    original, info = decode("source")
    shape, dtype = original.shape, original.dtype
    host.smap = SharedMemory(create=True, size=original.nbytes)
    host.omap = SharedMemory(create=True, size=math.prod(shape)*4)
    host.s = np.ndarray(shape, dtype, buffer=host.smap.buf)
    host.o = np.ndarray(shape, np.float32, buffer=host.omap.buf)
    host.s[:] = original
    host.s.flags.writeable = False
    del original
    host.count(source_copy=1)
    host.icc = info["icc_bytes"]
    host.source_info = info
    source_map = dict(name=host.smap.name, shape=list(shape), dtype=str(dtype), readonly=True)
    result_map = dict(name=host.omap.name, shape=list(shape), dtype="float32")
    mappings = dict(S=dict(name=host.smap.name, bytes=host.s.nbytes),
                    O=dict(name=host.omap.name, bytes=host.o.nbytes), full_O_allocations=1)
    base = dict(S=source_map, cancel=str(host.path / "cancel.signal"), operation=str(host.path),
                reservation=c["science"])
    atomic_json(host.session / "shared-mappings.json", mappings)
    directory = Path(c["bundle"])
    render_directory=Path(c.get("render_bundle") or directory)
    if engineering:
        atomic_json(host.session / "gm080-synthetic-decode.json",
                    dict(identity=identities["source_identity"], maps=mappings, photo_decodes=0))
        host.progress("render_start")
        host_boundary(host.session, "cpu", "R0", point="cpu-r0",
                      buffers=(shared_object("source_rgb", host.smap, host.s),
                               shared_object("O", host.omap, host.o)))
        _worker(host, "r0", dict(base, O=result_map, bundle=str(render_directory), work=str(host.session / "R0")))
        need(False, "GM080_BARRIER_RETURNED_WITHOUT_CANCEL")
    if c["operation"] == "fit":
        ref, _ = decode("reference")
        mapping = SharedMemory(create=True, size=ref.nbytes)
        view = None
        try:
            view = np.ndarray(ref.shape, ref.dtype, buffer=mapping.buf)
            view[:] = ref
            desc = dict(name=mapping.name, shape=list(ref.shape), dtype=str(ref.dtype), readonly=True)
            del ref
            atomic_json(host.session / "shared-mappings.json",
                        dict(mappings, reference=dict(name=mapping.name, bytes=view.nbytes)))
            host.progress("fit_start")
            phase("fit")
            host_boundary(host.session,"cpu","training_prepare",point="cpu-fit-prepare",
                          buffers=(shared_object("source_rgb",host.smap,host.s),
                                   shared_object("reference_rgb",mapping,view)))
            work = _worker(host, "fit", dict(base, reference=desc, work=str(host.session / "fit")))
        finally:
            del view
            mapping.close()
            mapping.unlink()
        host.check()
        host.progress("save_fast_recipe")
        phase("save_fast_recipe")
        receipt = strict_load(work / "fit-receipt.json")
        intent = dict(schema="gm065-preview-fit-v1", job_id=host.spec["job_id"],
                      attempt_id=host.spec["attempt_id"], input_headers=c["headers"])
        publish(directory, work / "parameters.npz", intent, receipt, dict(c, **identities))
    if c["operation"] != "fit":
        phase("reuse_fast_parameters")
    if c["operation"]=="fine-replay":
        from ..cpu_recipe.generated_fine import complete as complete_fine
        m,_=complete_fine(render_directory)
    else:
        m,_=complete(render_directory)
    if c["operation"] != "fit":
        need((m["bundle_id"], m["math_id"]) == (host.sem["bundle_id"], host.sem["math_id"]),
             "PREVIEW_REPLAY_RECIPE_CHANGED")
    host.sem.update(bundle_id=m["bundle_id"], math_id=m["math_id"])
    atomic_json(host.session / "render-authorization.json", dict(
        parent_context=host.sem["instance_context"], bundle_id=m["bundle_id"], math_id=m["math_id"],
        parameter_sha256=m["mathematical"]["r0_parameters"]["value_sha256"],
        executor_build=c["executor_build"]["sha256"], bundle_complete=binding(render_directory / "COMPLETE.json")))
    host.check()
    host.progress("render_start")
    phase("render")
    host_boundary(host.session, "cpu", "R0", point="cpu-r0",
                  buffers=(shared_object("source_rgb", host.smap, host.s),
                           shared_object("O", host.omap, host.o)))
    work = _worker(host, "r0", dict(base, O=result_map, bundle=str(render_directory), work=str(host.session / "R0")))
    result = strict_load(work / "r0-result.json")
    total = math.ceil(shape[0]/512)*math.ceil(shape[1]/512)
    need(result["status"] == "COMPLETE" and result["counts"]["forward_batches"] == total
         and result["counts"]["completed_tiles"] == total, "PREVIEW_R0_WORK")
    host.count(R0_render=1, forward=total)
    host.o.flags.writeable = False
    hashes=dict(O=array_hash(host.o))
    if c["mode"]=="fine":
        fine(host,c,render_directory,directory,decode,hashes,phase)
    else:
        host.y=host.o
    host.progress("host_handoff")
    phase("host_handoff")
    host.finish_prepare(info,result,hashes,False,phase_clock=clock)


def fine(host,c,parent,directory,decode,hashes,phase):
    from multiprocessing.shared_memory import SharedMemory
    import numpy as np
    from ..cpu_refine.host import worker,p3_metadata
    from ..cpu_recipe.generated_fine import complete,publish
    from ..common import sha_file
    staging=directory
    if c["operation"]=="refine":
        host.progress("read_reference")
        reference,_=decode("reference")
        mapping=SharedMemory(create=True,size=reference.nbytes)
        view=None
        try:
            view=np.ndarray(reference.shape,reference.dtype,buffer=mapping.buf)
            view[:]=reference
            shape=list(view.shape)
            dtype=str(view.dtype)
            del reference
            host.progress("H_area")
            phase("P3_CONSTRUCTION_AND_SOLVE")
            from ..light.resources import host_boundary, shared_object
            host_boundary(host.session,"cpu","P3_prepare",point="cpu-p3-prepare",
                          buffers=(shared_object("source_rgb",host.smap,host.s),
                                   shared_object("O",host.omap,host.o),
                                   shared_object("reference_rgb",mapping,view)))
            work=worker(host,"prepare",dict(work=str(host.session/"P3"),
                O=dict(name=host.omap.name,shape=list(host.o.shape),dtype="float32",readonly=True),
                reference=dict(name=mapping.name,shape=shape,dtype=dtype,readonly=True),
                reference_wh=[shape[1],shape[0]],cancel=str(host.path/"cancel.signal")),
                timeout=2400)
        finally:
            del view
            mapping.close()
            mapping.unlink()
        prepared=strict_load(work/"prepare-result.json")
        p3=p3_metadata(c,prepared,work/"b.npz")
        staging=directory.with_name("."+directory.name+"-"+host.spec["attempt_id"])
        intent=dict(semantics=host.spec["semantics"],job_id=host.spec["job_id"],attempt_id=host.spec["attempt_id"])
        publish(staging,parent,work/"b.npz",prepared["solver"],p3,intent,c)
    manifest,_=complete(staging)
    host.sem.update(bundle_id=manifest["bundle_id"],math_id=manifest["math_id"],bundle_directory=str(staging))
    host.progress("CPU")
    phase("CPU")
    from ..light.resources import host_boundary, shared_object
    host_boundary(host.session,"cpu","CPU",point="cpu-fine",
                  buffers=(shared_object("source_rgb",host.smap,host.s),
                           shared_object("O",host.omap,host.o)))
    hashes.update(host.postprocess(False))
    host.o=None
    host.omap.close()
    host.omap.unlink()
    host.omap=None
    with host.receiver.prepared_view() as view:
        host.y=view.view()
    del view
    host.check()
    if staging!=directory:
        staging.rename(directory)
    host.sem["bundle_directory"]=str(directory)


def export_cancel_path(session, key):
    from ..adaptive_execution.portable_contract import preview_cpu_export
    fingerprint=hashlib.sha256(key.encode("utf8")).hexdigest()
    short=preview_cpu_export(Path(session),fingerprint)
    if short is not None:
        return short/"cancel.json"
    return Path(session)/"preview-export-cancel"/(fingerprint+".json")


def export(host, request):
    from ..engine import group15_codec as codec
    from ..export_metadata import policy_for_export_key
    from ..export_pixels import mix_quantize
    from ..gm004a_export import publish_export
    from ..gm004a_export import _request_dir
    from ..cpu_replay.host import array_hash
    from PIL import Image

    host.require_binding(request)
    host.check()
    source = host.context["source"]
    dest = writable_path(Path(request["destination"]))
    header = host.context["headers"]["source"]
    fmt, bits = header["format"], header["bits"]
    need(dest.is_absolute() and dest.parent.is_dir() and not dest.is_relative_to(k.ROOT)
         and k.SUFFIXES.get(dest.suffix.lower()) == fmt and dest != Path(source["path"]),
         "PREVIEW_EXPORT_SOURCE_FORMAT")
    key = request["request_key"]
    need(type(key) is str and 1 <= len(key) <= 128, "EXPORT_KEY_REQUIRED")
    from contextlib import contextmanager
    from ..store import locked
    from ..light.eta import PhaseDurations
    phases=PhaseDurations() if host.context.get("light_plan") else None
    def phase(stage):
        if phases:
            phases.mark(stage)
        host.progress(stage)
    marker = export_cancel_path(host.session, key)
    def check():
        host.check()
        if marker.exists():
            raise InterruptedError("CANCELLED")
    @contextmanager
    def publication():
        with locked(marker.with_suffix(".lock"),timeout=10):
            check()
            if host.context.get("light_plan"):
                phase("export_publish")
            yield
    from ..light.resources import host_boundary, shared_object
    resident=(shared_object("source_rgb",host.smap,host.s),)
    tx, _ = _request_dir(host.session, key)
    prior = tx / "intent.json"
    quantized = None
    phase("mixing" if phases else "encoding")
    if prior.is_file():
        expected = strict_load(prior)["request"]["expected_pixels_sha256"]
    else:
        host_boundary(host.session,"cpu","export",point="cpu-export-mix",buffers=resident)
        quantized = mix_quantize(host.s, host.y, request["alpha"], bits=bits, check=check)
        expected = array_hash(quantized)
        host.count(mix_traversals=1)
    fields = dict(request_key=key, alpha=request["alpha"], format=fmt, bit_depth=bits,
                  icc_policy="preserve_source" if host.icc else "none",
                  icc_sha256=hashlib.sha256(host.icc).hexdigest() if host.icc else None,
                  source_path=source["path"], source_sha256=source["sha256"],
                  recipe_sha256=host.sem["math_id"], lease_id=host.binding["handle_id"], lease_generation=1,
                  width=header["width"], height=header["height"], expected_pixels_sha256=expected,
                  executor_build=host.sem["executor_build"])
    if host.context.get("light_plan"):
        phase("source_exif")
    prior_request = strict_load(prior)["request"] if prior.is_file() else None
    fields.update(policy_for_export_key(source["path"], source["sha256"], prior=prior_request))

    def writer(path, req):
        nonlocal quantized
        check()
        if quantized is None:
            quantized = mix_quantize(host.s, host.y, req["alpha"], bits=bits, check=check)
            need(array_hash(quantized) == expected, "PREVIEW_EXPORT_RETRY_PIXELS")
        phase("encoding")
        if fmt == "jpeg":
            codec.write_jpg(path, quantized, host.icc)
        elif fmt == "tiff":
            codec.write_tiff(path, quantized, host.icc, byteorder=host.source_info.get("byteorder"))
        else:
            codec.write_common(path, quantized, fmt, host.icc)
        host.count(native_encode=1)
        quantized = None
        gc.collect()

    def readback(path, req):
        check()
        host_boundary(host.session,"cpu","readback",point="cpu-export-readback",buffers=resident)
        phase("metadata_verify")
        if fmt == "jpeg":
            with Image.open(path, formats=["JPEG"]) as image:
                need(image.mode == "RGB" and image.size == (header["width"], header["height"])
                     and image.info.get("icc_profile") == host.icc, "PREVIEW_JPEG_READBACK")
                image.load()
        elif fmt == "tiff":
            from .tiff_readback import verify as stream_readback
            proof = stream_readback(path, host.icc, expected, check=check)
            atomic_json(tx / "pixel-stream-readback.json", proof)
        else:
            codec.readback_common(path, fmt, host.icc, expected)
        host.count(native_full_readback=1)
        return {n: req[n] for n in ("alpha", "format", "bit_depth", "icc_sha256", "source_sha256",
                "recipe_sha256", "lease_id", "lease_generation", "width", "height")} | dict(pixels_sha256=expected)

    result=publish_export(host.session, dest, fields, writer, readback,
                          publication_guard=publication, metadata_check=check, metadata_progress=phase)
    if phases and result["status"]=="SUCCEEDED" and not result.get("idempotent_replay"):
        result=dict(result,light_phase_seconds=phases.completed())
    return result
