"""Identity and worker dispatch for a frozen GradeMirror build."""
from contextlib import contextmanager
from functools import lru_cache
import importlib
import os
from pathlib import Path
import runpy
import sys

from ..common import reject_reparse, strict_load
from ..cpu_replay.contract import need


def state():
    return getattr(sys, "_grademirror_portable", None)


def enabled():
    return state() is not None


@lru_cache(maxsize=1)
def source_index():
    value = state()
    return {row["source"]: row for row in value.finder.rows.values()} if value else {}


def source_record(path):
    ctx = state()
    path = Path(path).absolute()
    if ctx is None or not path.is_relative_to(ctx.root):
        return None
    return source_index().get(path.relative_to(ctx.root).as_posix())


def source_files(directory, pattern):
    directory = Path(directory)
    if not enabled():
        return sorted(directory.glob(pattern))
    ctx = state()
    return sorted(ctx.root / name for name in source_index()
                  if (ctx.root / name).parent == directory and Path(name).match(pattern))


def source_sha(path):
    row = source_record(path)
    if row is not None:
        return row["source_sha256"]
    from ..common import sha_file
    return sha_file(Path(path))


def executable(role):
    from .runtime import ROOT
    if role == "desktop":
        return ROOT / "GradeMirror.exe"
    return ROOT / "workers" / role / f"GradeMirror-{role}.exe"


def application_image():
    """Validate the real GUI or CLI image against its distinct package row."""
    from .system import packaged
    from ..winproc import current_identity
    context = state()
    need(context is not None and context.role == "desktop", "PORTABLE_APP_ROLE")
    actual = Path(sys.executable).resolve()
    applications = (context.root / "GradeMirror.exe", context.root / "GradeMirror-CLI.exe")
    need(actual in applications, "PORTABLE_APP_IMAGE_PATH")
    identity = current_identity()
    need(not identity.get("unknown") and Path(identity["image_path"]).resolve() == actual,
         "PORTABLE_APP_PROCESS_IMAGE")
    return packaged(actual)


def argv(exe, entry, *args, external=False):
    if enabled():
        return [str(exe), *map(str, args)]
    flags = ["-I", "-S", "-B", "-X", "utf8"] if external else ["-I", "-B", "-X", "utf8"]
    return [str(exe), *flags, str(entry), *map(str, args)]


def entry_identity(path, identity):
    """Bind a logical compiled member to the separately verified process image."""
    from .system import packaged
    from ..common import sha_file
    context = state()
    row = source_record(path)
    need(context is not None and row is not None, "PORTABLE_ENTRY_NOT_ARCHIVED")
    packaged(path)
    archive = packaged(context.root / "runtime/grademirror-code.zip")
    process = packaged(Path(sys.executable))
    need(not identity.get("unknown") and Path(identity["image_path"]).resolve() == process
         and process == executable(context.role), "PORTABLE_ACTUAL_PROCESS_IMAGE")
    return dict(source=row["source"], source_sha256=row["source_sha256"],
                bytecode=row["bytecode"], bytecode_sha256=row["bytecode_sha256"],
                archive=archive.relative_to(context.root).as_posix(), archive_sha256=sha_file(archive),
                role_executable=process.relative_to(context.root).as_posix(),
                role_executable_sha256=sha_file(process))


def run_worker(path):
    if enabled():
        return runpy.run_module(f"tools.{path.stem}", run_name="__main__", alter_sys=True)
    return runpy.run_path(str(path), run_name="__main__")


def load_worker(path):
    if enabled():
        return vars(importlib.import_module(f"tools.{path.stem}"))
    return runpy.run_path(str(path), run_name=f"_{path.stem}_preload")


_dll_dirs = []


def bind_dependencies(role):
    ctx = state()
    from .system import manifest, packaged
    from ..cpu_replay.contract import digest

    package = manifest()
    archive = packaged(ctx.root / "runtime/grademirror-code.zip")
    need(package["build"]["sha256"] == ctx.finder.index["source_build"] and
         package["build"]["sha256"] == digest(package["build"]["files"]), "PORTABLE_BUILD_ID")
    expected_exe = executable(role)
    exe = Path(sys.executable).resolve()
    if role == "desktop":
        application_image()
    else:
        need(exe == expected_exe, "PORTABLE_ROLE_EXE")
        packaged(exe)
    site = ctx.root / "runtime" / role / "Lib/site-packages"
    if role != "control":
        reject_reparse(site)
        need(site.is_dir(), "PORTABLE_ROLE_SITE")
        sys.path.append(str(site))
        # Keep only the application role's import tree, with the runtime's
        # standard library extensions in a distinct location.
        for folder in (site,):
            _dll_dirs.append(os.add_dll_directory(str(folder)))
    stdlib = packaged(ctx.root / "runtime/cpython/python312.zip")
    modules = ctx.root / "runtime/cpython/DLLs"
    reject_reparse(modules)
    _dll_dirs.append(os.add_dll_directory(str(modules)))
    need(str(stdlib) in sys.path, "PORTABLE_STDLIB_NOT_BOUND")
    sys.path.append(str(modules))
    if role == "desktop":
        # The packaged desktop closure omits the optional Qt networking and QML components.
        from ..common import strict_load
        retired = strict_load(packaged(ctx.root / "licenses/qt-omitted-members.json"))
        for name in retired:
            need(not os.path.lexists(site / name), "PORTABLE_RETIRED_QT_MEMBER", name)
        # The Qt runtime is loaded by the desktop and CLI only.
        for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH"):
            os.environ.pop(name, None)
        from .system import check_loaded
        check_loaded()
