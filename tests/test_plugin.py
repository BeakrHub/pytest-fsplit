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
