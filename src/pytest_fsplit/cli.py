"""Small command line helpers for stored duration files."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

from pytest_fsplit.durations import load_file_durations, load_node_durations


def list_slowest_tests() -> None:
    parser = _duration_parser("List slowest test nodes from a duration file.")
    parser.add_argument(
        "--count",
        "-c",
        help="How many slowest entries to list.",
        default=10,
        type=int,
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
        type=int,
    )
    args = parser.parse_args()
    _print_slowest_entries(load_file_durations(Path(args.durations_path)), args.count)


def _duration_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--durations-path",
        help="Path to the JSON duration file. Defaults to .test_durations.",
        default=".test_durations",
    )
    return parser


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
