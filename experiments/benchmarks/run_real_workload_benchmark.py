"""Manual-only DeepSeek + Tavily P5.3 benchmark entry point; never used by CI."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.real_workload import (
    archive_real_workload_benchmark,
    manual_deepseek_tavily_runner,
    run_real_workload_benchmark,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the manual P5.3 DeepSeek + Tavily benchmark.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("experiments/archives/real-workload-benchmarks"),
        help="Parent directory for a timestamped JSON and Markdown archive.",
    )
    return parser.parse_args()


async def _main() -> tuple[Path, Path]:
    args = _arguments()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = await run_real_workload_benchmark(
        manual_deepseek_tavily_runner(),
        configuration={
            "execution": "manual_real_deepseek_tavily",
            "cache": "disabled",
            "ci": False,
        },
    )
    return archive_real_workload_benchmark(result, args.output_root / timestamp)


if __name__ == "__main__":
    json_path, report_path = asyncio.run(_main())
    print(f"Archived JSON: {json_path}")
    print(f"Archived report: {report_path}")
