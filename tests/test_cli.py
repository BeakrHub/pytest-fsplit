from __future__ import annotations

import json
import sys
from pathlib import Path

from pytest_fsplit.cli import list_slowest_files, list_slowest_tests


def test_slowest_tests_cli_lists_node_durations(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    duration_path = tmp_path / "durations.json"
    duration_path.write_text(
        json.dumps(
            {
                "tests/test_alpha.py::test_fast": 1.0,
                "tests/test_beta.py::test_slow": 3.0,
                "tests/test_gamma.py::test_middle": 2.0,
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fsplit-slowest-tests",
            "--durations-path",
            str(duration_path),
            "--count",
            "2",
        ],
    )

    list_slowest_tests()

    assert capsys.readouterr().out.splitlines() == [
        "3.00 tests/test_beta.py::test_slow",
        "2.00 tests/test_gamma.py::test_middle",
    ]


def test_slowest_files_cli_aggregates_node_durations(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    duration_path = tmp_path / "durations.json"
    duration_path.write_text(
        json.dumps(
            {
                "tests/test_alpha.py::test_one": 1.0,
                "tests/test_alpha.py::test_two": 3.0,
                "tests/test_beta.py::test_one": 2.0,
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fsplit-slowest-files",
            "--durations-path",
            str(duration_path),
            "--count",
            "2",
        ],
    )

    list_slowest_files()

    assert capsys.readouterr().out.splitlines() == [
        "4.00 tests/test_alpha.py",
        "2.00 tests/test_beta.py",
    ]

