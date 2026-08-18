from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pytest_split.plugin")


def write_project(root: Path) -> None:
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text("def test_slow():\n    pass\n")
    (tests / "test_fast.py").write_text("def test_fast():\n    pass\n")
    (root / ".test_durations").write_text(
        json.dumps(
            {
                "tests/test_slow.py::test_slow": 10.0,
                "tests/test_fast.py::test_fast": 1.0,
            }
        )
    )


def run_pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


def test_pytest_split_options_remain_owned_by_pytest_split(tmp_path: Path) -> None:
    write_project(tmp_path)

    result = run_pytest(
        tmp_path,
        "--collect-only",
        "-q",
        "--splits",
        "2",
        "--group",
        "1",
    )

    assert result.returncode == pytest.ExitCode.OK
    assert "[pytest-split] Splitting tests with algorithm:" in result.stdout
    assert "pytest-fsplit 1/2:" not in result.stdout


def test_native_file_shards_work_with_pytest_split_installed(tmp_path: Path) -> None:
    write_project(tmp_path)

    result = run_pytest(
        tmp_path,
        "--collect-only",
        "-q",
        "--fsplits",
        "2",
        "--fgroup",
        "1",
    )

    assert result.returncode == pytest.ExitCode.OK
    assert "tests/test_slow.py::test_slow" in result.stdout
    assert "tests/test_fast.py::test_fast" not in result.stdout


def test_file_shards_reject_pytest_split_selection_options(tmp_path: Path) -> None:
    write_project(tmp_path)

    result = run_pytest(
        tmp_path,
        "--fsplits",
        "2",
        "--fgroup",
        "1",
        "--splits",
        "2",
        "--group",
        "1",
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert (
        "--fsplits cannot be combined with pytest-split selection options"
        in result.stderr
    )


def test_file_shards_reject_pytest_split_duration_storage(tmp_path: Path) -> None:
    write_project(tmp_path)

    result = run_pytest(
        tmp_path,
        "--fsplits",
        "2",
        "--fgroup",
        "1",
        "--store-durations",
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "--fsplits cannot be combined with pytest-split duration storage" in result.stderr
