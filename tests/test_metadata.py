from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).parents[1]


def load_pyproject() -> dict[str, object]:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())


def test_pytest_plugin_entry_point_is_declared() -> None:
    pyproject = load_pyproject()

    assert pyproject["project"]["entry-points"]["pytest11"] == {
        "pytest-fsplit": "pytest_fsplit.plugin",
    }


def test_pytest_dependency_range_matches_compatibility_ci() -> None:
    pyproject = load_pyproject()

    assert "pytest>=7" in pyproject["project"]["dependencies"]


def test_python_classifiers_match_ci_matrix() -> None:
    pyproject = load_pyproject()
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    classified_versions = {
        classifier.rsplit("::", 1)[1].strip()
        for classifier in pyproject["project"]["classifiers"]
        if classifier.startswith("Programming Language :: Python :: 3.")
    }
    tested_versions = set(re.findall(r'- "(\d+\.\d+)"', workflow))

    assert classified_versions == tested_versions


def test_console_scripts_are_declared() -> None:
    pyproject = load_pyproject()

    assert pyproject["project"]["scripts"] == {
        "fsplit-plan": "pytest_fsplit.cli:show_plan",
        "fsplit-slowest-files": "pytest_fsplit.cli:list_slowest_files",
        "fsplit-slowest-tests": "pytest_fsplit.cli:list_slowest_tests",
        "slowest-tests": "pytest_fsplit.cli:list_slowest_tests",
    }
