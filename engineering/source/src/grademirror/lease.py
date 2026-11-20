"""Result lease identity validation; acquiring pixels belongs to the session host."""

import math
import re
import time
from .common import GMError


LEASE_FIELDS = {"handle_id", "generation", "owner_instance_id", "project_revision",
                "recipe_sha256", "state", "expires_at", "bytes_reserved", "capabilities"}


BINDING_FIELDS = {"handle_id", "generation", "owner_instance_id", "project_revision", "recipe_sha256"}


def validate_lease_handle(handle, *, binding=None):
    if handle is None:
        raise GMError("RESULT_NOT_RESIDENT", "No result has been allocated", 4)
    from .hybrid_replay import VERSION_FIELDS
    hybrid = isinstance(handle, dict) and bool(VERSION_FIELDS & handle.keys())
    fields = LEASE_FIELDS | VERSION_FIELDS if hybrid else LEASE_FIELDS
    if not isinstance(handle, dict) or set(handle) != fields:
        raise GMError("RESULT_LEASE_INVALID", "Incomplete result lease")
    if hybrid:
        from .hybrid_replay.contract import versions
        versions(handle)
    if handle["state"] not in {"NOT_ALLOCATED", "RESIDENT", "RELEASE_PENDING", "RELEASED", "LOST"}:
        raise GMError("RESULT_LEASE_INVALID", "Unknown lease state")
    if handle["state"] != "RESIDENT":
        raise GMError("RESULT_NOT_RESIDENT", "Explicit replay required", 4)
    if (any(not isinstance(handle[key], str) or not handle[key] for key in
            ("handle_id", "owner_instance_id", "project_revision"))
            or type(handle["generation"]) is not int or handle["generation"] < 1
            or not re.fullmatch(r"[0-9a-f]{64}", str(handle["recipe_sha256"]))
            or type(handle["bytes_reserved"]) is not int or handle["bytes_reserved"] < 0
            or not isinstance(handle["capabilities"], list)
            or any(not isinstance(value, str) for value in handle["capabilities"])
            or type(handle["expires_at"]) not in (int, float)
            or not math.isfinite(handle["expires_at"])):
        raise GMError("RESULT_LEASE_INVALID", "Malformed result lease")
    if handle["expires_at"] <= time.time():
        raise GMError("RESULT_NOT_RESIDENT", "Result lease expired; explicit replay required", 4)
    if binding is not None:
        keys = BINDING_FIELDS | VERSION_FIELDS if hybrid else BINDING_FIELDS
        if not isinstance(binding, dict) or set(binding) != keys:
            raise GMError("RESULT_LEASE_INVALID", "Complete result binding required")
        if any(type(binding[key]) is not type(handle[key]) or binding[key] != handle[key]
               for key in keys):
            raise GMError("RESULT_NOT_RESIDENT", "Stale result identity; explicit replay required", 4)
    return dict(handle)
