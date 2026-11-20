"""The sole executable R0 profile. This router imports only standard-library data."""
from .independent_r0 import profile as r0
from .independent_r0_p3_lld10 import profile as fine
from ..common import GMError
from ..retired_algorithms import is_retired,historical_profile,historical_settings

R0=r0.PROFILE_ID
FINE=fine.PROFILE_ID
DEFAULT_PROFILE_ID=R0
PROFILES={R0:dict(id=R0,label="R0 · Lab + M05",version=r0.RECIPE_SCHEMA_VERSION,
    fit_contract=r0.FIT_CONTRACT,core_sha256=r0.CORE_SHA256,
    contract_sha256=r0.PROFILE_CONTRACT_SHA256,settings=r0.SETTINGS,
    shapes=r0.PARAMETER_SHAPES,module="independent_r0",artifact_kind="independent_r0_recipe")}

PROFILES[FINE]=dict(id=FINE,label="精细 · R0 + P3 + LLD10",version=fine.RECIPE_SCHEMA_VERSION,fit_contract=fine.FIT_CONTRACT,core_sha256=fine.CORE_SHA256,contract_sha256=fine.PROFILE_CONTRACT_SHA256,settings=fine.SETTINGS,shapes=fine.PARAMETER_SHAPES,module="independent_r0_p3_lld10",artifact_kind="independent_r0_p3_lld10_recipe")

def resolve_mode(mode=None,profile=None):
    modes={"fast":R0,"fine":FINE}
    if mode is not None and mode not in modes:raise GMError("MODE_INVALID","Unknown processing mode",2)
    chosen=get_profile(profile) if profile is not None else get_profile(modes.get(mode,R0))
    if mode is not None and chosen["id"]!=modes[mode]:raise GMError("MODE_PROFILE_MISMATCH","Mode and profile disagree",2)
    return chosen

def mode_for(profile_id):
    get_profile(profile_id)
    return "fine" if profile_id==FINE else "fast"

def retired_error():
    return GMError("ALGORITHM_RETIRED","旧算法已停用。请使用原图和参考图另建 R0 工程并重新仿色；原工程与已有导出保留。",4)

def get_profile(profile_id,contract_sha256=None):
    if is_retired(profile_id):raise retired_error()
    if not isinstance(profile_id,str) or profile_id not in PROFILES:
        raise GMError("ALGORITHM_VERSION_UNAVAILABLE","Unknown algorithm profile",4)
    p=PROFILES[profile_id]
    if contract_sha256 is None or contract_sha256==p['contract_sha256']:return p
    if profile_id==FINE and contract_sha256 in fine.COMPATIBLE_CONTRACTS:
        return {**p,'contract_sha256':contract_sha256,'core_sha256':fine.COMPATIBLE_CONTRACTS[contract_sha256]['core_sha256']}
    raise GMError('RECIPE_PROFILE_MISMATCH','Unknown algorithm contract',7)

def runtimes():return {k:v["version"] for k,v in PROFILES.items()}
def runtime(profile_id):
    p=get_profile(profile_id);return dict(id=p["id"],version=p["version"])

def project_profile(parent_recipe,contract_sha256=None):
    required=parent_recipe.get("required_runtime")
    if required is None:raise retired_error()
    p=get_profile(required.get("id"),contract_sha256)
    if required!=runtime(p["id"]):
        raise GMError("ALGORITHM_VERSION_UNAVAILABLE","Unsupported algorithm version",4)
    return p

def recipe_profile(recipe):
    if recipe.get("profile_id") is None and recipe.get("recipe_schema_version") in ("0.2.0","0.3.0"):
        raise retired_error()
    p=get_profile(recipe.get("profile_id"),recipe.get('profile_contract_sha256'))
    if (recipe.get("recipe_schema_version")!=p["version"] or
            recipe.get("core_sha256")!=p["core_sha256"] or
            recipe.get("profile_contract_sha256")!=p["contract_sha256"]):
        raise GMError("RECIPE_PROFILE_MISMATCH","Recipe algorithm binding differs",7)
    return p

def settings_for(profile_id,training_hw=None):
    p=get_profile(profile_id);settings=dict(p["settings"])
    if not isinstance(training_hw,list) or len(training_hw)!=2 or any(type(n)is not int or n<13 for n in training_hw):
        raise GMError("TRAINING_GEOMETRY_INVALID","R0 requires the actual training grid",7)
    settings["baseline_training_hw"]=list(training_hw)
    return settings

def normalization(source_identity,reference_identity):
    return {name:dict(dtype=i["dtype"],bit_depth=i["bit_depth"],
        denominator={"uint8":255,"uint16":65535}.get(i["dtype"]),
        dtype_after="float32",resize="INTER_AREA",quantization="none")
        for name,i in (("source",source_identity),("reference",reference_identity))}

def metadata_profile(profile_id):
    """Read-only schema data, never a dispatch target or available-runtime entry."""
    return historical_profile(profile_id) if is_retired(profile_id) else get_profile(profile_id)

def metadata_settings(profile_id,training_hw=None):
    return historical_settings(profile_id,training_hw) if is_retired(profile_id) else settings_for(profile_id,training_hw)

def metadata_recipe_profile(recipe):
    identity=recipe.get('profile_id')
    p=historical_profile(identity) if is_retired(identity) else get_profile(identity,recipe.get('profile_contract_sha256'))
    if (recipe.get("recipe_schema_version")!=p["version"] or recipe.get("core_sha256")!=p["core_sha256"] or
            recipe.get("profile_contract_sha256")!=p["contract_sha256"]):
        raise GMError("RECIPE_PROFILE_MISMATCH","Historical recipe binding differs",7)
    return p
