from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def write_project(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text("def test_slow():\n    pass\n")
    (tests / "test_fast.py").write_text("def test_fast():\n    pass\n")
    (pytester.path / ".test_durations").write_text(
        json.dumps(
            {
                "tests/test_slow.py::test_slow": 10.0,
                "tests/test_fast.py::test_fast": 1.0,
            }
        )
    )
    pytester.makeini("[pytest]\ntestpaths = tests\n")


def write_equal_duration_project(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    durations: dict[str, float] = {}
    for name in ("a", "b", "c", "d"):
        test_file = tests / f"test_{name}.py"
        test_file.write_text(f"def test_{name}():\n    pass\n")
        durations[f"tests/test_{name}.py::test_{name}"] = 1.0
    (pytester.path / ".test_durations").write_text(json.dumps(durations))
    pytester.makeini("[pytest]\ntestpaths = tests\n")


def collected_node_ids(output: str) -> set[str]:
    return {
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    }


def test_file_shard_collects_only_the_selected_file(pytester: pytest.Pytester) -> None:
    write_project(pytester)

    result = pytester.runpytest(
        "--collect-only",
        "-q",
        "--fsplits",
        "2",
        "--fgroup",
        "1",
    )

    result.assert_outcomes()
    assert collected_node_ids(result.stdout.str()) == {"tests/test_slow.py::test_slow"}


def test_all_file_shards_match_unsharded_collection(pytester: pytest.Pytester) -> None:
    write_project(pytester)
    unsharded = pytester.runpytest("--collect-only", "-q")
    unsharded.assert_outcomes()

    sharded: set[str] = set()
    for index in (1, 2):
        result = pytester.runpytest(
            "--collect-only",
            "-q",
            "--fsplits",
            "2",
            "--fgroup",
            str(index),
        )
        result.assert_outcomes()
        node_ids = collected_node_ids(result.stdout.str())
        assert not (sharded & node_ids)
        sharded |= node_ids

    assert sharded == collected_node_ids(unsharded.stdout.str())


def test_pytest_split_style_shard_options_are_supported(
    pytester: pytest.Pytester,
) -> None:
    write_project(pytester)

    result = pytester.runpytest(
        "--collect-only",
        "-q",
        "--splits",
        "2",
        "--group",
        "1",
    )

    result.assert_outcomes()
    assert collected_node_ids(result.stdout.str()) == {"tests/test_slow.py::test_slow"}


def test_pytest_split_style_equals_options_are_supported(
    pytester: pytest.Pytester,
) -> None:
    write_equal_duration_project(pytester)
    duration_path = pytester.path / "durations.json"
    duration_path.write_text(
        json.dumps(
            {
                "tests/test_a.py::test_a": 1.0,
                "tests/test_b.py::test_b": 1.0,
                "tests/test_c.py::test_c": 1.0,
                "tests/test_d.py::test_d": 1.0,
            }
        )
    )

    result = pytester.runpytest(
        "--collect-only",
        "-q",
        f"--durations-path={duration_path}",
        "--splitting-algorithm=duration_based_chunks",
        "--splits=2",
        "--group=1",
    )

    result.assert_outcomes()
    assert collected_node_ids(result.stdout.str()) == {
        "tests/test_a.py::test_a",
        "tests/test_b.py::test_b",
    }


def test_pytest_split_style_options_are_supported_from_pytest_ini_addopts(
    pytester: pytest.Pytester,
) -> None:
    write_project(pytester)
    (pytester.path / "tox.ini").write_text(
        "[pytest]\n"
        "testpaths = tests\n"
        "addopts = --splits 2 --group 1\n"
    )

    result = pytester.runpytest("--collect-only", "-q")

    result.assert_outcomes()
    assert collected_node_ids(result.stdout.str()) == {"tests/test_slow.py::test_slow"}


def test_pytest_split_style_options_are_supported_from_pytest_addopts(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_project(pytester)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--splits 2 --group 1")

    result = pytester.runpytest("--collect-only", "-q")

    result.assert_outcomes()
    assert collected_node_ids(result.stdout.str()) == {"tests/test_slow.py::test_slow"}


def test_pytest_split_style_duration_storage_options_are_supported(
    pytester: pytest.Pytester,
) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("def test_example():\n    pass\n")
    duration_path = pytester.path / "durations.json"
    duration_path.write_text(json.dumps({"tests/test_deleted.py::test_deleted": 5.0}))
    pytester.makeini("[pytest]\ntestpaths = tests\n")

    result = pytester.runpytest(
        "--store-durations",
        "--clean-durations",
        "--durations-path",
        str(duration_path),
    )

    result.assert_outcomes(passed=1)
    stored_durations = json.loads(duration_path.read_text())
    assert set(stored_durations) == {"tests/test_example.py::test_example"}


def test_pytest_split_style_options_are_not_rewritten_when_pytest_split_is_loaded(
    pytester: pytest.Pytester,
) -> None:
    write_project(pytester)
    plugin_package = pytester.path / "pytest_split"
    plugin_package.mkdir()
    (plugin_package / "__init__.py").write_text("")
    (plugin_package / "plugin.py").write_text(
        'def pytest_addoption(parser):\n'
        '    parser.addoption("--splits", dest="splits", type=int)\n'
        '    parser.addoption("--group", dest="group", type=int)\n'
    )
    pytester.syspathinsert()

    result = pytester.runpytest(
        "-q",
        "-p",
        "pytest_split.plugin",
        "--splits",
        "2",
        "--group",
        "1",
    )

    result.assert_outcomes(passed=2)


def test_item_order_randomization_does_not_change_file_shard_membership(
    pytester: pytest.Pytester,
) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    durations: dict[str, float] = {}
    for name in ("alpha", "beta"):
        test_file = tests / f"test_{name}.py"
        test_file.write_text(
            f"def test_{name}_one():\n    pass\n\n"
            f"def test_{name}_two():\n    pass\n"
        )
        durations[f"tests/test_{name}.py::test_{name}_one"] = 1.0
        durations[f"tests/test_{name}.py::test_{name}_two"] = 1.0
    (pytester.path / ".test_durations").write_text(json.dumps(durations))
    pytester.makeini("[pytest]\ntestpaths = tests\n")
    pytester.makeconftest(
        """
        import pytest

        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(items):
            items.reverse()
        """
    )

    unsharded = pytester.runpytest("--collect-only", "-q")
    unsharded.assert_outcomes()

    sharded: set[str] = set()
    for index in (1, 2):
        result = pytester.runpytest(
            "--collect-only",
            "-q",
            "--fsplits",
            "2",
            "--fgroup",
            str(index),
        )
        result.assert_outcomes()
        node_ids = collected_node_ids(result.stdout.str())
        assert not (sharded & node_ids)
        sharded |= node_ids

    assert sharded == collected_node_ids(unsharded.stdout.str())


def test_duration_based_chunks_option_collects_contiguous_file_ranges(
    pytester: pytest.Pytester,
) -> None:
    write_equal_duration_project(pytester)

    result = pytester.runpytest(
        "--collect-only",
        "-q",
        "--fsplit-algorithm",
        "duration_based_chunks",
        "--fsplits",
        "2",
        "--fgroup",
        "1",
    )

    result.assert_outcomes()
    assert collected_node_ids(result.stdout.str()) == {
        "tests/test_a.py::test_a",
        "tests/test_b.py::test_b",
    }


def test_missing_duration_file_fails_when_splitting(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("def test_example():\n    pass\n")
    pytester.makeini("[pytest]\ntestpaths = tests\n")

    result = pytester.runpytest(
        "--fsplits",
        "2",
        "--fgroup",
        "1",
    )

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["ERROR: pytest-fsplit failed: duration file does not exist:*"])


def test_module_marked_empty_shard_exits_successfully(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text(
        "import pytest\npytestmark = pytest.mark.slow\n\ndef test_slow():\n    pass\n"
    )
    (pytester.path / ".test_durations").write_text(
        json.dumps({"tests/test_slow.py::test_slow": 10.0})
    )
    pytester.makeini("[pytest]\ntestpaths = tests\nmarkers = slow\n")

    result = pytester.runpytest(
        "-m",
        "not slow",
        "--fsplits",
        "1",
        "--fgroup",
        "1",
    )

    result.assert_outcomes()


def test_runtime_deselected_empty_shard_still_fails(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text(
        "import pytest\n\n@pytest.mark.slow\ndef test_slow():\n    pass\n"
    )
    (pytester.path / ".test_durations").write_text(
        json.dumps({"tests/test_slow.py::test_slow": 10.0})
    )
    pytester.makeini("[pytest]\ntestpaths = tests\nmarkers = slow\n")

    result = pytester.runpytest(
        "-m",
        "not slow",
        "--fsplits",
        "1",
        "--fgroup",
        "1",
    )

    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED


def test_planned_empty_file_shard_exits_successfully(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("def test_example():\n    pass\n")
    (pytester.path / ".test_durations").write_text(
        json.dumps({"tests/test_example.py::test_example": 1.0})
    )
    pytester.makeini("[pytest]\ntestpaths = tests\n")

    result = pytester.runpytest(
        "--fsplits",
        "3",
        "--fgroup",
        "2",
    )

    result.assert_outcomes()


def test_store_durations_writes_pytest_split_compatible_json(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "def test_one():\n    pass\n\ndef test_two():\n    pass\n"
    )
    pytester.makeini("[pytest]\ntestpaths = tests\n")

    result = pytester.runpytest(
        "--fsplit-store-durations",
        "--fsplit-durations-path",
        str(pytester.path / "durations.json"),
    )

    result.assert_outcomes(passed=2)
    durations = json.loads((pytester.path / "durations.json").read_text())
    assert set(durations) == {
        "tests/test_example.py::test_one",
        "tests/test_example.py::test_two",
    }
    assert all(isinstance(duration, int | float) for duration in durations.values())


def test_store_durations_merges_existing_entries_by_default(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("def test_example():\n    pass\n")
    duration_path = pytester.path / "durations.json"
    duration_path.write_text(json.dumps({"tests/test_deleted.py::test_deleted": 5.0}))
    pytester.makeini("[pytest]\ntestpaths = tests\n")

    result = pytester.runpytest(
        "--fsplit-store-durations",
        "--fsplit-durations-path",
        str(duration_path),
    )

    result.assert_outcomes(passed=1)
    durations = json.loads(duration_path.read_text())
    assert set(durations) == {
        "tests/test_deleted.py::test_deleted",
        "tests/test_example.py::test_example",
    }


def test_clean_durations_drops_existing_entries(pytester: pytest.Pytester) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("def test_example():\n    pass\n")
    duration_path = pytester.path / "durations.json"
    duration_path.write_text(json.dumps({"tests/test_deleted.py::test_deleted": 5.0}))
    pytester.makeini("[pytest]\ntestpaths = tests\n")

    result = pytester.runpytest(
        "--fsplit-store-durations",
        "--fsplit-clean-durations",
        "--fsplit-durations-path",
        str(duration_path),
    )

    result.assert_outcomes(passed=1)
    assert set(json.loads(duration_path.read_text())) == {
        "tests/test_example.py::test_example",
    }


def test_clean_durations_requires_store_durations(pytester: pytest.Pytester) -> None:
    write_project(pytester)

    result = pytester.runpytest("--fsplit-clean-durations")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["ERROR: --fsplit-clean-durations requires --fsplit-store-durations"]
    )


def test_store_durations_rejects_malformed_existing_duration_file(
    pytester: pytest.Pytester,
) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("def test_example():\n    pass\n")
    duration_path = pytester.path / "durations.json"
    duration_path.write_text(json.dumps({"tests/test_deleted.py::test_deleted": "bad"}))
    pytester.makeini("[pytest]\ntestpaths = tests\n")

    result = pytester.runpytest(
        "--fsplit-store-durations",
        "--fsplit-durations-path",
        str(duration_path),
    )

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["ERROR: pytest-fsplit could not read existing duration file*"])


def test_sharding_rejects_pytest_split_selection_options(pytester: pytest.Pytester) -> None:
    write_project(pytester)
    pytester.makeconftest(
        """
        def pytest_addoption(parser):
            parser.addoption("--splits", dest="splits", type=int)
            parser.addoption("--group", dest="group", type=int)
        """
    )

    result = pytester.runpytest(
        "--fsplits",
        "2",
        "--fgroup",
        "1",
        "--splits",
        "2",
        "--group",
        "1",
    )

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["ERROR: --fsplits cannot be combined with pytest-split selection options:*"]
    )


def test_sharding_rejects_pytest_split_duration_storage(
    pytester: pytest.Pytester,
) -> None:
    write_project(pytester)
    pytester.makeconftest(
        """
        def pytest_addoption(parser):
            parser.addoption("--store-durations", dest="store_durations", action="store_true")
        """
    )

    result = pytester.runpytest(
        "--fsplits",
        "2",
        "--fgroup",
        "1",
        "--store-durations",
    )

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["ERROR: --fsplits cannot be combined with pytest-split duration storage:*"]
    )


def test_duration_path_defaults_to_invocation_directory(pytester: pytest.Pytester) -> None:
    write_project(pytester)
    subdir = pytester.path / "subdir"
    subdir.mkdir()
    local_duration_path = subdir / ".test_durations"
    local_duration_path.write_text(
        json.dumps(
            {
                "tests/test_slow.py::test_slow": 1.0,
                "tests/test_fast.py::test_fast": 10.0,
            }
        )
    )

    previous_cwd = Path.cwd()
    os.chdir(subdir)
    try:
        result = pytester.runpytest(
            "--rootdir",
            str(pytester.path),
            str(pytester.path / "tests"),
            "--collect-only",
            "-q",
            "--fsplits",
            "2",
            "--fgroup",
            "1",
        )
    finally:
        os.chdir(previous_cwd)

    result.assert_outcomes()
    assert collected_node_ids(result.stdout.str()) == {"tests/test_fast.py::test_fast"}


def test_custom_file_pattern_shards_files_from_other_collectors(
    pytester: pytest.Pytester,
) -> None:
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "slow.case").write_text("slow\n")
    (tests / "fast.case").write_text("fast\n")
    (pytester.path / ".test_durations").write_text(
        json.dumps(
            {
                "tests/slow.case::test_case": 10.0,
                "tests/fast.case::test_case": 1.0,
            }
        )
    )
    pytester.makeini("[pytest]\ntestpaths = tests\n")
    pytester.makeconftest(
        """
        import pytest

        class CaseFile(pytest.File):
            def collect(self):
                yield CaseItem.from_parent(self, name="test_case")

        class CaseItem(pytest.Item):
            def runtest(self):
                pass

            def reportinfo(self):
                return self.path, 0, self.name

        def pytest_collect_file(file_path, parent):
            if file_path.suffix == ".case":
                return CaseFile.from_parent(parent, path=file_path)
            return None
        """
    )

    result = pytester.runpytest(
        "-vv",
        "--fsplit-file-pattern",
        "*.case",
        "--fsplits",
        "2",
        "--fgroup",
        "1",
    )

    result.assert_outcomes(passed=1)
    assert "slow.case" in result.stdout.str()


@pytest.mark.parametrize("nbval_option", ["--nbval", "--nbval-lax"])
def test_nbval_options_automatically_shard_notebook_files(
    pytester: pytest.Pytester,
    nbval_option: str,
) -> None:
    notebooks = pytester.path / "notebooks"
    notebooks.mkdir()
    (notebooks / "slow.ipynb").write_text("{}\n")
    (notebooks / "fast.ipynb").write_text("{}\n")
    (pytester.path / ".test_durations").write_text(
        json.dumps(
            {
                "notebooks/slow.ipynb::Cell 1": 10.0,
                "notebooks/fast.ipynb::Cell 1": 1.0,
            }
        )
    )
    pytester.makeini("[pytest]\ntestpaths = notebooks\n")
    pytester.makeconftest(
        """
        import pytest

        def pytest_addoption(parser):
            parser.addoption("--nbval", dest="nbval", action="store_true")
            parser.addoption("--nbval-lax", dest="nbval_lax", action="store_true")

        class NotebookFile(pytest.File):
            def collect(self):
                yield NotebookCell.from_parent(self, name="Cell 1")

        class NotebookCell(pytest.Item):
            def runtest(self):
                pass

            def reportinfo(self):
                return self.path, 0, self.name

        def pytest_collect_file(file_path, parent):
            if file_path.suffix == ".ipynb" and (
                parent.config.getoption("nbval") or parent.config.getoption("nbval_lax")
            ):
                return NotebookFile.from_parent(parent, path=file_path)
            return None
        """
    )

    result = pytester.runpytest(
        "--collect-only",
        "-q",
        nbval_option,
        "--fsplits",
        "2",
        "--fgroup",
        "1",
    )

    result.assert_outcomes()
    assert "notebooks/slow.ipynb::Cell 1" in result.stdout.str()
    assert "notebooks/fast.ipynb::Cell 1" not in result.stdout.str()


def test_xdist_workers_receive_the_controller_shard_plan(pytester: pytest.Pytester) -> None:
    pytest.importorskip("xdist")
    write_project(pytester)

    result = pytester.runpytest_subprocess(
        "-n",
        "2",
        "--dist",
        "loadgroup",
        "-q",
        "--fsplits",
        "2",
        "--fgroup",
        "1",
    )

    result.assert_outcomes(passed=1)
