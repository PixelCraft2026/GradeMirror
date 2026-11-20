"""Read supported photo headers in the existing isolated I/O runtime."""
import json
import sys
from pathlib import Path

if not getattr(sys, "_grademirror_portable", None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from grademirror.engine import group15_codec as codec
from grademirror.common import GMError


def main():
    try:
        photos = []
        for path in sys.argv[1:]:
            info = codec.inspect_source(path, strict_group15=False, development_pair=True)
            photos.append({k: v for k, v in info.items() if k != "icc_bytes"})
        print(json.dumps({"ok": True, "photos": photos}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": exc.code if isinstance(exc, GMError) else str(exc), "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
