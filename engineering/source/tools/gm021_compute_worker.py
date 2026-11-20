"""Fixed hybrid worker entry; no test provider or callback in the payload."""
import argparse,json,sys
from pathlib import Path
if not getattr(sys, "_grademirror_portable", None):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('R0','CPU'));p.add_argument('payload',nargs='?');p.add_argument('--entry-only',action='store_true');args=p.parse_args()
    if args.entry_only:
        print(json.dumps(dict(entry=__file__,mode=args.mode,science=0,executable=sys.executable)))
        return 0
    if args.payload is None:p.error('payload required')
    from grademirror.common import strict_load
    from grademirror.resource_execution.binding import payload_check
    payload_check(strict_load(Path(args.payload)),args.mode)
    from grademirror.hybrid_replay.worker import main as run
    run(args.mode,Path(args.payload));return 0

if __name__=='__main__':raise SystemExit(main())
