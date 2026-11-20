"""Explicit offline CLI. Fit and replay use the desktop's service."""
import argparse
import json
import uuid
from pathlib import Path

from . import service


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("inspect", "fit", "replay"))
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--alpha", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.operation == "inspect":
        from ..cpu_recipe.validation import inspect_cpu_recipe
        print(json.dumps(inspect_cpu_recipe(args.recipe), ensure_ascii=False))
        return 0
    if not args.source or ((args.reference is not None) != (args.operation == "fit")):
        parser.error("Select source and, for fit, reference")
    key = str(uuid.uuid4())
    row = None
    try:
        row = service.submit(args.operation, args.source, args.reference, args.recipe, key)
        if row.get("binding") and args.output:
            service.consume(key, row, "export", destination=str(args.output.absolute()), alpha=args.alpha,
                            request_key=str(uuid.uuid4()))
        print(json.dumps({"state": row["state"], "recipe": str(args.recipe)}, ensure_ascii=False))
        return 0 if row["state"] == "SUCCEEDED" else 1
    finally:
        if row and row.get("binding"):
            released = service.consume(key, row, "release")
            if not released.get("released"):
                raise RuntimeError("Release is still pending")
