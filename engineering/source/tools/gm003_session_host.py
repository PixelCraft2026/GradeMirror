"""CPU/I/O ResultSession host, independent of the one-shot neural compute Job."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from multiprocessing.connection import Listener
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "_grademirror_portable", None):
    sys.path.insert(0, str(ROOT / "src"))
from grademirror.common import (atomic_json, strict_load, writable_path, under,
                                writable_destination, writable_output_root)
from grademirror.gm003_ipc import recv_array, recv_json, send_array, send_json
if __name__=='__main__'and sys.argv[1:]==['--gm023-entry-only']:
    print(json.dumps(dict(entry=__file__,science=0,executable=sys.executable)));raise SystemExit(0)
if (__name__ == '__main__' and (len(sys.argv) != 2 or sys.argv[1].startswith('--') or
        not (Path(sys.argv[1]) / 'session-spec.json').is_file())):
    print(json.dumps(dict(error='SESSION_ARGUMENT_INVALID', science=0)))
    raise SystemExit(1)
if (__name__ == '__main__' and len(sys.argv) == 2 and not sys.argv[1].startswith('--') and
        (Path(sys.argv[1]) / 'session-spec.json').is_file() and
        strict_load(Path(sys.argv[1]) / 'session-spec.json').get('backend_id') == 'r0-cpu-portable-v1'):
    from grademirror.cpu_replay.host import main as portable_host
    raise SystemExit(portable_host(Path(sys.argv[1])))
from grademirror.engine import group15_codec as codec


def _sha_array(array):
    return hashlib.sha256(memoryview(array).cast("B")).hexdigest()


def _serializable_info(info):
    return {key: value for key, value in info.items() if key != "icc_bytes"}


def _export_alpha(name: str) -> float:
    table = {"alpha1.tif": 1.0, "alpha0.5.tif": 0.5,
             "alpha1.jpg": 1.0, "alpha0.5.jpg": 0.5,
             "source.jpg": 0.0, "reference.jpg": 0.0}
    if name not in table:
        raise ValueError("EXPORT_NAME_INVALID")
    return table[name]


class Host:
    def __init__(self, session_dir: Path):
        if os.environ.get('GM063_LAUNCH_CONFIG'):
            from grademirror.adaptive_execution.calibration_launch import enter
            enter("session_host",session_dir/"host-observation")
        spec = strict_load(session_dir / "session-spec.json")
        from grademirror.resource_execution.binding import worker_check
        resource=spec.get('resource_execution')
        worker_check(session_dir.parent,'host',None if resource is None else resource['binding'])
        if spec.get('backend_id') is not None:
            from grademirror.hybrid_replay import BACKEND_ID
            from grademirror.hybrid_replay.contract import require, execution_contract
            require(spec['backend_id']==BACKEND_ID and spec['hybrid_execution']['plan']['execution_contract_sha256']==execution_contract()[1],
                    'HYBRID_SESSION_VERSION')
        if spec.get("mode") != "synthetic":
            from grademirror.engine.profiles import get_profile, retired_error
            if spec.get("mode") != "development_pair" or spec.get("profile_id") is None:
                raise retired_error()
            profile = get_profile(spec["profile_id"],spec.get('profile_contract_sha256'))
            if spec.get("profile_contract_sha256") != profile["contract_sha256"]:
                raise ValueError("SESSION_PROFILE_BINDING_INVALID")
        import numpy as np
        self.np = np
        self.session_dir = session_dir
        self.spec = spec
        raw_output = Path(self.spec["output_root"])
        if not raw_output.is_absolute() or raw_output != writable_output_root(raw_output):
            raise ValueError("OUTPUT_ROOT_NOT_ABSOLUTE_CANONICAL")
        self.source = None
        self.reference = None
        self.y = None
        self._fast_y_map = None
        self.lease = None
        self.fit_count = 0
        self.render_count = 0
        self.blend_count = 0
        self.exports = []
        self.preview_saves = []
        self.exports_v2 = []
        self.exit_reason = None
        self.started = time.time()
        self.source_info = None
        self.reference_info = None
        decode_started = time.perf_counter()
        if self.spec["mode"] == "synthetic":
            from PIL import ImageCms
            srgb_icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            grid = np.arange(17 * 19 * 3, dtype=np.uint16).reshape(17, 19, 3)
            self.source = (grid * np.uint16(67)).copy()
            self.reference = ((grid % 251).astype(np.uint8)).copy()
            self.source_info = {"synthetic": True, "shape": list(self.source.shape), "dtype": "uint16",
                                "color": codec._color(srgb_icc), "icc_bytes": srgb_icc}
            self.reference_info = {"synthetic": True, "shape": list(self.reference.shape), "dtype": "uint8",
                                   "color": codec._color(), "icc_bytes": b""}
        elif self.spec["mode"] == "development_pair":
            from grademirror.light.resources import active_plan, check_host
            active=active_plan()
            if active is not None:
                check_host(session_dir,active,"decode",point="resident-source-decode")
            self.source, self.source_info = codec.read_source(
                Path(self.spec["source_path"]), strict_group15=False,
                development_pair=True)
            if self.source.dtype not in (np.uint8, np.uint16) or self.source.ndim != 3 or self.source.shape[2] != 3:
                raise ValueError("SOURCE_PIXEL_CONTRACT")
        else:
            raise ValueError("HOST_MODE_INVALID")
        self.source.setflags(write=False)
        self.source_pixel_sha256 = _sha_array(self.source)
        self.source_identity = {"file_sha256": self.spec["source_sha256"],
            "file_size_bytes": self.spec["source_bytes"], "pixel_sha256": self.source_pixel_sha256,
            "shape": list(self.source.shape), "dtype": str(self.source.dtype),
            "bit_depth": self.source.dtype.itemsize * 8}
        atomic_json(session_dir / "source-decode.json", {
            "decode_seconds": time.perf_counter() - decode_started,
            "identity": self.source_identity, "info": _serializable_info(self.source_info),
            "decoded_by": "cpu_io_host"})
        self.hybrid = None
        if spec.get('backend_id') is not None:
            from grademirror.hybrid_replay.session import SessionAdapter
            if spec['hybrid_execution'].get('fit_contract')=='gm023-hybrid-fit-v1':
                from grademirror.hybrid_fit.session import SessionAdapter
            self.hybrid = SessionAdapter(self)
            import atexit
            atexit.register(self.hybrid.close)

    def _consumer_boundary(self, stage, *, point=None, quantized=None):
        from grademirror.light.resources import active_plan, check_host, owned_object, shared_object
        active = active_plan()
        if active is None:
            return
        if self.hybrid is not None:
            buffers = self.hybrid.output_objects()
        elif active["selection"]["backend"] == "gpu":
            buffers = [owned_object("source_rgb", self.source)]
            if self.y is not None:
                mapping = getattr(self, "_fast_y_map", None)
                buffers.append(shared_object("Y", mapping, self.y) if mapping is not None
                               else owned_object("Y", self.y))
        else:
            # Mixed constructs its shared mappings before the adapter is
            # attached. The full conservative demand applies until that
            # adapter can supply verified mapping descriptors.
            buffers = []
        if quantized is not None:
            buffers.append(owned_object("output_quantized", quantized))
        check_host(self.session_dir, active, stage, point=point, buffers=buffers)

    def _reference(self):
        if self.reference is None:
            self._consumer_boundary("reference_roi" if self.y is not None else "reference_fetch",
                                    point="resident-reference-decode")
            decode_started = time.perf_counter()
            development_pair = self.spec["mode"] == "development_pair"
            self.reference, self.reference_info = codec.read_reference(
                Path(self.spec["reference_path"]), strict_group15=not development_pair,
                development_pair=development_pair)
            if (self.reference.dtype not in (self.np.uint8, self.np.uint16) or self.reference.ndim != 3
                    or self.reference.shape[2] != 3 or
                    (not development_pair and list(self.reference.shape) != [1536, 1024, 3])):
                raise ValueError("REFERENCE_PIXEL_CONTRACT")
            self.reference.setflags(write=False)
            atomic_json(self.session_dir / "reference-decode.json", {
                "decode_seconds": time.perf_counter() - decode_started,
                "pixel_sha256": _sha_array(self.reference),
                "info": _serializable_info(self.reference_info), "decoded_by": "cpu_io_host"})
        return self.reference

    def _identity(self, array, file_sha256, file_size_bytes, bit_depth):
        return {"file_sha256": file_sha256, "file_size_bytes": file_size_bytes,
            "pixel_sha256": _sha_array(array), "shape": list(array.shape),
            "dtype": str(array.dtype), "bit_depth": bit_depth}

    def _require_lease(self, request):
        if not self.lease or self.lease["state"] != "RESIDENT":
            raise ValueError("RESULT_NOT_RESIDENT")
        # The host is authoritative. A caller that passed a stale client-side
        # check must never turn an expired lease back into a live one.
        if self.lease["expires_at"] <= time.time():
            self.lease["state"] = "LOST"
            atomic_json(self.session_dir / "lease.json", self.lease)
            raise ValueError("RESULT_NOT_RESIDENT")
        for key in ("handle_id", "generation", "owner_instance_id", "project_revision", "recipe_sha256"):
            if request.get(key) != self.lease[key]:
                raise ValueError("RESULT_NOT_RESIDENT")
        if self.lease.get('backend_id') is not None:
            from grademirror.lease import validate_lease_handle, BINDING_FIELDS
            from grademirror.hybrid_replay import VERSION_FIELDS
            keys=BINDING_FIELDS|VERSION_FIELDS
            validate_lease_handle(self.lease,binding={k:request[k] for k in keys if k in request})
        self.lease["expires_at"] = time.time() + 300
        atomic_json(self.session_dir / "lease.json", self.lease)

    def _export_event(self,event):
        if not hasattr(self,'session_dir'):return None
        from grademirror.fine_observation import BufferObservation
        if not hasattr(self,'_export_observation'):
            self._export_observation=BufferObservation(self.session_dir/'export-memory.jsonl',role='host')
        self._export_observation.track('source',self.source)
        if self.y is not None:self._export_observation.track('Y',self.y)
        try:
            self._export_observation.event(event)
        except OSError:
            # Observation is optional. Publication receipts are still required.
            pass
        return self._export_observation

    def _blend(self,alpha,*,bits=None,check=None,auxiliary_bits=None,export=False):
        from grademirror.export_pixels import mix_quantize
        self._consumer_boundary("export" if export else "blend", point="resident-export-mix" if export else "resident-blend")
        observation=self._export_event('mix_before');stats={};started=time.perf_counter()
        try:
            q=mix_quantize(self.source,self.y,alpha,bits=bits,check=check,stats=stats,
                auxiliary_bits=auxiliary_bits,observe=observation.track if observation else None)
            self.blend_count+=1;self._export_event('quantized')
            return q,stats
        finally:
            stats.update(seconds=time.perf_counter()-started,alpha=alpha if type(alpha)in(float,int)and math.isfinite(alpha)else repr(alpha)[:120],blend_count=self.blend_count)
            if hasattr(self,'session_dir'):
                try:
                    with (self.session_dir/'export-pixels.jsonl').open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(stats,separators=(',',':'))+'\n')
                except OSError:
                    pass

    def _close_fast_y(self):
        mapping = getattr(self, "_fast_y_map", None)
        if mapping is not None:
            self.y = None
            mapping.close()
            self._fast_y_map = None

    def _receive_y(self, conn, request):
        from grademirror.light.runtime import operation
        from grademirror.engine.profiles import R0

        active = operation()
        shared = request.get("shared_y")
        if shared is None:
            if active is not None:
                send_json(conn, {"ok": True, "handoff_ready": True})
            return recv_array(conn, expected_dtype="<f4", expected_shape=tuple(self.source.shape)), None

        import mmap
        import re
        from multiprocessing.shared_memory import SharedMemory

        if (sys.platform != "win32" or active is None or active["selection"]["backend"] != "gpu"
                or active["quality"] != "fast" or self.spec.get("profile_id") != R0
                or not isinstance(shared, dict) or set(shared) != {"name", "sha256"}
                or not re.fullmatch(r"gm-fast-y-[0-9a-f]{32}", str(shared["name"]))
                or not re.fullmatch(r"[0-9a-f]{64}", str(shared["sha256"]))):
            raise ValueError("SHARED_Y_BINDING_INVALID")
        expected_bytes = self.source.size * self.np.dtype("<f4").itemsize
        mapping = SharedMemory(name=shared["name"], create=False)
        try:
            if not expected_bytes <= mapping.size < expected_bytes + mmap.PAGESIZE:
                raise ValueError("SHARED_Y_SIZE_INVALID")
            # Pin the mapping first. Touch no pages until compute unmaps its view.
            send_json(conn, {"ok": True, "handoff_ready": True})
            if recv_json(conn) != {"shared_y_released": True}:
                raise ValueError("SHARED_Y_RELEASE_REQUIRED")
            return self.np.ndarray(self.source.shape, dtype="<f4", buffer=mapping.buf), mapping
        except BaseException:
            mapping.close()
            raise

    def handle(self, conn):
        request = recv_json(conn)
        if not isinstance(request, dict) or "command" not in request:
            raise ValueError("IPC_COMMAND_INVALID")
        command = request["command"]
        if command == "LIGHT_VALIDATION_QUANTIZATION":
            from grademirror.light.runtime import operation
            from grademirror.light.validation import quantization_rows
            from grademirror.engine.profiles import FINE

            active = operation()
            if not active or active.get("validation_evidence") is not True:
                raise ValueError("LIGHT_VALIDATION_EVIDENCE_REQUIRED")
            self._require_lease(request)
            if getattr(self, "_validation_quantized", False):
                raise ValueError("LIGHT_VALIDATION_EVIDENCE_ALREADY_CONSUMED")
            bits = (8, 16) if self.spec.get("profile_id") == FINE else (8,)
            rows = quantization_rows(self.source, self.y, bits, lambda: self._require_lease(request))
            atomic_json(self.session_dir / "light-validation-quantization.json", dict(rows=rows))
            self._validation_quantized = True
            send_json(conn, {"ok": True, "rows": rows})
            return True
        if command=='GM063_QUALIFY' and os.environ.get('GM063_LAUNCH_CONFIG'):
            from grademirror.adaptive_execution.calibration_qualification import respond
            return respond(self,conn,request)
        if command.startswith('HYBRID_'):
            if getattr(self,'hybrid',None) is None:raise ValueError('HYBRID_BACKEND_REQUIRED')
            return self.hybrid.handle(conn,request)
        if getattr(self,'hybrid',None) is not None and command=='PUT_Y':
            raise ValueError('HYBRID_SHARED_ADOPTION_REQUIRED')
        if command == "PING":
            send_json(conn, {"ok": True, "state": self.lease["state"] if self.lease else "NOT_ALLOCATED",
                             "session_id": self.spec["session_id"]})
        elif command == "FETCH_SOURCE":
            send_json(conn, {"ok": True, "identity": self.source_identity,
                             "info": _serializable_info(self.source_info)})
            send_array(conn, self.source)
        elif command == "FETCH_REFERENCE":
            ref = self._reference()
            identity = self._identity(ref, self.spec["reference_sha256"],
                                      self.spec["reference_bytes"], ref.dtype.itemsize * 8)
            send_json(conn, {"ok": True, "identity": identity,
                             "info": _serializable_info(self.reference_info)})
            send_array(conn, ref)
            from grademirror.light.runtime import operation
            if operation() is not None:
                self.reference = None
                self.reference_info = None
        elif command == "PUT_Y":
            if self.spec.get("profile_id") is not None and any(request.get(k) != self.spec[k]
                    for k in ("profile_id", "profile_contract_sha256")):
                raise ValueError("RESULT_PROFILE_BINDING_INVALID")
            if self.y is not None:
                raise ValueError("RESULT_ALREADY_RESIDENT")
            digest = request.get("recipe_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or request.get("source_pixel_sha256") != self.source_pixel_sha256:
                raise ValueError("RESULT_RECIPE_IDENTITY_INVALID")
            self._consumer_boundary("handoff_receive", point="host-before-put-y")
            value, mapping = self._receive_y(conn, request)
            stripe = None
            try:
                low = high = 0
                for row in range(0, len(value), 64):
                    stripe = value[row:row+64]
                    if not self.np.isfinite(stripe).all():
                        raise ValueError("NONFINITE_RESULT")
                    low += int(self.np.count_nonzero(stripe < 0))
                    high += int(self.np.count_nonzero(stripe > 1))
                value_sha = _sha_array(value)
                if mapping is not None and value_sha != request["shared_y"]["sha256"]:
                    raise ValueError("SHARED_Y_HASH_MISMATCH")
                fit_count = int(request.get("fit_count", 0))
                render_count = int(request.get("render_count", 0))
                if fit_count not in (0, 1) or render_count != 1:
                    raise ValueError("COMPUTE_COUNT_INVALID")
                lease = {"handle_id": str(uuid.uuid4()), "generation": 1,
                         "owner_instance_id": self.spec["session_id"],
                         "project_revision": self.spec["project_revision"],
                         "recipe_sha256": digest, "state": "RESIDENT",
                         "expires_at": time.time() + 300,
                         "bytes_reserved": int(self.source.nbytes + value.nbytes),
                         "capabilities": ["blend", "export"]}
                atomic_json(self.session_dir / "result-hashes.json", {
                    "float32_sha256": value_sha,
                    "source_pixel_sha256": self.source_pixel_sha256,
                    "recipe_sha256": digest,
                    "raw_min": float(value.min()), "raw_max": float(value.max()),
                    "clip_low_fraction": low / value.size,
                    "clip_high_fraction": high / value.size})
                if mapping is not None:
                    atomic_json(self.session_dir / "fast-y-handoff.json", {
                        "name": mapping.name, "bytes": value.nbytes, "sha256": value_sha,
                        "worker_unmapped_before_read": True, "host_full_copy": False})
                atomic_json(self.session_dir / "lease.json", lease)
                value.setflags(write=False)
                self.y, self._fast_y_map = value, mapping
                self.fit_count, self.render_count, self.lease = fit_count, render_count, lease
                mapping = None
                send_json(conn, {"ok": True, "lease": lease, "float32_sha256": value_sha})
            finally:
                if mapping is not None:
                    stripe = value = None
                    mapping.close()
        elif command == "BLEND":
            self._require_lease(request)
            q16, _ = self._blend(request.get("alpha"))
            send_json(conn, {"ok": True, "alpha": request["alpha"],
                             "q16_sha256": _sha_array(q16),
                             "fit_count": self.fit_count,
                             "render_count": self.render_count,
                             "blend_count": self.blend_count})
        elif command in {"PREVIEW_PIXEL", "PREVIEW_JPG"}:
            self._require_lease(request)
            from grademirror.gm004a_preview import get_preview
            binding = {key: request[key] for key in ("handle_id", "generation",
                "owner_instance_id", "project_revision", "recipe_sha256")}
            if getattr(self,'hybrid',None) is not None:
                from grademirror.hybrid_replay.contract import versions
                binding.update(versions(request));self.hybrid.output_admission()
            view = request.get("view", "result")
            from grademirror.light.runtime import operation
            light = operation() is not None
            from grademirror.light.eta import emit
            report=(lambda stage,done=None,total=None:emit(self.session_dir/"light-preview-progress.json",stage,done,total)) if light else None
            if report and view=="reference":
                report("preview_reference")
            if view != "reference" or self.reference is not None:
                self._consumer_boundary("roi", point="resident-preview")
            ref = self._reference() if view == "reference" else None
            preview_call=self.hybrid.preview if getattr(self,'hybrid',None) is not None else get_preview
            pixels, metadata = preview_call(self.source, self.y, ref,
                (self.reference_info if view == "reference" else self.source_info)["icc_bytes"] if light
                else self.source_info["icc_bytes"], binding, view=view,
                alpha=request.get("alpha", 1.), max_side=request.get("max_side", 1024),
                roi=request.get("roi"), request_id=request.get("request_id"),
                color_interpretation=(self.reference_info if view == "reference"
                                      else self.source_info)["color"]["interpretation"],
                deadline=time.monotonic() + (120 if getattr(self,'hybrid',None) is not None else 30),
                reference_roi=light,progress=report)
            if report:
                report("preview_transfer")
            if command == "PREVIEW_PIXEL":
                send_json(conn, {"ok": True, "preview": metadata,
                                 "fit_count": self.fit_count, "render_count": self.render_count})
                send_array(conn, pixels)
            else:
                if len(self.preview_saves) >= 4:
                    raise ValueError("PREVIEW_SAVE_LIMIT")
                if getattr(self,'hybrid',None) is not None:self.hybrid.io('preview-save',preview_JPG=1)
                if not isinstance(request.get("destination"), str) or not request["destination"]:
                    raise ValueError("PREVIEW_PATH_INVALID")
                root = Path(self.spec["output_root"])
                destination = writable_destination(Path(request["destination"]), root,
                    forbidden=tuple(self.spec[k] for k in ("source_path", "reference_path") if k in self.spec))
                if destination.suffix.lower() != ".jpg":
                    raise ValueError("PREVIEW_PATH_INVALID")
                destination.parent.mkdir(parents=True, exist_ok=True)
                receipt = codec.write_jpg(destination, pixels, self.source_info["icc_bytes"])
                self.preview_saves.append(receipt)
                if sum(item["bytes"] for item in self.preview_saves) > 16 * 1024**2:
                    raise ValueError("PREVIEW_SAVE_BUDGET")
                send_json(conn, {"ok": True, "preview": metadata,
                                 "file": receipt, "fit_count": self.fit_count,
                                 "render_count": self.render_count})
        elif command == "EXPORT":
            self._require_lease(request)
            name = request.get("name")
            if name not in {"alpha1.tif", "alpha0.5.tif", "source.jpg", "reference.jpg",
                            "alpha1.jpg", "alpha0.5.jpg"}:
                raise ValueError("EXPORT_NAME_INVALID")
            out = writable_path(Path(self.spec["output_root"]) / name)
            if out.exists():
                raise ValueError("EXPORT_EXISTS")
            out.parent.mkdir(parents=True, exist_ok=True)
            final_out=out
            from grademirror.export_metadata import policy_request,apply as apply_metadata
            metadata_policy=policy_request(self.spec['source_path'],self.spec['source_sha256'])
            out=final_out.parent/('.gm060-legacy-'+uuid.uuid4().hex+'.partial')
            alpha = _export_alpha(name)
            try:
                if name == "reference.jpg":
                    ref = self._reference()
                    receipt = codec.write_jpg(out, ref, self.source_info["icc_bytes"])
                    q16_sha = None
                elif name == "source.jpg":
                    source_float = self.source.astype(self.np.float32) / self.np.float32(65535)
                    receipt = codec.write_jpg(out, source_float, self.source_info["icc_bytes"])
                    q16_sha = None
                else:
                    if name.endswith(".tif"):
                        q16,_ = self._blend(alpha)
                        q16_sha = _sha_array(q16)
                        with open(self.spec['source_path'],'rb') as source_header:source_endian=source_header.read(2)
                        receipt = codec.write_tiff(out, q16, self.source_info["icc_bytes"],byteorder='>' if source_endian==b'MM' else '<')
                        check = codec.readback_tiff(out, self.source_info["icc_bytes"],
                                                    expected_pixel_sha256=q16_sha)
                        receipt["readback"] = check
                    else:
                        q8,stats = self._blend(alpha,bits=8,auxiliary_bits=16 if self.source.dtype==self.np.uint16 else None)
                        q16_sha = stats['auxiliary_sha256']if self.source.dtype==self.np.uint16 else _sha_array(q8)
                        receipt = codec.write_jpg(out, q8, self.source_info["icc_bytes"])
                height,width=receipt['shape'][:2]
                metadata_receipt=apply_metadata(self.spec['source_path'],out,metadata_policy,width,height,check=lambda:self._require_lease(request))
                self._require_lease(request)
                with open(out,'r+b') as staged:staged.flush();os.fsync(staged.fileno())
                os.link(out,final_out)
            finally:
                if out.is_file() and not out.is_symlink():out.unlink()
            receipt={'encoding_before_metadata':receipt,'source_exif':metadata_receipt,**codec._identity(final_out)}
            out=final_out
            record = {"name": name, "path": str(out), "alpha": alpha,
                      "q16_sha256": q16_sha, "receipt": receipt,
                      "fit_count": self.fit_count, "render_count": self.render_count}
            self.exports.append(record)
            atomic_json(self.session_dir / "exports.json", self.exports)
            send_json(conn, {"ok": True, "export": record})
        elif command == "EXPORT_V2":
            self._require_lease(request)
            from grademirror.gm004a_export import publish_export
            from grademirror.store import locked
            export = request.get("export")
            if not isinstance(export, dict):
                raise ValueError("EXPORT_REQUEST_INVALID")
            if getattr(self,'hybrid',None) is not None:
                from grademirror.hybrid_replay.contract import versions
                if versions(export)!=versions(self.lease):raise ValueError('HYBRID_EXPORT_BINDING')
                self.hybrid.io('export-request',export_requests=1)
                self.hybrid.output_admission()
            if not isinstance(request.get("destination"), str) or not request["destination"]:
                raise ValueError("EXPORT_PATH_INVALID")
            job_id = str(uuid.UUID(export["operation_job_id"]))
            attempt_id = str(uuid.UUID(export["operation_attempt_id"]))
            operation = self.session_dir.parents[2] / job_id / attempt_id
            operation_spec = strict_load(operation / "spec.json")
            publication_root = writable_output_root(Path(request.get("publication_root", self.spec["output_root"])))
            semantics = operation_spec.get("semantics", {})
            if (operation_spec.get("kind") != "export"
                    or operation_spec.get('job_id')!=job_id or operation_spec.get('attempt_id')!=attempt_id
                    or operation_spec.get("request_key") != export.get("request_key")
                    or str(publication_root) != semantics.get("publication_root", self.spec["output_root"])
                    or request["destination"] != semantics.get("destination")):
                raise ValueError("EXPORT_OPERATION_BINDING_INVALID")
            destination = writable_destination(Path(request["destination"]),
                publication_root,
                forbidden=tuple(self.spec[k] for k in ("source_path", "reference_path") if k in self.spec),
                allow_existing=True)
            expected_icc_policy = ("preserve_source" if self.source_info["icc_bytes"] is not None else "none")
            if export.get("format") not in {"tiff", "jpeg", "png", "webp", "bmp"} or export.get("icc_policy") != expected_icc_policy:
                raise ValueError("EXPORT_FORMAT_OR_ICC_UNSUPPORTED")
            source_format = {"TIFF": "tiff", "JPEG": "jpeg", "PNG": "png",
                             "WEBP": "webp", "BMP": "bmp"}.get(self.source_info.get("format"))
            if self.spec["mode"] in {"development_pair", "group15"} and source_format is not None and (export.get("format") != source_format or
                    export.get("bit_depth") != self.source_info["bits"]):
                raise ValueError("EXPORT_SOURCE_FORMAT_MISMATCH")
            if self.spec["mode"] == "synthetic" and (
                    export.get("format"), export.get("bit_depth")) not in {("tiff", 16), ("jpeg", 8)}:
                raise ValueError("EXPORT_BIT_DEPTH_UNSUPPORTED")
            if (export.get("alpha") is None or export.get("source_sha256") != self.spec["source_sha256"]
                    or export.get("recipe_sha256") != self.lease["recipe_sha256"]
                    or export.get("lease_id") != self.lease["handle_id"]
                    or export.get("lease_generation") != self.lease["generation"]
                    or export.get("width") != self.source.shape[1]
                    or export.get("height") != self.source.shape[0]
                    or export.get("icc_sha256") != (hashlib.sha256(self.source_info["icc_bytes"]).hexdigest()
                        if self.source_info["icc_bytes"] is not None else None)):
                raise ValueError("EXPORT_BINDING_INVALID")
            # A repeated key uses the saved intent's already validated pixel
            # hash. It reaches publish_export's semantic/file-identity check
            # without another full-image blend, encode, or TIFF readback.
            key_hash = hashlib.sha256(export["request_key"].encode("utf-8")).hexdigest()
            intent_path = self.session_dir / ".gm004a-exports" / key_hash / "intent.json"
            existing = strict_load(intent_path) if intent_path.is_file() else None
            quantized = None
            active_path=self.session_dir/'active-operation.json'
            active=strict_load(active_path)if active_path.is_file()else{}
            deadline=min(operation_spec.get('operation_deadline_at',float('inf')),active.get('deadline_wall',time.time()+self.spec.get('operation_seconds',120)))
            if getattr(self,'hybrid',None) is not None:deadline=min(deadline,time.time()+300)
            def check_export():
                if (operation/'cancel.signal').exists():raise InterruptedError('CANCELLED')
                if time.time()>=deadline:raise TimeoutError('EXPORT_DEADLINE')
            from grademirror.light.runtime import operation as light_plan
            from grademirror.light.eta import emit,PhaseDurations
            phases=PhaseDurations() if light_plan() is not None else None
            def export_phase(stage):
                if phases:
                    phases.mark(stage)
                    emit(operation/"light-export-progress.json",stage)
            if existing is not None:
                export["expected_pixels_sha256"] = existing["request"]["expected_pixels_sha256"]
            else:
                export_phase("mixing")
                quantized,_ = self._blend(export["alpha"],bits=export['bit_depth'],check=check_export,export=True)
                export["expected_pixels_sha256"] = _sha_array(quantized)
            export["source_path"] = self.spec["source_path"]
            from grademirror.export_metadata import policy_for_export_key
            prior_request = existing["request"] if existing is not None else None
            export.update(policy_for_export_key(export["source_path"], export["source_sha256"],
                                                prior=prior_request, requested=export.get("metadata_policy_id")))
            def writer(temp, req):
                nonlocal quantized
                self._consumer_boundary("export", point="resident-export-encode", quantized=quantized)
                if getattr(self,'hybrid',None) is not None:self.hybrid.io('native-encode',native_JPG_encodes=1)
                export_phase("encoding")
                self._export_event('encode_before')
                if req["format"] == "tiff":
                    from grademirror.storage_guard import reserve,tiff_writer_upper
                    source_size=Path(self.spec['source_path']).stat().st_size
                    encoded,metadata=tiff_writer_upper(req["width"],req["height"],req["bit_depth"],source_size)
                    # Both the encoded TIFF and metadata replacement can coexist.
                    reserve(temp,encoded+metadata)
                    with open(self.spec['source_path'],'rb') as source_header:source_endian=source_header.read(2)
                    codec.write_tiff(temp, quantized, self.source_info["icc_bytes"], byteorder='>' if source_endian==b'MM' else '<')
                elif req["format"] == "jpeg":
                    codec.write_jpg(temp, quantized, self.source_info["icc_bytes"])
                else:
                    codec.write_common(temp, quantized, req["format"], self.source_info["icc_bytes"])
                self._export_event('encode_after')
                if req["format"] != "jpeg" or self.hybrid is None:
                    quantized = None
            def validator(temp, req):
                self._consumer_boundary("readback", point="resident-export-readback", quantized=quantized)
                export_phase("metadata_verify")
                self._export_event('readback_before')
                if req["format"] == "tiff":
                    receipt = codec.readback_tiff(temp, self.source_info["icc_bytes"],
                        expected_pixel_sha256=req["expected_pixels_sha256"])
                    atomic_json(self.session_dir / ".gm004a-exports" / key_hash / "codec-readback.json",
                        {"request_key": req["request_key"], "readback_count": 1, "receipt": receipt})
                    pixel_hash = receipt["pixel_sha256"]
                elif req["format"] == "jpeg":
                    if getattr(self,'hybrid',None) is not None:
                        self.hybrid.io('native-readback',native_export_full_readbacks=1)
                        before=codec._identity(temp)
                        decoded=codec._read_development(temp,dict(format='JPEG',bits=8,height=self.source.shape[0],width=self.source.shape[1]))
                        maximum=0
                        for row in range(0,len(decoded),64):
                            maximum=max(maximum,int(self.np.max(self.np.abs(decoded[row:row+64].astype(self.np.int16)-quantized[row:row+64].astype(self.np.int16)))))
                        atomic_json(self.session_dir/'.gm004a-exports'/key_hash/'codec-readback.json',
                            dict(readback_count=1,decoded_pixel_sha256=_sha_array(decoded),preencode_Q8_sha256=req['expected_pixels_sha256'],
                                 lossy=True,pixel_exact_required=False,max_difference=maximum))
                        del decoded;codec._check_unchanged(temp,before)
                    with self.np.errstate(all="raise"):
                        # write_jpg already verifies header, ICC, quality and
                        # 4:4:4. The saved Q8 digest binds the encoded input.
                        pixel_hash = req["expected_pixels_sha256"]
                else:
                    receipt = codec.readback_common(temp, req["format"],
                        self.source_info["icc_bytes"], req["expected_pixels_sha256"])
                    pixel_hash = receipt["pixel_sha256"]
                self._export_event('readback_after')
                return {"alpha": req["alpha"], "format": req["format"],
                    "bit_depth": req["bit_depth"], "icc_sha256": req["icc_sha256"],
                    "source_sha256": req["source_sha256"],
                    "recipe_sha256": req["recipe_sha256"],
                    "lease_id": req["lease_id"], "lease_generation": req["lease_generation"],
                    "width": req["width"], "height": req["height"],
                    "pixels_sha256": pixel_hash}
            @contextmanager
            def publication_guard():
                # The operation journal and this host share one lock only for
                # the final no-overwrite link and durable host commit.
                job_id = str(uuid.UUID(export["operation_job_id"]))
                attempt_id = str(uuid.UUID(export["operation_attempt_id"]))
                operation = self.session_dir.parents[2] / job_id / attempt_id
                with locked(operation / "record.lock", timeout=5):
                    operation_spec = strict_load(operation / "spec.json")
                    if (operation_spec.get("kind") != "export"
                            or operation_spec.get("job_id") != job_id
                            or operation_spec.get("attempt_id") != attempt_id
                            or operation_spec.get("request_key") != export["request_key"]):
                        raise ValueError("EXPORT_OPERATION_BINDING_INVALID")
                    if (operation / "cancel.signal").exists():
                        raise InterruptedError("CANCELLED")
                    check_export();self._require_lease(request);self._export_event('publication_before')
                    export_phase("export_publish")
                    yield
                    self._export_event('publication_after')
            def metadata_progress(stage):
                export_phase("source_exif" if stage=='保留原图 EXIF' else "metadata_verify")
                from grademirror.gm004a_operations import OperationStore
                code='EXPORT_SOURCE_EXIF' if stage=='保留原图 EXIF' else 'EXPORT_METADATA_VERIFY'
                OperationStore(self.session_dir.parents[3]).transition(export['operation_job_id'],export['operation_attempt_id'],'RUNNING','source_exif' if stage=='保留原图 EXIF' else 'metadata_verify',code)
            result = publish_export(self.session_dir, destination, export, writer,
                                    validator, publication_guard=publication_guard, metadata_check=check_export, metadata_progress=metadata_progress)
            if getattr(self,'hybrid',None) is not None:
                from grademirror.hybrid_replay.contract import versions
                result.update(versions(self.lease))
            if result["status"] not in {"SUCCEEDED", "SUCCEEDED_RECOVERED"}:
                raise ValueError("EXPORT_INTERRUPTED")
            if phases and result["status"]=="SUCCEEDED" and not result.get("idempotent_replay"):
                result=dict(result,light_phase_seconds=phases.completed())
            self.exports_v2.append(result)
            atomic_json(self.session_dir / "exports-v2.json", self.exports_v2)
            send_json(conn, {"ok": True, "export": result,
                             "fit_count": self.fit_count, "render_count": self.render_count})
        elif command == "STATUS":
            if self.lease and self.lease["state"] == "RESIDENT":
                self._require_lease(request)
            send_json(conn, {"ok": True, "lease": self.lease,
                             "fit_count": self.fit_count, "render_count": self.render_count,
                             "blend_count": self.blend_count,
                             "exports": len(self.exports)})
        elif command == "RELEASE":
            self._require_lease(request)
            self.lease["state"] = "RELEASED"
            self.lease["expires_at"] = time.time()
            atomic_json(self.session_dir / "lease.json", self.lease)
            self._close_fast_y()
            self.y = None
            if getattr(self,'hybrid',None) is not None:self.hybrid.close()
            self.exit_reason = "released"
            send_json(conn, {"ok": True, "released": True})
            return False
        elif command == "ABORT":
            self._close_fast_y()
            if getattr(self,'hybrid',None) is not None:self.hybrid.close()
            self.exit_reason = "aborted"
            send_json(conn, {"ok": True, "aborted": True})
            return False
        else:
            raise ValueError("IPC_COMMAND_UNKNOWN")
        return True


def main(session_dir: Path):
    from grademirror.winproc import current_identity
    who=current_identity()
    atomic_json(session_dir/'host-entry.json',who)
    from grademirror.light.runtime import operation
    if operation() is not None:
        from grademirror.light.resources import host_identity_ack, stop_before_decode
        host_identity_ack(session_dir,"host-actual.json",who)
        stop_before_decode(session_dir,"host")
    host = Host(session_dir)
    spec = host.spec
    try:
        with Listener(("127.0.0.1", 0), family="AF_INET",
                      authkey=bytes.fromhex(spec["auth_hex"])) as listener:
            atomic_json(session_dir / "host-ready.json", {
                "session_id": spec["session_id"], "pid": os.getpid(),
                "identity": current_identity(),
                "ipc_address": list(listener.address),
                "source_pixel_sha256": host.source_pixel_sha256})
            keep = True
            while keep:
                with listener.accept() as conn:
                    active_path = session_dir / "active-operation.json"
                    try:
                        atomic_json(active_path, {"accepted_at": time.time(),
                                                  "deadline_wall": time.time() + spec.get("operation_seconds", 120)})
                        keep = host.handle(conn)
                    except Exception as exc:
                        failure = {"ok": False, "error_code": str(getattr(exc, "code", str(exc)))[:120]}
                        resource = getattr(exc, "resource_boundary", None)
                        if resource is not None:
                            failure["resource_boundary"] = resource
                        try:
                            send_json(conn, failure)
                        except (EOFError, OSError) as error:
                            exc.add_note(str(error))
                        try:
                            with (session_dir / "host-errors.jsonl").open("a", encoding="utf-8") as stream:
                                stream.write(json.dumps({"error": type(exc).__name__, "message": str(exc)},
                                                        ensure_ascii=False) + "\n")
                        except OSError as error:
                            exc.add_note(str(error))
                    finally:
                        try:
                            active_path.unlink(missing_ok=True)
                        except OSError:
                            pass
    finally:
        host._close_fast_y()
    atomic_json(session_dir / "host-exit.json", {"reason": host.exit_reason,
                                                 "fit_count": host.fit_count,
                                                 "render_count": host.render_count,
                                                 "blend_count": host.blend_count})


if __name__ == "__main__":
    try:
        if sys.argv[1:]==['--gm021-entry-only']:
            print(json.dumps(dict(entry=__file__,science=0,executable=sys.executable)));raise SystemExit(0)
        if len(sys.argv) != 2:
            raise ValueError("HOST_ARG_INVALID")
        main(Path(sys.argv[1]).resolve())
    except Exception:
        if len(sys.argv) == 2:
            (Path(sys.argv[1]) / "host-failure.log").write_text(traceback.format_exc()[:16384], encoding="utf-8")
        raise
