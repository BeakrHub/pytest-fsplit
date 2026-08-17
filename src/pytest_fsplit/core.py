"""Compatibility imports for pytest-fsplit's core APIs."""

from __future__ import annotations

from pytest_fsplit.collection import (
    absolute_collection_patterns,
    absolute_initial_paths,
    discover_candidate_files,
    lexical_absolute,
    logical_path,
    matches_any_pattern,
    normalise_test_paths,
    should_ignore_collection_path,
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
    MarkerExpressionAnalysis,
)
from pytest_fsplit.planning import (
    assign_files_to_shards,
    build_file_shard_plan,
    build_file_shard_plans,
)
from pytest_fsplit.reporting import format_file_shard_plan_summary

__all__ = [
    "DEFAULT_FILE_PATTERNS",
    "DEFAULT_SPLITTING_ALGORITHM",
    "DEFAULT_TEST_PATHS",
    "SPLITTING_ALGORITHMS",
    "FileShard",
    "FileShardPlan",
    "FileShardingError",
    "MarkerExpressionAnalysis",
    "absolute_collection_patterns",
    "absolute_initial_paths",
    "analyze_marker_expression",
    "assign_files_to_shards",
    "build_file_shard_plan",
    "build_file_shard_plans",
    "discover_candidate_files",
    "discover_zero_weight_files",
    "format_file_shard_plan_summary",
    "lexical_absolute",
    "load_file_durations",
    "logical_path",
    "matches_any_pattern",
    "normalise_test_paths",
    "should_ignore_collection_path",
]
