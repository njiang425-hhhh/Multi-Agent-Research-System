"""Manual P16 DeepSeek + Tavily showcase entry point; never used by CI."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.showcase import (
    SHOWCASE_CASES,
    archive_showcase,
    manual_deepseek_tavily_showcase_runner,
    run_showcase,
)


def _arguments() -> argparse.Namespace:
    case_ids = [item.case_id for item in SHOWCASE_CASES]
    parser = argparse.ArgumentParser(description="Run the manual P16 DeepSeek + Tavily showcase.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("experiments/archives/showcase"),
        help="Parent directory for one timestamped showcase archive.",
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=case_ids,
        help="Run only one named fixed showcase case; repeat to select several in dataset order.",
    )
    parser.add_argument(
        "--memory-case",
        choices=case_ids,
        help="Explicitly enable existing P8 local memory for exactly one selected case (default: off).",
    )
    return parser.parse_args()


async def _main() -> tuple[Path, Path]:
    args = _arguments()
    requested = set(args.case or [])
    cases = [item for item in SHOWCASE_CASES if not requested or item.case_id in requested]
    if args.memory_case and args.memory_case not in {item.case_id for item in cases}:
        raise ValueError("--memory-case must also be selected by --case")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_directory = args.output_root / timestamp
    result = await run_showcase(
        manual_deepseek_tavily_showcase_runner(),
        cases=cases,
        memory_case_id=args.memory_case,
        memory_store_path=archive_directory / "memory.db" if args.memory_case else None,
    )
    return archive_showcase(result, archive_directory)


if __name__ == "__main__":
    json_path, report_path = asyncio.run(_main())
    print(f"Archived JSON: {json_path}")
    print(f"Archived report: {report_path}")
