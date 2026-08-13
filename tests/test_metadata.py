from __future__ import annotations

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


def test_console_scripts_are_declared() -> None:
    pyproject = load_pyproject()

    assert pyproject["project"]["scripts"] == {
        "fsplit-plan": "pytest_fsplit.cli:show_plan",
        "fsplit-slowest-files": "pytest_fsplit.cli:list_slowest_files",
        "fsplit-slowest-tests": "pytest_fsplit.cli:list_slowest_tests",
    }
