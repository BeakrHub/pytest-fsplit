"""Pytest integration for file-level sharding."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from _pytest.reports import TestReport

from pytest_fsplit.core import (
    DEFAULT_SPLITTING_ALGORITHM,
    SPLITTING_ALGORITHMS,
    FileShardPlan,
    absolute_collection_patterns,
    absolute_initial_paths,
    build_file_shard_plan,
    format_file_shard_plan_summary,
    lexical_absolute,
    should_ignore_collection_path,
)
from pytest_fsplit.durations import (
    load_node_durations,
    merge_node_durations,
    normalise_node_path,
    write_node_durations,
)
from pytest_fsplit.errors import FileShardingError

FSPLITS_OPTION = "--fsplits"
FGROUP_OPTION = "--fgroup"
STORE_DURATIONS_OPTION = "--fsplit-store-durations"
DURATIONS_PATH_OPTION = "--fsplit-durations-path"
CLEAN_DURATIONS_OPTION = "--fsplit-clean-durations"
FILE_PATTERN_OPTION = "--fsplit-file-pattern"
ALGORITHM_OPTION = "--fsplit-algorithm"

_PLAN_ATTRIBUTE = "_pytest_fsplit_plan"
_ATTEMPTED_FILES_ATTRIBUTE = "_pytest_fsplit_attempted_files"
_DESELECTED_FILES_ATTRIBUTE = "_pytest_fsplit_deselected_files"
_CACHED_DURATIONS_ATTRIBUTE = "_pytest_fsplit_cached_durations"
_XDIST_WORKER_PLAN_KEY = "pytest_fsplit_plan"
_XDIST_WORKER_PLAN_VERSION = 1
_SETUP_AND_TEARDOWN_DURATION_LIMIT_SECONDS = 60 * 10


def _serialize_plan(plan: FileShardPlan) -> str:
    return json.dumps(
        {
            "version": _XDIST_WORKER_PLAN_VERSION,
            "root": str(plan.root),
            "shard_count": plan.shard_count,
            "shard_index": plan.shard_index,
            "test_paths": list(plan.test_paths),
            "file_patterns": list(plan.file_patterns),
            "candidate_files": list(plan.candidate_files),
            "assigned_files": sorted(plan.assigned_files),
            "selected_files": sorted(plan.selected_files),
            "selected_directories": sorted(plan.selected_directories),
            "estimated_seconds": plan.estimated_seconds,
            "untimed_files": sorted(plan.untimed_files),
            "zero_weight_files": sorted(plan.zero_weight_files),
            "marker_expression_supported": plan.marker_expression_supported,
            "splitting_algorithm": plan.splitting_algorithm,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _deserialize_plan(serialized_plan: object) -> FileShardPlan:
    if not isinstance(serialized_plan, str):
        raise FileShardingError("xdist worker received a non-string file shard plan")
    try:
        payload = json.loads(serialized_plan)
        if not isinstance(payload, dict):
            raise TypeError("plan payload is not an object")
        if payload.get("version") != _XDIST_WORKER_PLAN_VERSION:
            raise ValueError(f"unsupported plan version: {payload.get('version')!r}")
        return FileShardPlan(
            root=lexical_absolute(Path(payload["root"])),
            shard_count=int(payload["shard_count"]),
            shard_index=int(payload["shard_index"]),
            test_paths=tuple(payload["test_paths"]),
            file_patterns=tuple(payload["file_patterns"]),
            candidate_files=tuple(payload["candidate_files"]),
            assigned_files=frozenset(payload["assigned_files"]),
            selected_files=frozenset(payload["selected_files"]),
            selected_directories=frozenset(payload["selected_directories"]),
            estimated_seconds=float(payload["estimated_seconds"]),
            untimed_files=frozenset(payload["untimed_files"]),
            zero_weight_files=frozenset(payload["zero_weight_files"]),
            marker_expression_supported=bool(payload["marker_expression_supported"]),
            splitting_algorithm=str(
                payload.get("splitting_algorithm", DEFAULT_SPLITTING_ALGORITHM)
            ),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FileShardingError(f"xdist worker received an invalid file shard plan: {exc}") from exc


def _get_option(config: pytest.Config, option: str, default: Any = None) -> Any:
    return config.getoption(option, default=default)


def _configured_duration_path(config: pytest.Config) -> Path:
    configured = Path(_get_option(config, "fsplit_durations_path"))
    if not configured.is_absolute():
        configured = lexical_absolute(config.invocation_params.dir) / configured
    return lexical_absolute(configured)


def _configured_test_paths(config: pytest.Config) -> tuple[str, ...]:
    return (
        tuple(str(argument) for argument in config.args)
        or tuple(config.getini("testpaths"))
        or (".",)
    )


def _configured_file_patterns(config: pytest.Config) -> tuple[str, ...]:
    configured_patterns = _get_option(config, "fsplit_file_patterns", default=None)
    return tuple(configured_patterns or config.getini("python_files"))


def _build_plan(config: pytest.Config, shard_count: int, shard_index: int) -> FileShardPlan:
    lexical_root = lexical_absolute(config.rootpath)
    invocation_directory = lexical_absolute(config.invocation_params.dir)
    ignore_paths = absolute_collection_patterns(
        invocation_directory,
        _get_option(config, "ignore", default=()) or (),
    )
    ignore_globs = absolute_collection_patterns(
        invocation_directory,
        _get_option(config, "ignore_glob", default=()) or (),
    )
    initial_paths = absolute_initial_paths(invocation_directory, config.args)
    return build_file_shard_plan(
        lexical_root,
        shard_count,
        shard_index,
        duration_path=_configured_duration_path(config),
        test_paths=_configured_test_paths(config),
        file_patterns=_configured_file_patterns(config),
        marker_expression=_get_option(config, "markexpr", default=""),
        ignore_paths=ignore_paths,
        ignore_globs=ignore_globs,
        norecurse_patterns=config.getini("norecursedirs"),
        initial_paths=(str(path) for path in initial_paths),
        splitting_algorithm=_get_option(
            config,
            "fsplit_algorithm",
            default=DEFAULT_SPLITTING_ALGORITHM,
        ),
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup(
        "pytest-fsplit: split pytest collection into file-level shards"
    )
    group.addoption(
        FSPLITS_OPTION,
        dest="fsplits",
        action="store",
        type=int,
        default=None,
        help="split test files into this many deterministic file shards",
    )
    group.addoption(
        FGROUP_OPTION,
        dest="fgroup",
        action="store",
        type=int,
        default=None,
        help="run this one-based deterministic file shard",
    )
    group.addoption(
        DURATIONS_PATH_OPTION,
        dest="fsplit_durations_path",
        default=os.path.join(os.getcwd(), ".test_durations"),
        help="path to the JSON duration file read or written by pytest-fsplit",
    )
    group.addoption(
        STORE_DURATIONS_OPTION,
        dest="fsplit_store_durations",
        action="store_true",
        help="store pytest-split-compatible node durations in --fsplit-durations-path",
    )
    group.addoption(
        CLEAN_DURATIONS_OPTION,
        dest="fsplit_clean_durations",
        action="store_true",
        help="when storing durations, remove entries for tests that did not run",
    )
    group.addoption(
        FILE_PATTERN_OPTION,
        dest="fsplit_file_patterns",
        action="append",
        default=None,
        metavar="PATTERN",
        help=(
            "file path pattern to shard; may be supplied multiple times. "
            "Defaults to pytest's python_files patterns."
        ),
    )
    group.addoption(
        ALGORITHM_OPTION,
        dest="fsplit_algorithm",
        default=DEFAULT_SPLITTING_ALGORITHM,
        choices=SPLITTING_ALGORITHMS,
        help=(
            "file splitting algorithm. "
            "least_duration greedily balances files by historical duration; "
            "duration_based_chunks preserves contiguous lexical file order."
        ),
    )


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> int | pytest.ExitCode | None:
    shard_count = _get_option(config, "fsplits")
    shard_index = _get_option(config, "fgroup")
    store_durations = bool(_get_option(config, "fsplit_store_durations", default=False))
    clean_durations = bool(_get_option(config, "fsplit_clean_durations", default=False))
    if clean_durations and not store_durations:
        raise pytest.UsageError(
            f"{CLEAN_DURATIONS_OPTION} requires {STORE_DURATIONS_OPTION}"
        )
    if shard_count is None and shard_index is None:
        return None
    if shard_count is None:
        raise pytest.UsageError(f"{FGROUP_OPTION} requires {FSPLITS_OPTION}")
    if shard_index is None:
        raise pytest.UsageError(f"{FSPLITS_OPTION} requires {FGROUP_OPTION}")
    if shard_count < 1:
        raise pytest.UsageError(f"{FSPLITS_OPTION} must be >= 1")
    if shard_index < 1 or shard_index > shard_count:
        raise pytest.UsageError(f"{FGROUP_OPTION} must be between 1 and {shard_count}")
    return None


def pytest_configure(config: pytest.Config) -> None:
    shard_count = _get_option(config, "fsplits")
    shard_index = _get_option(config, "fgroup")
    store_durations = bool(_get_option(config, "fsplit_store_durations", default=False))
    clean_durations = bool(_get_option(config, "fsplit_clean_durations", False))

    if store_durations:
        try:
            cached_durations = (
                {}
                if clean_durations
                else load_node_durations(
                    _configured_duration_path(config),
                    missing_ok=True,
                    empty_ok=True,
                )
            )
        except FileShardingError as exc:
            duration_path = _configured_duration_path(config)
            raise pytest.UsageError(
                f"pytest-fsplit could not read existing duration file {duration_path}: {exc}"
            ) from exc
        setattr(config, _CACHED_DURATIONS_ATTRIBUTE, cached_durations)

    if shard_count is None and shard_index is None:
        return
    if store_durations:
        raise pytest.UsageError(
            f"{STORE_DURATIONS_OPTION} cannot be combined with {FSPLITS_OPTION}: "
            "it would record only this shard's timings"
        )

    pytest_split_selection_options = tuple(
        option_name
        for option_name, option_destination in (("--splits", "splits"), ("--group", "group"))
        if _get_option(config, option_destination, default=None) is not None
    )
    if pytest_split_selection_options:
        raise pytest.UsageError(
            f"{FSPLITS_OPTION} cannot be combined with pytest-split selection options: "
            f"{', '.join(pytest_split_selection_options)}"
        )

    if bool(_get_option(config, "store_durations", default=False)):
        raise pytest.UsageError(
            f"{FSPLITS_OPTION} cannot be combined with pytest-split duration storage: "
            "--store-durations would record only this shard's timings"
        )

    worker_input = getattr(config, "workerinput", None)
    if isinstance(worker_input, dict) and _XDIST_WORKER_PLAN_KEY in worker_input:
        try:
            plan = _deserialize_plan(worker_input[_XDIST_WORKER_PLAN_KEY])
        except FileShardingError as exc:
            raise pytest.UsageError(f"pytest-fsplit failed: {exc}") from exc
        if plan.shard_count != shard_count or plan.shard_index != shard_index:
            raise pytest.UsageError(
                "pytest-fsplit failed: xdist worker plan does not match shard options"
            )
        setattr(config, _PLAN_ATTRIBUTE, plan)
        return

    try:
        plan = _build_plan(config, shard_count, shard_index)
    except FileShardingError as exc:
        raise pytest.UsageError(f"pytest-fsplit failed: {exc}") from exc
    setattr(config, _PLAN_ATTRIBUTE, plan)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: object) -> None:
    """Pass the controller-built plan to xdist workers."""

    config = getattr(node, "config", None)
    worker_input = getattr(node, "workerinput", None)
    plan: FileShardPlan | None = getattr(config, _PLAN_ATTRIBUTE, None)
    if plan is not None and isinstance(worker_input, dict):
        worker_input[_XDIST_WORKER_PLAN_KEY] = _serialize_plan(plan)


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    plan: FileShardPlan | None = getattr(config, _PLAN_ATTRIBUTE, None)
    if plan is None:
        return None
    return should_ignore_collection_path(collection_path, plan)


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> None:
    """Record selected files that survived pruning and reached collection."""

    plan: FileShardPlan | None = getattr(parent.config, _PLAN_ATTRIBUTE, None)
    if plan is None:
        return None
    try:
        relative_path = lexical_absolute(file_path).relative_to(plan.root).as_posix()
    except ValueError:
        return None
    if relative_path not in plan.selected_files:
        return None
    attempted_files = getattr(parent.config, _ATTEMPTED_FILES_ATTRIBUTE, frozenset())
    setattr(
        parent.config,
        _ATTEMPTED_FILES_ATTRIBUTE,
        frozenset(attempted_files).union({relative_path}),
    )
    return None


def _selected_files_requested_by_session(
    session: pytest.Session,
    plan: FileShardPlan,
) -> frozenset[str]:
    initial_paths = getattr(session, "_initialpaths", None)
    if initial_paths is None:
        return plan.selected_files

    lexical_initial_paths = tuple(lexical_absolute(Path(path)) for path in initial_paths)
    return frozenset(
        file_path
        for file_path in plan.selected_files
        if any(
            initial_path == plan.root / file_path
            or initial_path in (plan.root / file_path).parents
            for initial_path in lexical_initial_paths
        )
    )


def _explicit_files_requested_by_session(
    session: pytest.Session,
    plan: FileShardPlan,
    file_paths: set[str],
) -> frozenset[str]:
    initial_paths = getattr(session, "_initialpaths", None)
    if initial_paths is None:
        return frozenset()

    lexical_initial_paths = {lexical_absolute(Path(path)) for path in initial_paths}
    return frozenset(
        file_path
        for file_path in file_paths
        if plan.root / file_path in lexical_initial_paths
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    """Fail closed unless requested shard files reached collection or produced items."""

    if sys.exc_info()[0] is not None:
        return
    if getattr(session, "testsfailed", 0) > 0:
        return

    plan: FileShardPlan | None = getattr(session.config, _PLAN_ATTRIBUTE, None)
    if plan is None:
        return

    collected_files = {normalise_node_path(item.nodeid) for item in session.items}
    deselected_files = set(getattr(session.config, _DESELECTED_FILES_ATTRIBUTE, frozenset()))
    attempted_files = set(getattr(session.config, _ATTEMPTED_FILES_ATTRIBUTE, frozenset()))
    item_files = collected_files | deselected_files
    observed_files = item_files | attempted_files
    requested_files = _selected_files_requested_by_session(session, plan)
    missing_files = sorted(requested_files - observed_files)
    explicitly_requested_files = _explicit_files_requested_by_session(session, plan, item_files)
    unexpected_files = sorted(item_files - plan.selected_files - explicitly_requested_files)

    problems: list[str] = []
    if missing_files:
        displayed_files = ", ".join(missing_files[:5])
        suffix = "" if len(missing_files) <= 5 else f" (+{len(missing_files) - 5} more)"
        problems.append(
            "selected files were never visited or represented by collected or deselected tests: "
            f"{displayed_files}{suffix}"
        )
    if unexpected_files:
        displayed_files = ", ".join(unexpected_files[:5])
        suffix = "" if len(unexpected_files) <= 5 else f" (+{len(unexpected_files) - 5} more)"
        problems.append(
            "collected or deselected tests outside the selected shard: "
            f"{displayed_files}{suffix}"
        )
    if problems:
        raise pytest.UsageError(f"pytest-fsplit collection mismatch; {'; '.join(problems)}")


def pytest_deselected(items: list[pytest.Item]) -> None:
    """Record files containing items that pytest deselected at runtime."""

    if not items:
        return
    config = items[0].config
    plan: FileShardPlan | None = getattr(config, _PLAN_ATTRIBUTE, None)
    if plan is None:
        return

    deselected_files = {normalise_node_path(item.nodeid) for item in items}
    previously_deselected = getattr(config, _DESELECTED_FILES_ATTRIBUTE, frozenset())
    setattr(
        config,
        _DESELECTED_FILES_ATTRIBUTE,
        frozenset(previously_deselected).union(deselected_files),
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    plan: FileShardPlan | None = getattr(session.config, _PLAN_ATTRIBUTE, None)
    shard_was_planned_empty = plan is not None and not plan.assigned_files
    all_assigned_files_were_pruned = (
        plan is not None
        and bool(plan.assigned_files)
        and not plan.selected_files
        and plan.assigned_files == plan.zero_weight_files
    )
    if (
        plan is not None
        and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED
        and (shard_was_planned_empty or all_assigned_files_were_pruned)
    ):
        session.exitstatus = pytest.ExitCode.OK

    if not _get_option(session.config, "fsplit_store_durations", default=False):
        return

    terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    durations: dict[str, float] = {}
    if terminal_reporter is not None:
        for reports in terminal_reporter.stats.values():
            for report in reports:
                if not isinstance(report, TestReport):
                    continue
                if report.duration < 0:
                    continue
                if (
                    report.when in {"setup", "teardown"}
                    and report.duration > _SETUP_AND_TEARDOWN_DURATION_LIMIT_SECONDS
                ):
                    continue
                durations[report.nodeid] = durations.get(report.nodeid, 0.0) + report.duration

    duration_path = _configured_duration_path(session.config)
    write_node_durations(
        duration_path,
        merge_node_durations(
            getattr(session.config, _CACHED_DURATIONS_ATTRIBUTE, {}),
            durations,
            clean=bool(_get_option(session.config, "fsplit_clean_durations", False)),
        ),
    )


def pytest_report_header(config: pytest.Config) -> str | list[str] | None:
    plan: FileShardPlan | None = getattr(config, _PLAN_ATTRIBUTE, None)
    if plan is None or os.getenv("PYTEST_XDIST_WORKER"):
        return None
    summary = format_file_shard_plan_summary(plan)
    if plan.marker_expression_supported:
        return summary
    return [
        summary,
        "pytest-fsplit warning: marker expression could not be analyzed safely; "
        "static marker pruning is disabled and untimed files use the median fallback",
    ]
