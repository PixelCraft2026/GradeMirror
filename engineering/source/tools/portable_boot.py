"""Load the onedir application from its pinned bytecode archive."""
import hashlib
import importlib.abc
import importlib.util
import io
import json
import marshal
import os
from pathlib import Path
import stat
import sys
import types
from zipfile import ZipFile

from portable_config import ARCHIVE_SHA256, SOURCE_BUILD, STDLIB_SHA256


PROFILES = {
    "GradeMirror.exe": "desktop",
    "GradeMirror-CLI.exe": "desktop",
    "GradeMirror-control.exe": "control",
    "GradeMirror-io.exe": "io",
    "GradeMirror-cpu.exe": "cpu",
    "GradeMirror-cu124.exe": "cu124",
    "GradeMirror-solver.exe": "solver",
}


def check_plain(path):
    for part in (*path.parents, path):
        if part.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(f"Reparse point in portable package: {part.name}")


class ProductArchive(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, root, archive):
        self.root = root
        self.archive = archive
        self.index = json.loads(archive.read("identity.json"))
        if self.index["source_build"] != SOURCE_BUILD:
            raise RuntimeError("PORTABLE_SOURCE_BUILD")
        self.rows = self.index["members"]

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tools":
            spec = importlib.util.spec_from_loader(fullname, None, is_package=True)
            spec.submodule_search_locations = []
            return spec
        if fullname not in self.rows:
            if fullname.startswith(("grademirror.", "tools.gm")):
                raise ImportError(f"Unknown product archive module: {fullname}")
            return None
        row = self.rows[fullname]
        return importlib.util.spec_from_file_location(
            fullname, self.root / row["source"], loader=self,
            submodule_search_locations=[] if row["package"] else None,
        )

    def get_code(self, fullname):
        row = self.rows[fullname]
        data = self.archive.read(row["bytecode"])
        if data[:4] != importlib.util.MAGIC_NUMBER or hashlib.sha256(data).hexdigest() != row["bytecode_sha256"]:
            raise ImportError(f"Invalid product archive member: {fullname}")
        code = marshal.loads(data[16:])
        if not isinstance(code, types.CodeType) or code.co_filename != row["source"]:
            raise ImportError(f"Invalid product code object: {fullname}")
        return code

    def exec_module(self, module):
        exec(self.get_code(module.__name__), module.__dict__)


def main():
    executable = Path(sys.executable).absolute()
    check_plain(executable)
    role = PROFILES[executable.name]
    in_workers = executable.parent.parent.name == "workers"
    root = executable.parents[2] if in_workers else executable.parent
    archive_file = root / "runtime/grademirror-code.zip"
    check_plain(archive_file)
    payload = archive_file.read_bytes()
    if len(payload) > 16 * 1024**2 or hashlib.sha256(payload).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("PORTABLE_CODE_ARCHIVE_CHANGED")
    finder = ProductArchive(root, ZipFile(io.BytesIO(payload)))
    sys.meta_path.insert(0, finder)
    sys._grademirror_portable = types.SimpleNamespace(root=root, role=role, finder=finder)
    sys.dont_write_bytecode = True
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("PYTHONHOME", None)
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8")

    standard = root / "runtime/cpython/python312.zip"
    check_plain(standard)
    with standard.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != STDLIB_SHA256:
            raise RuntimeError("PORTABLE_STDLIB_CHANGED")
    sys.path.append(str(standard))
    from grademirror.light.portable import bind_dependencies
    bind_dependencies(role)
    command = executable.name
    if command == "GradeMirror.exe" and sys.argv[1:2] != ["pick-file"]:
        sys.argv.insert(1, "desktop")
    elif command == "GradeMirror-CLI.exe":
        sys.argv.insert(1, "cli")
    elif in_workers and sys.argv[1:2] not in (["worker"], ["probe"]):
        raise SystemExit(2)

    from tools.gm066_entry import main as entry
    return entry()


if __name__ == "__main__":
    raise SystemExit(main())
