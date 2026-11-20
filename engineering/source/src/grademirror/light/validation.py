"""Bounded saved-result evidence for an admitted preview validation row."""
import hashlib
import re

from ..cpu_replay.contract import digest, need


def quantization_rows(source, result, depths, check):
    """Hash row strips in the same order and precision as full-frame export."""
    from ..export_pixels import mix_quantize

    rows = []
    for alpha in (0.0, 0.5, 1.0):
        for depth in depths:
            hashed = hashlib.sha256()
            for start in range(0, len(source), 64):
                region = slice(start, start + 64)
                values = mix_quantize(source[region], result[region], alpha, bits=depth, check=check)
                hashed.update(memoryview(values).cast("B"))
            rows.append(dict(alpha=alpha, bits=depth, sha256=hashed.hexdigest(),
                             shape=list(source.shape), dtype=f"uint{depth}"))
    return rows


def gpu_identity(recipe):
    """Keep container/recipe identity distinct from the values that govern output."""
    names = {
        "recipe": "recipe_sha256",
        "parameter": "parameter_sha256",
        "container": "archive_sha256",
        "math": "profile_contract_sha256",
        "core": "core_sha256",
        "source": "source_sha256",
        "reference": "reference_sha256",
    }
    result = {}
    for key, field in names.items():
        value = recipe.get(field)
        need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
             "LIGHT_COMPARE_IDENTITY_REQUIRED", field)
        result[key] = value
    for field in ("device", "runtime", "settings", "source_identity",
                  "reference_identity", "input_normalization", "color_policy"):
        value = recipe.get(field)
        need(isinstance(value, dict) and value, "LIGHT_COMPARE_IDENTITY_REQUIRED", field)
        result[field] = digest(value)
    return result


def compare_gpu(left, right, *, fine=False):
    """Refuse a missing or divergent numerical identity before pixel comparison."""
    first, second = (gpu_identity(row["recipe"]) for row in (left, right))
    for field in first.keys() - {"recipe", "container"}:
        need(first[field] == second[field], "LIGHT_COMPARE_IDENTITY_CHANGED", field)
    if fine:
        coefficient = left.get("coefficient")
        need(isinstance(coefficient, str) and re.fullmatch(r"[0-9a-f]{64}", coefficient)
             and coefficient == right.get("coefficient"), "LIGHT_COMPARE_COEFFICIENT")
        need(isinstance(left.get("ranges"), (dict, list)) and left["ranges"]
             and digest(left["ranges"]) == digest(right.get("ranges")), "LIGHT_COMPARE_RANGES")
    required = {"O", "P", "LO", "LP", "Y"} if fine else {"O", "Y"}
    for field in required:
        value = left["fields"].get(field)
        need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
             and value == right["fields"].get(field), "LIGHT_COMPARE_FLOAT_FIELD", field)
    expected = {(alpha, bits) for alpha in (0.0, 0.5, 1.0) for bits in ((8, 16) if fine else (8,))}
    def quant(rows):
        result = {}
        for row in rows:
            key = row["alpha"], row["bits"]
            need(key not in result and isinstance(row["sha256"], str)
                 and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "LIGHT_COMPARE_QUANTIZATION")
            result[key] = row["sha256"]
        need(result.keys() == expected, "LIGHT_COMPARE_QUANTIZATION_SCOPE")
        return result
    need(quant(left["quant"]) == quant(right["quant"]), "LIGHT_COMPARE_QUANTIZATION_CHANGED")
    return dict(left=first, right=second)

