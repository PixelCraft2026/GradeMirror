"""Photo and worker entry. Explicit request claims precede all science."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "_grademirror_portable", None):
    sys.path.insert(0, str(ROOT / "src"))

from grademirror.common import atomic_json, strict_load
from grademirror.preview import contract as k


def probe(payload):
    from grademirror.engine import group15_codec as codec
    work = payload.parent / "probe"
    try:
        k.runtime_check("io")
        paths = strict_load(payload)["paths"]
        photos = []
        for path in paths:
            info = codec.inspect_source(path, strict_group15=False, development_pair=True)
            photos.append({n: v for n, v in info.items() if n != "icc_bytes"})
        atomic_json(work / "photos.json", dict(photos=photos))
        return 0
    except Exception as error:
        atomic_json(work / "photos.json", dict(photos=[], error=str(error)))
        return 1


def main():
    role = sys.argv[1]
    if role == "desktop":
        from grademirror.preview.desktop import main as desktop
        return desktop()[0]
    if role == "diagnose":
        from grademirror.preview.diagnostic import main as diagnose
        return diagnose()
    if role == "cli":
        from grademirror.preview.cli import main as cli
        return cli(sys.argv[2:])
    payload = Path(sys.argv[2])
    if role == "probe":
        return probe(payload)
    session = k.activate_worker(role, payload)
    from grademirror.cpu_replay import runtime
    try:
        if role == "owner":
            from grademirror.cpu_replay.owner import main as run
        elif role == "host":
            from grademirror.cpu_replay.host import main as run
        else:
            if role=="solver":
                from grademirror.cpu_refine.worker import solver
                solver(payload.parent)
            elif role=="prepare":
                from grademirror.cpu_refine.worker import prepare
                runtime.require_runtime(payload.parent)
                prepare(strict_load(payload))
            elif role=="cpu":
                from grademirror.engine.cpu_postprocess.service import worker
                runtime.require_runtime(payload.parent)
                worker(payload)
            else:
                from grademirror.cpu_first_fit.worker import fit, render
                (fit if role == "fit" else render)(strict_load(payload))
            runtime.observation(payload.parent, role, "EXITED")
            return 0
        return run(session)
    except BaseException as error:
        runtime.error(payload.parent, error)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

