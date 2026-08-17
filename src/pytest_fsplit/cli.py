"""Small command line helpers for stored duration files."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from pytest_fsplit.durations import load_file_durations, load_node_durations
from pytest_fsplit.errors import FileShardingError
from pytest_fsplit.models import (
    DEFAULT_FILE_PATTERNS,
    DEFAULT_SPLITTING_ALGORITHM,
    SPLITTING_ALGORITHMS,
    FileShardPlan,
)
from pytest_fsplit.planning import build_file_shard_plans
from pytest_fsplit.reporting import format_file_shard_plan_summary


def list_slowest_tests() -> None:
    parser = _duration_parser("List slowest test nodes from a duration file.")
    parser.add_argument(
        "--count",
        "-c",
        help="How many slowest entries to list.",
        default=10,
        type=_positive_int,
    )
    args = parser.parse_args()
    _print_slowest_entries(load_node_durations(Path(args.durations_path)), args.count)


def list_slowest_files() -> None:
    parser = _duration_parser("List slowest test files from a duration file.")
    parser.add_argument(
        "--count",
        "-c",
        help="How many slowest entries to list.",
        default=10,
        type=_positive_int,
    )
    args = parser.parse_args()
    _print_slowest_entries(load_file_durations(Path(args.durations_path)), args.count)


def show_plan() -> None:
    parser = argparse.ArgumentParser(description="Show the computed pytest-fsplit file shards.")
    parser.add_argument(
        "--root",
        help="Project root used for test discovery. Defaults to the current directory.",
        default=".",
    )
    parser.add_argument(
        "--fsplits",
        help="Number of file shards to plan.",
        required=True,
        type=_positive_int,
    )
    parser.add_argument(
        "--durations-path",
        help="Path to the JSON duration file. Defaults to .test_durations.",
        default=".test_durations",
    )
    parser.add_argument(
        "--test-path",
        help="Test path to discover. May be supplied multiple times. Defaults to tests.",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--file-pattern",
        help="File path pattern to shard. May be supplied multiple times.",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--ignore",
        help="Collection path to ignore. May be supplied multiple times.",
        action="append",
        default=(),
    )
    parser.add_argument(
        "--ignore-glob",
        help="Collection glob to ignore. May be supplied multiple times.",
        action="append",
        default=(),
    )
    parser.add_argument(
        "--norecursedirs",
        help="Directory pattern not to recurse into. May be supplied multiple times.",
        action="append",
        default=(),
    )
    parser.add_argument(
        "-m",
        "--marker-expression",
        help="Marker expression used for static whole-file marker pruning.",
        default="",
    )
    parser.add_argument(
        "--algorithm",
        help="File splitting algorithm.",
        choices=SPLITTING_ALGORITHMS,
        default=DEFAULT_SPLITTING_ALGORITHM,
    )
    parser.add_argument(
        "--show-files",
        help="Print files assigned to each shard after each shard summary.",
        action="store_true",
    )
    parser.add_argument(
        "--json",
        help="Print the plan as JSON.",
        action="store_true",
        dest="json_output",
    )
    args = parser.parse_args()

    root = Path(args.root)
    duration_path = Path(args.durations_path)
    if not duration_path.is_absolute():
        duration_path = root / duration_path

    try:
        plans = build_file_shard_plans(
            root,
            args.fsplits,
            duration_path=duration_path,
            test_paths=args.test_path or ("tests",),
            file_patterns=args.file_pattern or DEFAULT_FILE_PATTERNS,
            marker_expression=args.marker_expression,
            ignore_paths=args.ignore,
            ignore_globs=args.ignore_glob,
            norecurse_patterns=args.norecursedirs,
            splitting_algorithm=args.algorithm,
        )
    except FileShardingError as exc:
        parser.error(str(exc))

    if args.json_output:
        print(json.dumps(_plans_to_jsonable(plans), sort_keys=True, indent=2))
        return

    for line in _format_plan_lines(plans, show_files=args.show_files):
        print(line)


def _duration_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--durations-path",
        help="Path to the JSON duration file. Defaults to .test_durations.",
        default=".test_durations",
    )
    return parser


def _positive_int(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if count < 1:
        raise argparse.ArgumentTypeError("must be greater than or equal to 1")
    return count


def _format_slowest_entries(durations: Mapping[str, float], count: int) -> tuple[str, ...]:
    return tuple(
        f"{duration:.2f} {node_id}"
        for node_id, duration in sorted(
            durations.items(),
            key=lambda item: (-item[1], item[0]),
        )[:count]
    )


def _print_slowest_entries(durations: Mapping[str, float], count: int) -> None:
    for line in _format_slowest_entries(durations, count):
        print(line)


def _format_plan_lines(
    plans: tuple[FileShardPlan, ...],
    *,
    show_files: bool = False,
) -> tuple[str, ...]:
    if not plans:
        return ()
    first_plan = plans[0]
    lines = [
        (
            "pytest-fsplit plan: "
            f"{first_plan.shard_count} shards, "
            f"{first_plan.splitting_algorithm}, "
            f"{len(first_plan.candidate_files)} candidate files"
        )
    ]
    for plan in plans:
        lines.append(format_file_shard_plan_summary(plan, prefix="shard"))
        if show_files:
            lines.extend(f"  {file_path}" for file_path in sorted(plan.assigned_files))
    return tuple(lines)


def _plans_to_jsonable(plans: tuple[FileShardPlan, ...]) -> dict[str, object]:
    if not plans:
        return {"shards": []}
    first_plan = plans[0]
    return {
        "shard_count": first_plan.shard_count,
        "splitting_algorithm": first_plan.splitting_algorithm,
        "candidate_file_count": len(first_plan.candidate_files),
        "shards": [
            {
                "shard_index": plan.shard_index,
                "assigned_file_count": len(plan.assigned_files),
                "selected_file_count": len(plan.selected_files),
                "estimated_seconds": plan.estimated_seconds,
                "untimed_file_count": len(plan.untimed_files),
                "zero_weight_file_count": len(plan.zero_weight_files),
                "assigned_files": sorted(plan.assigned_files),
                "selected_files": sorted(plan.selected_files),
                "untimed_files": sorted(plan.untimed_files),
                "zero_weight_files": sorted(plan.zero_weight_files),
            }
            for plan in plans
        ],
    }
