"""Header qualification shared by desktop intake and the public new-pair API."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from .common import GMError
from .gm003_supervisor import IO_PY, ROOT


def inspect_photos(*paths: Path) -> list[dict]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        resolved=tuple(Path(p).resolve(strict=True) for p in paths)
        argv=[str(IO_PY), "-B", str(ROOT / "tools/gm_photo_probe.py"), *map(str,resolved)]
        from .light.runtime import headers_scope, worker, data_root
        capability=headers_scope()
        if capability is not None:
            argv,env=worker("headers",resolved,data_root(),value=capability)
        result = subprocess.run(argv, cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=40, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise GMError("IMAGE_INSPECTION_FAILED", str(exc), 3) from exc
    if result.returncode != 0 or payload.get("ok") is not True:
        raise GMError(payload.get("error", "IMAGE_INSPECTION_FAILED"),
                      payload.get("message", payload.get("error", "图片检查失败")), 3)
    photos = payload["photos"]
    for info in photos:
        w, h = info["width"], info["height"]
        from .preview.startup_fixture import allowed as startup_fixture
        if max(w, h) < 512 and not startup_fixture(info):
            raise GMError("IMAGE_TOO_SMALL", f"图片 {w} × {h}，最长边至少需要 512 像素", 3)
        info["training_grid"] = [max(1, round(h * 512 / max(w, h))),
                                 max(1, round(w * 512 / max(w, h)))]
    return photos


def inspect_pair(source: Path, reference: Path) -> tuple[dict, dict]:
    original, target = inspect_photos(source, reference)
    if original["training_grid"] != target["training_grid"]:
        raise GMError("PAIR_ASPECT_RATIO_MISMATCH",
            f"原图 {original['width']} × {original['height']}，参考图 "
            f"{target['width']} × {target['height']}。请使用同一张照片、相同构图比例的参考图。", 3)
    return original, target
