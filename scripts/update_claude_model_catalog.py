#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vibe.api import resolve_cli_path
from vibe.claude_model_catalog import (
    get_catalog_path,
    infer_bundle_path_from_cli,
    infer_models_from_bundle,
    write_catalog_models,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the tracked Claude model catalog from the current Claude installation bundle."
    )
    parser.add_argument("--cli-path", help="Explicit Claude CLI path. Defaults to resolve_cli_path('claude').")
    parser.add_argument("--bundle-path", help="Explicit Claude bundle path to scan.")
    parser.add_argument(
        "--output",
        help="Output catalog path. Defaults to vibe/data/claude_models.json in this repository.",
    )
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve() if args.output else get_catalog_path(REPO_ROOT / "vibe")

    bundle_path: Path | None = None
    if args.bundle_path:
        bundle_path = Path(args.bundle_path).expanduser().resolve()
    else:
        cli_path = args.cli_path or resolve_cli_path("claude")
        bundle_path = infer_bundle_path_from_cli(cli_path)

    if bundle_path is None or not bundle_path.exists():
        print("Could not find a Claude bundle to scan.", file=sys.stderr)
        return 1

    models = infer_models_from_bundle(bundle_path)
    if not models:
        print(f"No Claude models inferred from {bundle_path}", file=sys.stderr)
        return 1

    written_path = write_catalog_models(models, output_path)
    print(f"Wrote {len(models)} Claude models to {written_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
