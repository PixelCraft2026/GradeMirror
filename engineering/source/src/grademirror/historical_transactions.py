"""Standard-library validation for pre-retirement, already linearized commits.

This module exposes schema data to the disk transaction verifier only. It cannot
load a model, decode a photo, dispatch a worker, or authorize a new old-algorithm fit.
"""
from .engine import profiles
from .retired_algorithms import B0,C5,historical_profile,historical_settings
from .common import GMError

def transaction_profile(semantic):
    identity=semantic.get("profile_id")
    p=historical_profile(identity) if identity in (B0,C5) else profiles.get_profile(identity,semantic.get('profile_contract_sha256'))
    if semantic.get("fit_contract")!=p["fit_contract"]:
        raise GMError("FIT_RECOVERY_CONFLICT","Historical transaction contract differs",7)
    return p

def parent_profile(recipe):
    required=recipe.get("required_runtime")
    if required is None:return historical_profile(B0)
    p=profiles.metadata_profile(required.get("id"))
    if required!={"id":p["id"],"version":p["version"]}:
        raise GMError("FIT_RECOVERY_CONFLICT","Historical parent runtime differs",7)
    return p

def transaction_settings(profile_id,training_hw):
    return historical_settings(profile_id,training_hw) if profile_id in (B0,C5) else profiles.settings_for(profile_id,training_hw)
