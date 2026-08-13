from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("nbval")


def test_nbval_lax_notebooks_are_sharded_as_files(tmp_path: Path) -> None:
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["x = 1\n"],
            }
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    notebooks = tmp_path / "notebooks"
    notebooks.mkdir()
    (notebooks / "slow.ipynb").write_text(json.dumps(notebook))
    (notebooks / "fast.ipynb").write_text(json.dumps(notebook))
    (tmp_path / ".test_durations").write_text(
        json.dumps(
            {
                "notebooks/slow.ipynb::Cell 0": 10.0,
                "notebooks/fast.ipynb::Cell 0": 1.0,
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
            "--nbval-lax",
            "--fsplits",
            "2",
            "--fgroup",
            "1",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "notebooks/slow.ipynb::Cell 0" in result.stdout
    assert "notebooks/fast.ipynb::Cell 0" not in result.stdout

