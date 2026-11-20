"""Fixed experimental first-fit stages; no configurable provider or dynamic target."""
import argparse,json,sys,os
from pathlib import Path
if not getattr(sys, "_grademirror_portable", None):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

def gm063_observation_work(mode,payload_path,payload):
    base=Path(payload_path).resolve().parent
    work=Path(payload['manifest']['output_root']).resolve() if mode=='CPU' else Path(payload['operation_path']).resolve()/mode
    if work!=base/mode:raise ValueError('GM063_OBSERVATION_PAYLOAD_PATH')
    return work/'gm063-observation'

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('R0','P3','CPU'));p.add_argument('payload',nargs='?');p.add_argument('--entry-only',action='store_true');args=p.parse_args()
    if args.entry_only:
        print(json.dumps(dict(entry=__file__,mode=args.mode,science=0,executable=sys.executable)));return 0
    if args.payload is None:p.error('payload required')
    if os.environ.get('GM063_LAUNCH_CONFIG'):
        from grademirror.adaptive_execution.calibration_launch import enter
        payload_path=Path(args.payload);p=json.loads(payload_path.read_text('utf-8'))
        enter('H_'+args.mode,gm063_observation_work(args.mode,payload_path,p))
    from grademirror.common import strict_load
    from grademirror.resource_execution.binding import payload_check
    payload_check(strict_load(Path(args.payload)),args.mode)
    import platform
    def native_ver(system='',release='',version='',supported_platforms=None):
        value=sys.getwindowsversion();return 'Microsoft','Windows','.'.join(map(str,value.platform_version or value[:3]))
    platform._syscmd_ver=native_ver
    if args.mode=='CPU':
        from grademirror.hybrid_replay.worker import main as run
    else:
        from grademirror.hybrid_fit.worker import main as run
    run(args.mode,Path(args.payload));return 0

if __name__=='__main__':raise SystemExit(main())
