"""Small command line helpers for stored duration files."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping


def list_slowest_tests() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--durations-path",
        help="Path to the JSON duration file. Defaults to .test_durations.",
        default=".test_durations",
        type=argparse.FileType(),
    )
    parser.add_argument(
        "-c",
        "--count",
        help="How many slowest tests to list.",
        default=10,
        type=int,
    )
    args = parser.parse_args()
    _list_slowest_tests(json.load(args.durations_path), args.count)


def _list_slowest_tests(durations: Mapping[str, float], count: int) -> None:
    slowest_tests = tuple(
        sorted(durations.items(), key=lambda item: item[1], reverse=True)
    )[:count]
    for test, duration in slowest_tests:
        print(f"{duration:.2f} {test}")

