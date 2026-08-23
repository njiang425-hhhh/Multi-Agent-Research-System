"""Run the deterministic, local-only P15 Memory Demo Activation showcase."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory import run_memory_demo


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local P15 memory demo")
    parser.add_argument(
        "--store-path",
        help="Optional SQLite path to retain the demo records; temporary by default.",
    )
    args = parser.parse_args()
    print(json.dumps(run_memory_demo(args.store_path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
