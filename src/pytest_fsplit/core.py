"""Deterministic file-level shard planning."""

from __future__ import annotations

import ast
import errno
import fnmatch
import os
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from statistics import median

from _pytest.pathlib import fnmatch_ex

from pytest_fsplit.durations import load_file_durations
from pytest_fsplit.errors import FileShardingError

DEFAULT_TEST_PATHS = ("tests",)
DEFAULT_FILE_PATTERNS = ("test_*.py",)
DEFAULT_SPLITTING_ALGORITHM = "least_duration"
SPLITTING_ALGORITHMS = ("least_duration", "duration_based_chunks")


@dataclass(frozen=True)
class FileShard:
    """One shard produced by the longest-processing-time-first assignment."""

    files: tuple[str, ...]
    estimated_seconds: float
    untimed_files: tuple[str, ...]
    zero_weight_files: tuple[str, ...]


@dataclass(frozen=True)
class FileShardPlan:
    """The selected shard plus the paths needed to prune collection."""

    root: Path
    shard_count: int
    shard_index: int
    test_paths: tuple[str, ...]
    file_patterns: tuple[str, ...]
    candidate_files: tuple[str, ...]
    assigned_files: frozenset[str]
    selected_files: frozenset[str]
    selected_directories: frozenset[str]
    estimated_seconds: float
    untimed_files: frozenset[str]
    zero_weight_files: frozenset[str]
    marker_expression_supported: bool = True
    splitting_algorithm: str = DEFAULT_SPLITTING_ALGORITHM


@dataclass(frozen=True)
class MarkerExpressionAnalysis:
    """Static facts that are safe to infer from a pytest marker expression."""

    excluded_markers: frozenset[str]
    supported: bool


def lexical_absolute(path: Path) -> Path:
    """Make a path absolute without resolving symlink components."""

    return Path(os.path.abspath(path))

def normalise_test_paths(root: Path, test_paths: Iterable[str]) -> tuple[str, ...]:
    lexical_root = lexical_absolute(root)
    normalised: set[str] = set()
    for configured_path in test_paths:
        path = Path(str(configured_path).split("::", 1)[0])
        absolute_path = lexical_absolute(path if path.is_absolute() else lexical_root / path)
        try:
            relative_path = absolute_path.relative_to(lexical_root).as_posix()
        except ValueError as exc:
            raise FileShardingError(
                f"configured test path must be inside the repository: {configured_path}"
            ) from exc
        normalised.add(relative_path or ".")
    if not normalised:
        raise FileShardingError("pytest testpaths must contain at least one repository path")
    return tuple(sorted(normalised))


def _matches_any_pattern(path: Path, patterns: Iterable[str]) -> bool:
    return any(fnmatch_ex(pattern, path) for pattern in patterns)


def absolute_collection_patterns(base: Path, configured_patterns: Iterable[str]) -> tuple[str, ...]:
    """Normalize pytest collection paths and globs without expanding them."""

    absolute_patterns: set[str] = set()
    lexical_base = lexical_absolute(base)
    for configured_pattern in configured_patterns:
        pattern = Path(configured_pattern)
        absolute_pattern = pattern if pattern.is_absolute() else lexical_base / pattern
        absolute_patterns.add(str(lexical_absolute(absolute_pattern)))
    return tuple(sorted(absolute_patterns))


def absolute_initial_paths(base: Path, configured_paths: Iterable[str]) -> frozenset[Path]:
    """Normalize pytest collection arguments without resolving symlinks."""

    lexical_base = lexical_absolute(base)
    initial_paths: set[Path] = set()
    for configured_path in configured_paths:
        path_text = str(configured_path).split("::", 1)[0]
        path = Path(path_text)
        absolute_path = path if path.is_absolute() else lexical_base / path
        initial_paths.add(lexical_absolute(absolute_path))
    return frozenset(initial_paths)


def _is_ignored_collection_path(
    path: Path,
    *,
    ignore_paths: tuple[Path, ...],
    ignore_globs: tuple[str, ...],
) -> bool:
    if path in ignore_paths:
        return True
    return any(fnmatch.fnmatch(str(path), ignore_glob) for ignore_glob in ignore_globs)


def _logical_path(path: Path, root: Path) -> str:
    """Return a repository-relative path without following symlinks."""

    try:
        relative_path = lexical_absolute(path).relative_to(root).as_posix()
    except ValueError as exc:
        raise FileShardingError(
            f"discovered test path is outside the repository's logical path: {path}"
        ) from exc
    return relative_path or "."


def _walk_test_files(
    test_root: Path,
    *,
    root: Path,
    ignore_paths: tuple[Path, ...],
    ignore_globs: tuple[str, ...],
    norecurse_patterns: tuple[str, ...],
    initial_paths: frozenset[Path],
) -> Iterable[tuple[Path, tuple[int, int]]]:
    """Yield logical test paths while following symlinks without following cycles."""

    active_directories: dict[tuple[int, int], str] = {}
    initial_paths_with_parents = initial_paths.union(
        parent for initial_path in initial_paths for parent in initial_path.parents
    )

    def visit(path: Path) -> Iterable[tuple[Path, tuple[int, int]]]:
        lexical_path = lexical_absolute(path)
        logical_path = _logical_path(lexical_path, root)
        try:
            path_stat = lexical_path.stat()
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise FileShardingError(
                    "directory symlink cycle detected while discovering tests at "
                    f"{logical_path}"
                ) from exc
            raise FileShardingError(f"could not inspect test path {logical_path}: {exc}") from exc

        physical_identity = (path_stat.st_dev, path_stat.st_ino)
        is_directory = stat.S_ISDIR(path_stat.st_mode)
        ignore_is_bypassed = (
            lexical_path in initial_paths_with_parents
            if is_directory
            else lexical_path in initial_paths
        )
        if not ignore_is_bypassed and _is_ignored_collection_path(
            lexical_path,
            ignore_paths=ignore_paths,
            ignore_globs=ignore_globs,
        ):
            return

        if stat.S_ISREG(path_stat.st_mode):
            yield lexical_path, physical_identity
            return
        if not is_directory:
            return
        if not ignore_is_bypassed and any(
            fnmatch_ex(pattern, lexical_path) for pattern in norecurse_patterns
        ):
            return

        ancestor_path = active_directories.get(physical_identity)
        if ancestor_path is not None:
            raise FileShardingError(
                "directory symlink cycle detected while discovering tests: "
                f"{logical_path} reaches its ancestor {ancestor_path}"
            )

        active_directories[physical_identity] = logical_path
        try:
            try:
                children = sorted(lexical_path.iterdir(), key=lambda child: child.name)
            except (FileNotFoundError, NotADirectoryError):
                return
            except OSError as exc:
                raise FileShardingError(
                    f"could not traverse test directory {logical_path}: {exc}"
                ) from exc
            for child in children:
                yield from visit(child)
        finally:
            del active_directories[physical_identity]

    yield from visit(test_root)


def discover_candidate_files(
    root: Path,
    *,
    test_paths: Iterable[str] = DEFAULT_TEST_PATHS,
    file_patterns: Iterable[str] = DEFAULT_FILE_PATTERNS,
    ignore_paths: Iterable[str] = (),
    ignore_globs: Iterable[str] = (),
    norecurse_patterns: Iterable[str] = (),
    initial_paths: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return files pytest can collect from its configured test roots."""

    lexical_root = lexical_absolute(root)
    configured_test_paths = normalise_test_paths(lexical_root, test_paths)
    patterns = tuple(file_patterns)
    if not patterns:
        raise FileShardingError("file sharding patterns must contain at least one pattern")

    absolute_ignore_paths = tuple(
        Path(pattern) for pattern in absolute_collection_patterns(lexical_root, ignore_paths)
    )
    absolute_ignore_globs = absolute_collection_patterns(lexical_root, ignore_globs)
    configured_norecurse_patterns = tuple(norecurse_patterns)
    configured_initial_paths = absolute_initial_paths(lexical_root, initial_paths)
    if not configured_initial_paths:
        configured_initial_paths = frozenset(
            lexical_root if test_path == "." else lexical_root / test_path
            for test_path in configured_test_paths
        )

    candidates: set[str] = set()
    physical_candidates: dict[tuple[int, int], str] = {}
    for configured_test_path in configured_test_paths:
        test_root = (
            lexical_root
            if configured_test_path == "."
            else lexical_root / configured_test_path
        )
        for path, physical_identity in _walk_test_files(
            test_root,
            root=lexical_root,
            ignore_paths=absolute_ignore_paths,
            ignore_globs=absolute_ignore_globs,
            norecurse_patterns=configured_norecurse_patterns,
            initial_paths=configured_initial_paths,
        ):
            relative_path = PurePosixPath(_logical_path(path, lexical_root))
            if not _matches_any_pattern(path, patterns):
                continue

            logical_path = relative_path.as_posix()
            existing_path = physical_candidates.get(physical_identity)
            if existing_path is not None and existing_path != logical_path:
                first_path, second_path = sorted((existing_path, logical_path))
                raise FileShardingError(
                    "test file is exposed through multiple logical paths that resolve to the "
                    f"same physical file: {first_path} and {second_path}"
                )
            physical_candidates[physical_identity] = logical_path
            candidates.add(logical_path)
    return tuple(sorted(candidates))


def _marker_name(expression: ast.expr) -> str | None:
    target = expression.func if isinstance(expression, ast.Call) else expression
    if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Attribute):
        return None
    if not isinstance(target.value.value, ast.Name):
        return None
    if target.value.value.id != "pytest" or target.value.attr != "mark":
        return None
    return target.attr


def _marker_names(expression: ast.expr) -> frozenset[str]:
    if isinstance(expression, ast.List | ast.Set | ast.Tuple):
        names = {
            marker_name
            for element in expression.elts
            if (marker_name := _marker_name(element)) is not None
        }
        return frozenset(names)
    marker_name = _marker_name(expression)
    return frozenset({marker_name}) if marker_name is not None else frozenset()


def _module_marker_names(module: ast.Module) -> frozenset[str]:
    assignments: list[tuple[ast.expr, set[ast.Name]]] = []
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            matching_targets = {
                target
                for target in statement.targets
                if isinstance(target, ast.Name) and target.id == "pytestmark"
            }
            if matching_targets:
                assignments.append((statement.value, matching_targets))
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "pytestmark"
            and statement.value is not None
        ):
            assignments.append((statement.value, {statement.target}))

    if len(assignments) != 1:
        return frozenset()

    value, assignment_targets = assignments[0]
    references = {
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Name) and node.id == "pytestmark"
    }
    if references != assignment_targets:
        return frozenset()
    return _marker_names(value)


def analyze_marker_expression(marker_expression: str) -> MarkerExpressionAnalysis:
    """Find markers whose presence guarantees that the expression is false."""

    if not marker_expression.strip():
        return MarkerExpressionAnalysis(frozenset(), supported=True)

    try:
        expression = ast.parse(marker_expression, mode="eval").body
    except SyntaxError:
        return MarkerExpressionAnalysis(frozenset(), supported=False)

    def forced_markers(node: ast.expr) -> tuple[set[str], set[str]] | None:
        if isinstance(node, ast.Name):
            return {node.id}, set()
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            operand = forced_markers(node.operand)
            if operand is None:
                return None
            forced_true, forced_false = operand
            return forced_false, forced_true
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And | ast.Or):
            analyzed_values = [forced_markers(value) for value in node.values]
            if any(value is None for value in analyzed_values):
                return None
            values = [value for value in analyzed_values if value is not None]
            forced_true_sets = [value[0] for value in values]
            forced_false_sets = [value[1] for value in values]
            if isinstance(node.op, ast.And):
                forced_true = set.intersection(*forced_true_sets)
                forced_false = set.union(*forced_false_sets)
            else:
                forced_true = set.union(*forced_true_sets)
                forced_false = set.intersection(*forced_false_sets)
            return forced_true, forced_false
        return None

    result = forced_markers(expression)
    if result is None:
        return MarkerExpressionAnalysis(frozenset(), supported=False)
    _, forced_false = result
    return MarkerExpressionAnalysis(frozenset(forced_false), supported=True)


def _is_file_fully_marker_excluded(
    path: Path,
    *,
    excluded_markers: frozenset[str],
) -> bool:
    if not excluded_markers:
        return False

    try:
        module = ast.parse(path.read_text())
    except (OSError, SyntaxError, UnicodeError):
        return False

    return bool(_module_marker_names(module) & excluded_markers)


def discover_zero_weight_files(
    root: Path,
    candidate_files: Iterable[str],
    *,
    marker_expression: str,
) -> frozenset[str]:
    """Find files statically proven to be excluded by the active marker expression."""

    excluded_markers = analyze_marker_expression(marker_expression).excluded_markers
    return frozenset(
        file_path
        for file_path in candidate_files
        if _is_file_fully_marker_excluded(root / file_path, excluded_markers=excluded_markers)
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
    selected_shard = shards[shard_index - 1]
    assigned_files = frozenset(selected_shard.files)
    selected_zero_weight_files = frozenset(selected_shard.zero_weight_files)
    selected_files = assigned_files - selected_zero_weight_files

    return FileShardPlan(
        root=lexical_root,
        shard_count=shard_count,
        shard_index=shard_index,
        test_paths=configured_test_paths,
        file_patterns=configured_file_patterns,
        candidate_files=candidates,
        assigned_files=assigned_files,
        selected_files=selected_files,
        selected_directories=_parent_directories(selected_files),
        estimated_seconds=selected_shard.estimated_seconds,
        untimed_files=frozenset(selected_shard.untimed_files),
        zero_weight_files=selected_zero_weight_files,
        marker_expression_supported=marker_analysis.supported,
        splitting_algorithm=splitting_algorithm,
    )


def should_ignore_collection_path(collection_path: Path, plan: FileShardPlan) -> bool | None:
    """Return True only for directories and test files outside the selected shard."""

    try:
        lexical_collection_path = lexical_absolute(collection_path)
        relative_path = lexical_collection_path.relative_to(plan.root).as_posix()
    except ValueError:
        return None

    relative = PurePosixPath(relative_path)
    collection_roots = tuple(PurePosixPath(path) for path in plan.test_paths)
    if not any(
        root == PurePosixPath(".") or relative == root or root in relative.parents
        for root in collection_roots
    ):
        return None
    if relative in collection_roots:
        return None

    if lexical_collection_path.is_dir():
        return None if relative_path in plan.selected_directories else True
    if _matches_any_pattern(lexical_collection_path, plan.file_patterns):
        return None if relative_path in plan.selected_files else True
    return None
