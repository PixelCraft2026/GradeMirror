import hashlib, importlib, json, marshal, os, stat, sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
S = Path(__file__).resolve().parent / "source"

def canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")


def digest(obj):
    return hashlib.sha256(canon(obj)).hexdigest()


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def ident(path):
    return dict(bytes=path.stat().st_size, sha256=sha(path))


def write_json(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("xb") as f:
        f.write(canon(value) + b"\n")


def plain(path):
    path = Path(path)
    for folder in (*path.parents, path):
        if folder.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Reparse point: {folder}")
    return path


def capacity(planned):
    free = shutil.disk_usage(ROOT).free
    if free-planned < 3*1024**3:
        raise RuntimeError(f"PORTABLE_DISK_HEADROOM: {free=} {planned=}")
    return free


def code_name(logical):
    path = PurePosixPath(logical)
    if path.suffix != ".py":
        return None
    parts = path.with_suffix("").parts
    if parts[:2] == ("src", "grademirror"):
        parts = parts[1:]
    elif not (parts[0] == "tools" and len(parts) == 2 and path.name != "portable_boot.py"):
        return None
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def zip_member(z, name, data):
    entry = ZipInfo(name)
    entry.compress_type = ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    z.writestr(entry, data, compresslevel=6)


def code_archive(dest):
    inventory = {p.relative_to(S).as_posix(): ident(plain(p)) for p in sorted(S.rglob("*")) if p.is_file()}
    source_rows = [dict(path=rel, **row) for rel, row in sorted(inventory.items())]
    source_build = digest(source_rows)
    modules = {}
    import importlib.util
    target = dest/"runtime/grademirror-code.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "x") as z:
        for rel, record in sorted(inventory.items()):
            module = code_name(rel)
            if module is None:
                continue
            mod, package = module
            payload = (S/rel).read_bytes()
            pyc = importlib.util.MAGIC_NUMBER + b"\x01\x00\x00\x00" + importlib.util.source_hash(payload)
            pyc += marshal.dumps(compile(payload, rel, "exec", dont_inherit=True, optimize=0))
            member = rel + "c"
            row = dict(source=rel, source_sha256=record["sha256"], source_bytes=record["bytes"],
                       bytecode=member, bytecode_sha256=hashlib.sha256(pyc).hexdigest(), package=package)
            if rel == "src/grademirror/engine/cpu_postprocess/core.py":
                old = b"    if workspace_bound(s)['total_bytes']>512*1024**2:raise MemoryError('CPU_WORKSPACE_CAP')"
                new = (b"    from ...light.resources import workspace_limit\n"
                       b"    if workspace_bound(s)['total_bytes']>workspace_limit(*s.shapes[0]):raise MemoryError('CPU_WORKSPACE_CAP')")
                if payload.count(new) != 1:
                    raise ValueError("Unexpected CPU resource guard")
                row["restored_resource_guard_sha256"] = hashlib.sha256(payload.replace(new, old)).hexdigest()
            if mod in modules:
                raise ValueError("Duplicate product module")
            modules[mod] = row
            zip_member(z, member, pyc)
        index = dict(source_build=source_build, members=modules)
        zip_member(z, "identity.json", canon(index))
    return dict(sha256=source_build, files=source_rows), index, ident(target)


def bootstrap(staging, build, archive, stdlib):
    isolated = staging.parent / "roles/builder"
    if not isolated.is_dir():
        raise RuntimeError("GM-090 pinned builder wheels must be staged")
    sys.path.append(str(isolated))
    import PyInstaller.__main__

    launcher_icon = plain(S.parent / "runtime-data/assets/brand/app/grademirror-desktop-v3.ico")
    configuration = staging/"config"
    configuration.mkdir()
    source = configuration/"portable_config.py"
    source.write_text(
        f"SOURCE_BUILD = {build['sha256']!r}\nARCHIVE_SHA256 = {archive['sha256']!r}\nSTDLIB_SHA256 = {stdlib!r}\n",
        encoding="utf8",
    )
    path = staging/"portable.spec"
    path.write_text(
        f"""a = Analysis([{str(S/'tools/portable_boot.py')!r}],
    pathex={[str(configuration)]!r},
    binaries=[], datas=[], hiddenimports=['winreg', 'ctypes.wintypes', 'multiprocessing.connection',
        'multiprocessing.shared_memory', 'msvcrt', 'subprocess', 'xml.etree.ElementTree'],
    hookspath=[], runtime_hooks=[], excludes=['grademirror', 'PySide6', 'numpy', 'scipy', 'torch',
        'PIL', 'cv2', 'imagecodecs'], noarchive=False, optimize=0)
from pathlib import Path
import json
Path({str(staging/"pyinstaller-native-origins.json")!r}).write_text(
    json.dumps([dict(relative=name, original=origin, type=kind)
               for name,origin,kind in a.binaries], indent=2), encoding="utf8")
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GradeMirror',
    icon={str(launcher_icon)!r}, console=False, strip=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GradeMirror-CLI',
    icon={str(launcher_icon)!r}, console=True, strip=False, upx=False)
stock_crt = [row for row in a.binaries if row[0].casefold() == "vcruntime140.dll"]
assert len(stock_crt) == 1
remaining = [row for row in a.binaries if row[0].casefold() != "vcruntime140.dll"]
collection = COLLECT(gui, cli, remaining, a.datas, strip=False, upx=False, name='launchers')
""",
        encoding="utf8",
    )
    PyInstaller.__main__.run(
        [str(path), "--distpath", str(staging/"dist"), "--workpath", str(staging/"work"),
         "--noconfirm", "--log-level", "WARN"]
    )
    target=staging/"dist/launchers"
    crt=Path(sys.executable).parent/"VCRUNTIME140.dll"
    approved=dict(bytes=124544,sha256="d5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066")
    if ident(crt)!=approved:
        raise ValueError("Locked toolchain CRT does not match")
    target_file=target/"_internal/VCRUNTIME140.dll"
    # Match the staged writes for the other pinned CPython runtime DLLs.
    staged_file=target_file.with_suffix(".stage")
    try:
        with crt.open("rb") as source, staged_file.open("xb") as destination:
            while block:=source.read(1024**2):
                destination.write(block)
        if ident(staged_file)!=approved:
            raise ValueError("Locked toolchain CRT output differs")
        staged_file.rename(target_file)
        if ident(target_file)!=approved:
            raise ValueError("Locked toolchain CRT rename differs")
    finally:
        if staged_file.is_file():
            staged_file.unlink()
    return target


