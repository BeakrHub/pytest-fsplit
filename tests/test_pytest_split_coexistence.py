from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pytest_split.plugin")


def test_pytest_split_options_remain_owned_by_pytest_split(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text("def test_slow():\n    pass\n")
    (tests / "test_fast.py").write_text("def test_fast():\n    pass\n")
    (tmp_path / ".test_durations").write_text(
        json.dumps(
            {
                "tests/test_slow.py::test_slow": 10.0,
                "tests/test_fast.py::test_fast": 1.0,
            }
        )
    )

    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--splits",
            "2",
            "--group",
            "1",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "[pytest-split] Splitting tests with algorithm:" in result.stdout
    assert "pytest-fsplit 1/2:" not in result.stdout

