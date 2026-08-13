from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pytest_fsplit.core import (
    FileShardingError,
    analyze_marker_expression,
    assign_files_to_shards,
    build_file_shard_plan,
    discover_candidate_files,
    discover_zero_weight_files,
    load_file_durations,
    should_ignore_collection_path,
)


def write_file(
    root: Path,
    relative_path: str,
    text: str = "def test_example():\n    pass\n",
) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def write_durations(root: Path, durations: object) -> Path:
    path = root / ".test_durations"
    path.write_text(json.dumps(durations))
    return path


def test_load_file_durations_aggregates_node_timings_by_file(tmp_path: Path) -> None:
    duration_path = write_durations(
        tmp_path,
        {
            "tests/test_alpha.py::test_one": 1.25,
            "tests/test_alpha.py::test_two": 2.75,
            "tests/test_beta.py::test_one": 4.0,
        },
    )

    assert load_file_durations(duration_path) == {
        "tests/test_alpha.py": 4.0,
        "tests/test_beta.py": 4.0,
    }


def test_load_file_durations_rejects_unusable_files(tmp_path: Path) -> None:
    duration_path = write_durations(tmp_path, {"tests/test_alpha.py::test_one": -1})

    with pytest.raises(FileShardingError, match="finite, non-negative"):
        load_file_durations(duration_path)


def test_assign_files_uses_deterministic_lpt_and_median_for_new_files() -> None:
    shards = assign_files_to_shards(
        [
            "tests/test_alpha.py",
            "tests/test_beta.py",
            "tests/test_gamma.py",
            "tests/test_new.py",
        ],
        {
            "tests/test_alpha.py": 10.0,
            "tests/test_beta.py": 6.0,
            "tests/test_gamma.py": 2.0,
        },
        2,
    )

    assert shards[0].files == ("tests/test_alpha.py", "tests/test_gamma.py")
    assert shards[0].estimated_seconds == 12.0
    assert shards[1].files == ("tests/test_beta.py", "tests/test_new.py")
    assert shards[1].estimated_seconds == 12.0
    assert shards[1].untimed_files == ("tests/test_new.py",)


def test_discover_candidate_files_honors_ignores_and_norecursedirs(tmp_path: Path) -> None:
    write_file(tmp_path, "tests/test_kept.py")
    write_file(tmp_path, "tests/generated/test_ignored.py")
    write_file(tmp_path, "tests/build/test_norecurse.py")

    assert discover_candidate_files(
        tmp_path,
        ignore_globs=["tests/generated/*"],
        norecurse_patterns=["build"],
    ) == ("tests/test_kept.py",)


def test_explicit_initial_path_bypasses_collection_ignores(tmp_path: Path) -> None:
    write_file(tmp_path, "tests/test_kept.py")

    assert discover_candidate_files(
        tmp_path,
        ignore_globs=["*"],
        initial_paths=["tests/test_kept.py"],
        test_paths=["tests/test_kept.py"],
    ) == ("tests/test_kept.py",)


def test_symlinked_external_test_directory_is_a_logical_candidate(tmp_path: Path) -> None:
    external = tmp_path / "external"
    write_file(external, "test_external.py")
    tests = tmp_path / "tests"
    tests.mkdir()
    try:
        (tests / "linked").symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    assert discover_candidate_files(tmp_path) == ("tests/linked/test_external.py",)


def test_duplicate_physical_file_through_two_logical_paths_fails(tmp_path: Path) -> None:
    write_file(tmp_path, "tests/test_shared.py")
    try:
        (tmp_path / "tests" / "test_alias.py").symlink_to("test_shared.py")
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(FileShardingError, match="multiple logical paths"):
        discover_candidate_files(tmp_path)


def test_directory_symlink_cycle_fails_promptly(tmp_path: Path) -> None:
    write_file(tmp_path, "tests/test_before_cycle.py")
    try:
        (tmp_path / "tests" / "cycle").symlink_to(tmp_path / "tests", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(FileShardingError, match="symlink cycle"):
        discover_candidate_files(tmp_path)


def test_marker_expression_analysis_supports_grouped_negation() -> None:
    analysis = analyze_marker_expression("not (slow or network) and not flaky")

    assert analysis.supported is True
    assert analysis.excluded_markers == frozenset({"slow", "network", "flaky"})


def test_zero_weight_files_only_use_stable_module_markers(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        "tests/test_slow.py",
        "import pytest\npytestmark = pytest.mark.slow\n\ndef test_example():\n    pass\n",
    )
    write_file(
        tmp_path,
        "tests/test_dynamic.py",
        "\n".join(
            [
                "import pytest",
                "pytestmark = [pytest.mark.slow]",
                "pytestmark.clear()",
                "",
                "def test_example():",
                "    pass",
                "",
            ]
        ),
    )

    assert discover_zero_weight_files(
        tmp_path,
        ["tests/test_slow.py", "tests/test_dynamic.py"],
        marker_expression="not slow",
    ) == frozenset({"tests/test_slow.py"})


def test_build_plan_and_pruning_preserve_checkout_symlink_paths(tmp_path: Path) -> None:
    real_checkout = tmp_path / "real"
    write_file(real_checkout, "tests/test_alpha.py")
    write_file(real_checkout, "tests/test_beta.py")
    write_durations(
        real_checkout,
        {
            "tests/test_alpha.py::test_example": 10.0,
            "tests/test_beta.py::test_example": 1.0,
        },
    )
    checkout_link = tmp_path / "checkout-link"
    try:
        checkout_link.symlink_to(real_checkout, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    plan = build_file_shard_plan(checkout_link, 2, 1)

    assert plan.root == Path(os.path.abspath(checkout_link))
    assert should_ignore_collection_path(checkout_link / "tests", plan) is None
    assert should_ignore_collection_path(checkout_link / "tests/test_alpha.py", plan) is None
    assert should_ignore_collection_path(checkout_link / "tests/test_beta.py", plan) is True
