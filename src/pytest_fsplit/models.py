"""Shared models and constants for pytest-fsplit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_TEST_PATHS = ("tests",)
DEFAULT_FILE_PATTERNS = ("test_*.py",)
DEFAULT_SPLITTING_ALGORITHM = "least_duration"
SPLITTING_ALGORITHMS = ("least_duration", "duration_based_chunks")


@dataclass(frozen=True)
class FileShard:
    """One shard produced by a deterministic file assignment algorithm."""

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
