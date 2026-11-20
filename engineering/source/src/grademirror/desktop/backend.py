"""Desktop-facing adapter. This process never decodes pixels or imports the neural runtime."""

from __future__ import annotations

from .i18n import tr, render, source_text

from pathlib import Path
import threading

from grademirror import gm004a_api
from grademirror.image_limits import MAX_PIXELS, MIB, check_geometry, require_memory
from grademirror.common import sha_file
from grademirror.common import GMError, strict_load
from grademirror.project import (ProjectIdentityMismatch, create_project,
                                 show_project)
from grademirror.portable_project import open_portable_project
from grademirror.gm004a_operations import OperationStore


class EngineBackend:
    def adaptive_plan(self,request,snapshot=None,*,qualification=None,workspace=None):
        from ..adaptive_execution.planner import plan
        return plan(request,snapshot,qualification=qualification,workspace=workspace)

    def adaptive_submit(self,plan,request_key,workspace,*,experimental=False,qualification=None):
        from ..adaptive_execution.service import submit
        return submit(plan,request_key,workspace,experimental=experimental,qualification=qualification)

    def adaptive_call(self,workspace,action,job_id,attempt_id,**arguments):
        from ..adaptive_execution.service import invoke
        return invoke(workspace,action,job_id,attempt_id,**arguments)

    def adaptive_open_recipe(self,path):
        from ..adaptive_execution.service import open_recipe
        return open_recipe(path)

    def adaptive_relink_recipe(self,path,source):
        from ..adaptive_execution.service import relink_recipe
        return relink_recipe(path,source)

    SIZED_PREVIEWS = True
    _IMPORT_LOCK = threading.Lock()
    IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    def plan_resources(self, request, snapshot=None):
        from grademirror.resource_planning import plan_resources
        return plan_resources(request, snapshot)

    @staticmethod
    def _client_identity():
        from grademirror.winproc import current_identity
        identity = current_identity()
        if not identity or identity.get("unknown"):
            raise GMError("PROCESS_IDENTITY_UNKNOWN", "Desktop client identity unavailable", 9)
        return identity

    @staticmethod
    def _image_reader(path: Path, label: str):
        from PySide6.QtCore import QFile, QIODevice
        from PySide6.QtGui import QImageReader

        suffix = path.suffix.lower()
        if suffix not in EngineBackend.IMAGE_SUFFIXES:
            raise ValueError(tr('{0}支持 RGB JPEG、PNG、TIFF、WebP、BMP', label))
        from ..common import path_identity
        path = Path(path_identity(str(path), must_exist=True))
        if not path.is_file():
            raise ValueError(tr('无法读取{0}图片文件头：{1}', label, path))
        device = QFile(str(path))
        if not device.open(QIODevice.OpenModeFlag.ReadOnly):
            raise ValueError(tr('无法读取{0}图片文件头：{1}', label, path))
        # Dispatch only explicit supported decoders on the same open device.
        # Qt's generic content detection can otherwise choose an SVG plugin.
        header = bytes(device.peek(16))
        formats = {
            ".jpg": (b"jpeg", (b"\xff\xd8\xff",)),
            ".jpeg": (b"jpeg", (b"\xff\xd8\xff",)),
            ".png": (b"png", (b"\x89PNG\r\n\x1a\n",)),
            ".tiff": (b"tiff", (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+")),
            ".tif": (b"tiff", (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+")),
            ".webp": (b"webp", (b"RIFF",)),
            ".bmp": (b"bmp", (b"BM",)),
        }
        selected, magic = formats[suffix]
        if not header.startswith(magic) or (selected == b"webp" and header[8:12] != b"WEBP"):
            device.close()
            raise ValueError(tr('无法读取{0}图片文件头：{1}', label, path))
        reader = QImageReader(device, selected)
        reader.setAutoDetectImageFormat(False)
        reader.setAutoTransform(True)
        return reader, device

    @staticmethod
    def _check_image(path: Path, label: str):
        from PySide6.QtGui import QImageIOHandler
        reader, device = EngineBackend._image_reader(path, label)
        if not reader.canRead() or not reader.size().isValid():
            raise ValueError(tr('无法读取{0}图片文件头：{1}', label, path))
        size = reader.size()
        width, height = size.width(), size.height()
        if reader.transformation() & QImageIOHandler.TransformationRotate90:
            width, height = height, width
        check_geometry(width, height)
        from ..preview.startup_fixture import enabled as startup_fixture_enabled, allowed as startup_fixture
        if max(width, height) < 512 and not (startup_fixture_enabled() and path.stat().st_size < 4096
            and startup_fixture(dict(width=width, height=height, bits=8, format=path.suffix[1:],
                                     sha256=__import__("hashlib").sha256(path.read_bytes()).hexdigest()))):
            raise ValueError(tr('{0}最长边至少需要 512 像素', label))
        return (max(1, round(height * 512 / max(width, height))),
                max(1, round(width * 512 / max(width, height))))

    def create_project(self, source: Path, reference: Path, destination: Path, *, mode=None):
        from grademirror.image_probe import inspect_pair
        if destination.suffix.lower() != ".grademirror":
            raise ValueError(tr("工程目录必须以 .grademirror 结尾"))
        inspect_pair(source, reference)
        create_project(source, reference, destination, mode=mode)
        return self.open_project(destination)

    def import_preview(self, path: Path, label: str, *, max_side=1024):
        # Two simultaneous native TIFF decodes must not both pass the same
        # available-memory snapshot before either allocates its RGBX64 buffer.
        with self._IMPORT_LOCK:
            return self._import_preview(path, label, max_side=max_side)

    def _import_preview(self, path: Path, label: str, *, max_side=1024):
        # Replay/result methods also serve headless callers with the fixed
        # stdlib control runtime. Qt is needed only for GUI image import.
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QColorSpace, QImage, QImageReader, QImageIOHandler
        from grademirror.preview_limits import MAX_FIT_SIDE
        if type(max_side) is not int or not 1 <= max_side <= MAX_FIT_SIDE:
            raise ValueError("Preview side exceeds display limit")
        from grademirror.image_probe import inspect_photos
        inspect_photos(path)
        self._check_image(path, label)
        reader, device = self._image_reader(path, label)
        native = reader.size()
        # TIFF/PNG plugins may allocate the native RGBX64 image before scaling.
        # Validate the header and available memory before raising Qt's 256 MiB guard.
        if native.width() * native.height() > 64_000_000:
            require_memory(native.width() * native.height() * 12 + 512 * MIB)
        QImageReader.setAllocationLimit((MAX_PIXELS * 8 + MIB - 1) // MIB + 32)
        native_width, native_height = native.width(), native.height()
        if reader.transformation() & QImageIOHandler.TransformationRotate90:
            native_width, native_height = native_height, native_width
        scale = min(1.0, max_side / max(native.width(), native.height()))
        reader.setScaledSize(QSize(max(1, round(native.width() * scale)),
                                   max(1, round(native.height() * scale))))
        image = reader.read()
        if image.isNull():
            raise ValueError(tr('无法读取{0}预览：{1}', label, reader.errorString()))
        if not image.colorSpace().isValid():
            from grademirror.light import portable
            from grademirror.light.native_policy import default_srgb_profile
            profile = default_srgb_profile(Path(__file__).resolve().parents[3] if not portable.enabled() else None)
            fallback = QColorSpace.fromIccProfile(profile)
            if not fallback.isValid() or bytes(fallback.iccProfile()) != profile:
                raise GMError("DEFAULT_SRGB_PROFILE_UNSUPPORTED", "Qt changed the fixed sRGB profile", 7)
            image.setColorSpace(fallback)
        return image.convertToFormat(QImage.Format_RGB888), (native_width, native_height)

    def open_project(self, path: Path):
        from grademirror.engine.profiles import runtimes as installed_runtimes
        runtimes = installed_runtimes()
        view = show_project(path, available_runtimes=runtimes)
        if not view["metadata_only"]:
            return open_portable_project(path, available_runtimes=runtimes)
        if view.get("algorithm_retired"):
            view["revision_id"]=view["current_revision"]
            return view
        from grademirror.engine.profiles import project_profile
        view["profile_id"] = project_profile(strict_load(path / "revisions/r000001/recipe.json"))["id"]
        source = Path(view["source"]["canonical_path"])
        if (not source.is_file() or source.stat().st_size != view["source"]["bytes"]
                or sha_file(source) != view["source"]["byte_sha256"]):
            raise ProjectIdentityMismatch("linked source bytes do not match the project")
        view["source"]["identity_verified"] = True
        view["revision_id"] = view["current_revision"]
        view["refinement_pending"] = bool(strict_load(path/"project.json")["extensions"].get("refinement_seed"))
        return view

    def create_refinement(self, project: Path, destination: Path):
        from grademirror.refinement import create_refinement
        create_refinement(project,destination)
        return self.open_project(destination)

    def replay(self, project: Path, store: Path, output: Path, key: str, *,
               execution_backend=None, allow_experimental=False, execution_manifest=None,
               source_path=None, reference_path=None, resource_request=None):
        if execution_backend is not None or execution_manifest is not None or allow_experimental or resource_request is not None:
            result = gm004a_api.replay(project,store,output,request_key=key,
                client_identity=self._client_identity(),execution_backend=execution_backend,
                allow_experimental=allow_experimental,execution_manifest=execution_manifest,
                source_path=source_path,reference_path=reference_path,resource_request=resource_request)
            return self._with_source_metadata(store,result)
        result = gm004a_api.replay(project, store, output, request_key=key,
                                  client_identity=self._client_identity())
        return self._with_source_metadata(store, result)

    @staticmethod
    def _with_source_metadata(store: Path, result: dict):
        if not result.get("binding"):
            return result
        try:
            operation, spec = OperationStore(store).path(result["job_id"], result["attempt_id"])
            decode = strict_load(operation / "session/source-decode.json")
            info = decode["info"]
            if not isinstance(info, dict) or not isinstance(decode["identity"], dict):
                raise GMError("SOURCE_METADATA_INVALID", "Invalid source metadata object")
            expected = spec["semantics"]["source_sha256"]
            if info.get("sha256") != expected or decode["identity"].get("file_sha256") != expected:
                raise GMError("SOURCE_METADATA_IDENTITY_MISMATCH", "Source metadata belongs to another input")
            return {**result, "source_image_metadata": info}
        except (OSError, ValueError, KeyError, TypeError, GMError) as exc:
            # A display annotation must not discard a healthy resident result.
            return {**result, "source_metadata_error": str(exc)}

    def fit_new_pair(self, project: Path, store: Path, output: Path, key: str, *, execution_backend=None, allow_experimental=False, resource_request=None):
        if resource_request is not None:
            result=gm004a_api.fit_new_pair(project,store,output,request_key=key,client_identity=self._client_identity(),
                execution_backend=execution_backend,allow_experimental=allow_experimental,resource_request=resource_request)
            return dict(result=self._with_source_metadata(store,result),input_project=str(project),resource_policy='guarded-v1')
        if execution_backend is not None or allow_experimental:
            result=gm004a_api.fit_new_pair(project,store,output,request_key=key,client_identity=self._client_identity(),
                execution_backend=execution_backend,allow_experimental=allow_experimental)
            return {"result":self._with_source_metadata(store,result),"execution_manifest":result['execution_manifest'],
                "input_project":str(project),"input_project_unchanged":True}
        result = gm004a_api.fit_new_pair(project, store, output, request_key=key,
                                        client_identity=self._client_identity())
        try:
            view = self.open_project(project)
        except Exception:
            if result.get("binding"):
                gm004a_api.release(store, result["job_id"], result["attempt_id"], result["binding"])
            raise
        return {"result": self._with_source_metadata(store, result), "project": view}

    def preview(self, store: Path, job: str, attempt: str, binding: dict,
                *, view: str, alpha: float, request_id: str, roi=None, max_side=1024):
        return gm004a_api.get_preview(store, job, attempt, binding, view=view,
            alpha=alpha, max_side=max_side, roi=roi, request_id=request_id)

    def preview_many(self, store, job, attempt, binding, requests, *, superseded=None):
        return gm004a_api.get_previews(store, job, attempt, binding, requests,
                                      superseded=superseded)

    def export(self, store: Path, job: str, attempt: str, binding: dict,
               destination: Path, alpha: float, key: str):
        operation, _ = OperationStore(store).path(job, attempt)
        source_info = strict_load(operation / "session" / "source-decode.json")["info"]
        fmt = {"TIFF": "tiff", "JPEG": "jpeg", "PNG": "png",
               "WEBP": "webp", "BMP": "bmp"}.get(source_info.get("format"), "tiff")
        return gm004a_api.export(store, job, attempt, binding, destination,
            alpha=alpha, format=fmt, bit_depth=source_info.get("bits", 16),
            icc_policy="preserve_source", request_key=key,
            export_root=destination.parent)

    def keep_alive(self, store: Path, job: str, attempt: str, binding: dict):
        return gm004a_api.keep_alive(store, job, attempt, binding)

    def release(self, store: Path, job: str, attempt: str, binding: dict):
        return gm004a_api.release(store, job, attempt, binding)

    def cancel_key(self, store: Path, key: str):
        return self.cancel_operation(store, key)

    def cancel_operation(self, store: Path, key: str, job=None, attempt=None):
        from grademirror.common import GMError
        ops = OperationStore(store)
        found = ops.lookup_request(key)
        if found is None:
            return {"cancel_disposition": "NOT_ACCEPTED_YET"}
        _, spec = found
        if (job is not None or attempt is not None) and (job, attempt) != (spec["job_id"], spec["attempt_id"]):
            raise GMError("ADDRESS_MISMATCH", "Cancel address differs from the active key", 2)
        return ops.cancel(spec["job_id"], spec["attempt_id"])

    def abort_operation(self, store: Path, key: str, job=None, attempt=None):
        return gm004a_api.abort_operation(store, key, job_id=job, attempt_id=attempt)

    def status_key(self, store: Path, key: str):
        import hashlib
        from grademirror.common import strict_load
        index = (store / "gm004a-operations" / "keys" /
            (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"))
        if not index.is_file():
            return None
        address = strict_load(index)
        return OperationStore(store).progress_snapshot(address["job_id"], address["attempt_id"])

    def status(self, store: Path, job: str, attempt: str):
        return gm004a_api.result_status(store, job, attempt)
