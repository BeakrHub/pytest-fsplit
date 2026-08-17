"""Duration-weighted file shard planning."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from statistics import median

from pytest_fsplit.collection import (
    discover_candidate_files,
    lexical_absolute,
    normalise_test_paths,
)
from pytest_fsplit.durations import load_file_durations
from pytest_fsplit.errors import FileShardingError
from pytest_fsplit.markers import analyze_marker_expression, discover_zero_weight_files
from pytest_fsplit.models import (
    DEFAULT_FILE_PATTERNS,
    DEFAULT_SPLITTING_ALGORITHM,
    DEFAULT_TEST_PATHS,
    SPLITTING_ALGORITHMS,
    FileShard,
    FileShardPlan,
)


def assign_files_to_shards(
    candidate_files: Iterable[str],
    file_durations: Mapping[str, float],
    shard_count: int,
    *,
    zero_weight_files: Iterable[str] = (),
    splitting_algorithm: str = DEFAULT_SPLITTING_ALGORITHM,
) -> tuple[FileShard, ...]:
    """Assign files using a deterministic file-level splitting algorithm."""

    if shard_count <= 0:
        raise FileShardingError("file shard count must be greater than zero")
    if splitting_algorithm not in SPLITTING_ALGORITHMS:
        raise FileShardingError(
            "file splitting algorithm must be one of "
            f"{', '.join(SPLITTING_ALGORITHMS)}, got {splitting_algorithm!r}"
        )

    files = tuple(sorted(set(candidate_files)))
    if not files:
        raise FileShardingError("no candidate test files were discovered")

    effective_zero_weight_files = set(zero_weight_files) & set(files)
    usable_durations = {
        file_path: float(file_durations[file_path])
        for file_path in files
        if file_path not in effective_zero_weight_files
        and file_path in file_durations
        and file_durations[file_path] > 0
    }
    runnable_files = set(files) - effective_zero_weight_files
    if runnable_files and not usable_durations:
        raise FileShardingError(
            "duration file contains no usable timings for discovered test files"
        )

    fallback_duration = median(usable_durations.values()) if usable_durations else 0.0
    estimated_durations = {
        file_path: (
            0.0
            if file_path in effective_zero_weight_files
            else usable_durations.get(file_path, fallback_duration)
        )
        for file_path in files
    }
    untimed_files = set(files) - usable_durations.keys() - effective_zero_weight_files

    shard_files: list[list[str]] = [[] for _ in range(shard_count)]
    shard_weights = [0.0] * shard_count
    shard_untimed_files: list[list[str]] = [[] for _ in range(shard_count)]
    shard_zero_weight_files: list[list[str]] = [[] for _ in range(shard_count)]

    if splitting_algorithm == "least_duration":
        assigned_files = sorted(files, key=lambda path: (-estimated_durations[path], path))
        shard_numbers = (
            min(range(shard_count), key=lambda index: (len(shard_files[index]), index))
            if file_path in effective_zero_weight_files
            else min(range(shard_count), key=lambda index: (shard_weights[index], index))
            for file_path in assigned_files
        )
    else:
        assigned_files = files
        target_duration = sum(estimated_durations.values()) / shard_count

        def chunk_shard_numbers() -> Iterable[int]:
            shard_number = 0
            for _file_path in assigned_files:
                if (
                    shard_number < shard_count - 1
                    and shard_files[shard_number]
                    and shard_weights[shard_number] >= target_duration
                ):
                    shard_number += 1
                yield shard_number

        shard_numbers = chunk_shard_numbers()

    for file_path, shard_number in zip(assigned_files, shard_numbers, strict=True):
        shard_files[shard_number].append(file_path)
        shard_weights[shard_number] += estimated_durations[file_path]
        if file_path in untimed_files:
            shard_untimed_files[shard_number].append(file_path)
        if file_path in effective_zero_weight_files:
            shard_zero_weight_files[shard_number].append(file_path)

    return tuple(
        FileShard(
            files=tuple(sorted(shard_files[index])),
            estimated_seconds=shard_weights[index],
            untimed_files=tuple(sorted(shard_untimed_files[index])),
            zero_weight_files=tuple(sorted(shard_zero_weight_files[index])),
        )
        for index in range(shard_count)
    )


def _parent_directories(file_paths: Iterable[str]) -> frozenset[str]:
    directories: set[str] = set()
    for file_path in file_paths:
        for parent in PurePosixPath(file_path).parents:
            if parent == PurePosixPath("."):
                break
            directories.add(parent.as_posix())
    return frozenset(directories)


def _build_plan_from_shard(
    root: Path,
    shard_count: int,
    shard_index: int,
    *,
    test_paths: tuple[str, ...],
    file_patterns: tuple[str, ...],
    candidate_files: tuple[str, ...],
    selected_shard: FileShard,
    marker_expression_supported: bool,
    splitting_algorithm: str,
) -> FileShardPlan:
    assigned_files = frozenset(selected_shard.files)
    selected_zero_weight_files = frozenset(selected_shard.zero_weight_files)
    selected_files = assigned_files - selected_zero_weight_files

    return FileShardPlan(
        root=root,
        shard_count=shard_count,
        shard_index=shard_index,
        test_paths=test_paths,
        file_patterns=file_patterns,
        candidate_files=candidate_files,
        assigned_files=assigned_files,
        selected_files=selected_files,
        selected_directories=_parent_directories(selected_files),
        estimated_seconds=selected_shard.estimated_seconds,
        untimed_files=frozenset(selected_shard.untimed_files),
        zero_weight_files=selected_zero_weight_files,
        marker_expression_supported=marker_expression_supported,
        splitting_algorithm=splitting_algorithm,
    )


def build_file_shard_plans(
    root: Path,
    shard_count: int,
    *,
    duration_path: Path | None = None,
    test_paths: Iterable[str] = DEFAULT_TEST_PATHS,
    file_patterns: Iterable[str] = DEFAULT_FILE_PATTERNS,
    marker_expression: str = "",
    ignore_paths: Iterable[str] = (),
    ignore_globs: Iterable[str] = (),
    norecurse_patterns: Iterable[str] = (),
    initial_paths: Iterable[str] = (),
    splitting_algorithm: str = DEFAULT_SPLITTING_ALGORITHM,
) -> tuple[FileShardPlan, ...]:
    """Build all one-based shard plans rooted at a checkout."""

    if shard_count <= 0:
        raise FileShardingError("file shard count must be greater than zero")

    lexical_root = lexical_absolute(root)
    configured_test_paths = normalise_test_paths(lexical_root, test_paths)
    configured_file_patterns = tuple(file_patterns)
    marker_analysis = analyze_marker_expression(marker_expression)
    candidates = discover_candidate_files(
        lexical_root,
        test_paths=configured_test_paths,
        file_patterns=configured_file_patterns,
        ignore_paths=ignore_paths,
        ignore_globs=ignore_globs,
        norecurse_patterns=norecurse_patterns,
        initial_paths=initial_paths,
    )
    durations = load_file_durations(duration_path or lexical_root / ".test_durations")
    zero_weight_files = discover_zero_weight_files(
        lexical_root,
        candidates,
        marker_expression=marker_expression,
    )
    shards = assign_files_to_shards(
        candidates,
        durations,
        shard_count,
        zero_weight_files=zero_weight_files,
        splitting_algorithm=splitting_algorithm,
    )
    return tuple(
        _build_plan_from_shard(
            lexical_root,
            shard_count,
            shard_index,
            test_paths=configured_test_paths,
            file_patterns=configured_file_patterns,
            candidate_files=candidates,
            selected_shard=selected_shard,
            marker_expression_supported=marker_analysis.supported,
            splitting_algorithm=splitting_algorithm,
        )
        for shard_index, selected_shard in enumerate(shards, start=1)
    )


def build_file_shard_plan(
    root: Path,
    shard_count: int,
    shard_index: int,
    *,
    duration_path: Path | None = None,
    test_paths: Iterable[str] = DEFAULT_TEST_PATHS,
    file_patterns: Iterable[str] = DEFAULT_FILE_PATTERNS,
    marker_expression: str = "",
    ignore_paths: Iterable[str] = (),
    ignore_globs: Iterable[str] = (),
    norecurse_patterns: Iterable[str] = (),
    initial_paths: Iterable[str] = (),
    splitting_algorithm: str = DEFAULT_SPLITTING_ALGORITHM,
) -> FileShardPlan:
    """Build a one-based shard plan rooted at a checkout."""

    if shard_count <= 0:
        raise FileShardingError("file shard count must be greater than zero")
    if shard_index <= 0 or shard_index > shard_count:
        raise FileShardingError(
            f"file shard index must be between 1 and {shard_count}, got {shard_index}"
        )

    return build_file_shard_plans(
        root,
        shard_count,
        duration_path=duration_path,
        test_paths=test_paths,
        file_patterns=file_patterns,
        marker_expression=marker_expression,
        ignore_paths=ignore_paths,
        ignore_globs=ignore_globs,
        norecurse_patterns=norecurse_patterns,
        initial_paths=initial_paths,
        splitting_algorithm=splitting_algorithm,
    )[shard_index - 1]
