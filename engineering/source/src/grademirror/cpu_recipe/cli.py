"""Recipe-only CLI adapter, returning exactly one stable JSON result."""
import sys
from . import pack_cpu_recipe,inspect_cpu_recipe,verify_cpu_recipe,relink_cpu_recipe_source,RecipeError
from ..common import GMError
def dispatch(args):
    try:
        if args.recipe_command=='pack':value=pack_cpu_recipe(args.cpu_fast,args.out,args.request_key,cpu_fine=args.cpu_fine)
        elif args.recipe_command=='inspect':value=inspect_cpu_recipe(args.path)
        elif args.recipe_command=='verify':value=verify_cpu_recipe(args.path)
        else:value=relink_cpu_recipe_source(args.path,args.source)
        return value,0
    except (GMError,OSError,ValueError)as e:
        code=e.code if isinstance(e,GMError)else'RECIPE_IO_ERROR';stage=getattr(e,'stage',args.recipe_command);exit_code=getattr(e,'exit_code',7)
        print(f'{code} [{stage}]: {e}',file=sys.stderr)
        return dict(error_code=code,stage=stage,message=str(e),exit_code=exit_code,resident=False,execution_qualified=False),exit_code
