"""Preserve saved recipe and exact failed stage in v4 error envelopes."""
from pathlib import Path
from .first_contract import read
def metadata(c,payload):
 from ..gm004a_operations import OperationStore,_journal
 from .portable_contract import enabled
 readonly=enabled(c)
 args=payload.get('arguments',{})
 ops=OperationStore(Path(c['workspace'])/'store',read_only=readonly)
 if payload.get('action')=='submit' and args.get('request_key'):
  found=ops.lookup_request(args['request_key'],saved_only=readonly)
 elif args.get('job_id') and args.get('attempt_id'):
  found=ops.path(args['job_id'],args['attempt_id'])
 else:
  found=None
 if not found:
  next_step='尚无可核验的操作记录，请保留首错。' if readonly else '修正入口后显式重试；本次未开始计算。'
  return dict(stage='validate_request',preserved_recipes=[],next_step=next_step)
 path,spec=found
 value=_journal(path)[-1]['snapshot'] if readonly else ops.status(spec['job_id'],spec['attempt_id'])
 progress=path/'progress.json';stage=read(progress).get('stage') if progress.is_file() else value['stage'];saved=[]
 if not readonly:
  if value.get('recipe_available'):saved.append(dict(directory=value['recipe_directory'],bundle_id=value['recipe_bundle_id'],math_id=value['recipe_math_id'],state=value['recipe_state']))
  return dict(stage=stage,preserved_recipes=saved,recipe_available=bool(saved),next_step='已保存配方，需显式重放；不自动重新拟合或求解。' if saved else '本阶段未提交完整配方；保留失败，需按阶段额度显式恢复。')
 directory=Path(spec['semantics']['bundle_directory'])
 if (directory/'COMPLETE.json').is_file() and (directory/'manifest.json').is_file():
  recipe=read(directory/'manifest.json')
  saved.append(dict(directory=str(directory),bundle_id=recipe['bundle_id'],math_id=recipe['math_id'],state='SAVED_UNVERIFIED'))
 return dict(stage=stage,observation='SAVED_ONLY_CURRENT_RESIDENCY_UNKNOWN',preserved_recipes=saved,recipe_available=bool(saved),next_step='已保存配方，需显式重放；不自动重新拟合或求解。' if saved else '本阶段未提交完整配方；保留失败，需按阶段额度显式恢复。')
