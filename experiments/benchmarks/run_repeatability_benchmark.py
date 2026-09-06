"""Manual-only P6 DeepSeek + Tavily repeatability benchmark; never used by CI."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.evaluation.real_workload import manual_deepseek_tavily_runner
from experiments.evaluation.repeatability import archive_repeatability_benchmark, run_repeatability_benchmark


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the manual P6 DeepSeek + Tavily repeatability benchmark.")
    parser.add_argument("--rounds", type=int, default=3, help="Sequential P5.3 workload rounds (minimum: 2).")
    parser.add_argument(
        "--output-root", type=Path, default=Path("experiments/archives/repeatability-benchmarks"),
        help="Parent directory for a timestamped JSON and Markdown archive.",
    )
    return parser.parse_args()


async def _main() -> tuple[Path, Path]:
    args = _arguments()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = await run_repeatability_benchmark(
        manual_deepseek_tavily_runner(),
        rounds=args.rounds,
        configuration={
            "execution": "manual_real_deepseek_tavily",
            "cache": "disabled",
            "ci": False,
        },
    )
    return archive_repeatability_benchmark(result, args.output_root / timestamp)


if __name__ == "__main__":
    json_path, report_path = asyncio.run(_main())
    print(f"Archived JSON: {json_path}")
    print(f"Archived report: {report_path}")
