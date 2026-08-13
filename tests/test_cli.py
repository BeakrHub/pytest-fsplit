from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pytest_fsplit.cli import list_slowest_files, list_slowest_tests, show_plan


def write_plan_project(root: Path) -> None:
    tests = root / "tests"
    tests.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (tests / f"test_{name}.py").write_text(f"def test_{name}():\n    pass\n")
    (root / ".test_durations").write_text(
        json.dumps(
            {
                "tests/test_alpha.py::test_alpha": 10.0,
                "tests/test_beta.py::test_beta": 4.0,
                "tests/test_gamma.py::test_gamma": 1.0,
            }
        )
    )


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


def test_cli_rejects_non_positive_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    duration_path = tmp_path / "durations.json"
    duration_path.write_text(json.dumps({"tests/test_alpha.py::test_one": 1.0}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fsplit-slowest-files",
            "--durations-path",
            str(duration_path),
            "--count",
            "0",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        list_slowest_files()

    assert exc_info.value.code == 2


def test_plan_cli_prints_all_shard_summaries(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    write_plan_project(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fsplit-plan",
            "--root",
            str(tmp_path),
            "--fsplits",
            "2",
        ],
    )

    show_plan()

    assert capsys.readouterr().out.splitlines() == [
        "pytest-fsplit plan: 2 shards, least_duration, 3 candidate files",
        (
            "shard 1/2: least_duration, 1/3 files assigned, 1 collected, "
            "10.00s estimated, 0 untimed, 0 marker-excluded before collection"
        ),
        (
            "shard 2/2: least_duration, 2/3 files assigned, 2 collected, "
            "5.00s estimated, 0 untimed, 0 marker-excluded before collection"
        ),
    ]


def test_plan_cli_can_show_assigned_files(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    write_plan_project(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fsplit-plan",
            "--root",
            str(tmp_path),
            "--fsplits",
            "2",
            "--show-files",
        ],
    )

    show_plan()

    assert "  tests/test_alpha.py" in capsys.readouterr().out.splitlines()


def test_plan_cli_supports_json_output(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    write_plan_project(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fsplit-plan",
            "--root",
            str(tmp_path),
            "--fsplits",
            "2",
            "--json",
        ],
    )

    show_plan()

    plan = json.loads(capsys.readouterr().out)
    assert plan["shard_count"] == 2
    assert plan["splitting_algorithm"] == "least_duration"
    assert plan["candidate_file_count"] == 3
    assert plan["shards"][0]["assigned_files"] == ["tests/test_alpha.py"]
