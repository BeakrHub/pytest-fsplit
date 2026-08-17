"""Pytest collection path discovery and pruning helpers."""

from __future__ import annotations

import errno
import fnmatch
import os
import stat
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from _pytest.pathlib import fnmatch_ex

from pytest_fsplit.errors import FileShardingError
from pytest_fsplit.models import DEFAULT_FILE_PATTERNS, DEFAULT_TEST_PATHS, FileShardPlan


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


def matches_any_pattern(path: Path, patterns: Iterable[str]) -> bool:
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


def logical_path(path: Path, root: Path) -> str:
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
        path_logical = logical_path(lexical_path, root)
        try:
            path_stat = lexical_path.stat()
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise FileShardingError(
                    "directory symlink cycle detected while discovering tests at "
                    f"{path_logical}"
                ) from exc
            raise FileShardingError(f"could not inspect test path {path_logical}: {exc}") from exc

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
                f"{path_logical} reaches its ancestor {ancestor_path}"
            )

        active_directories[physical_identity] = path_logical
        try:
            try:
                children = sorted(lexical_path.iterdir(), key=lambda child: child.name)
            except (FileNotFoundError, NotADirectoryError):
                return
            except OSError as exc:
                raise FileShardingError(
                    f"could not traverse test directory {path_logical}: {exc}"
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
            relative_path = PurePosixPath(logical_path(path, lexical_root))
            if not matches_any_pattern(path, patterns):
                continue

            candidate_path = relative_path.as_posix()
            existing_path = physical_candidates.get(physical_identity)
            if existing_path is not None and existing_path != candidate_path:
                first_path, second_path = sorted((existing_path, candidate_path))
                raise FileShardingError(
                    "test file is exposed through multiple logical paths that resolve to the "
                    f"same physical file: {first_path} and {second_path}"
                )
            physical_candidates[physical_identity] = candidate_path
            candidates.add(candidate_path)
    return tuple(sorted(candidates))


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
    if matches_any_pattern(lexical_collection_path, plan.file_patterns):
        return None if relative_path in plan.selected_files else True
    return None
