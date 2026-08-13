"""Duration-file parsing, aggregation, and writing."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path

from pytest_fsplit.errors import FileShardingError


def normalise_node_path(node_id: str) -> str:
    return node_id.split("::", 1)[0].replace("\\", "/")


def _normalise_duration_payload(raw_durations: object, duration_path: Path) -> Mapping[str, object]:
    """Accept pytest-split's current object format and older list-of-pairs format."""

    if isinstance(raw_durations, list):
        try:
            raw_durations = dict(raw_durations)
        except (TypeError, ValueError) as exc:
            raise FileShardingError(
                f"duration file must contain duration pairs or a JSON object: {duration_path}"
            ) from exc

    if not isinstance(raw_durations, dict):
        raise FileShardingError(f"duration file must contain a JSON object: {duration_path}")
    return raw_durations


def load_node_durations(
    duration_path: Path,
    *,
    missing_ok: bool = False,
    empty_ok: bool = False,
) -> dict[str, float]:
    """Load pytest-split-compatible per-node timings."""

    try:
        raw_durations = json.loads(duration_path.read_text())
    except FileNotFoundError as exc:
        if missing_ok:
            return {}
        raise FileShardingError(f"duration file does not exist: {duration_path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FileShardingError(f"could not read valid JSON from {duration_path}: {exc}") from exc

    raw_durations = _normalise_duration_payload(raw_durations, duration_path)
    if not raw_durations and not empty_ok:
        raise FileShardingError(
            f"duration file must contain a non-empty JSON object: {duration_path}"
        )

    node_durations: dict[str, float] = {}
    for node_id, duration in raw_durations.items():
        if not isinstance(node_id, str) or not node_id:
            raise FileShardingError(f"duration file contains an invalid test node ID: {node_id!r}")
        if isinstance(duration, bool) or not isinstance(duration, int | float):
            raise FileShardingError(f"duration for {node_id!r} must be a number")

        numeric_duration = float(duration)
        if not math.isfinite(numeric_duration) or numeric_duration < 0:
            raise FileShardingError(
                f"duration for {node_id!r} must be a finite, non-negative number"
            )
        node_durations[node_id] = numeric_duration

    return node_durations


def load_file_durations(duration_path: Path) -> dict[str, float]:
    """Load node timings and aggregate them by collected file."""

    file_durations: dict[str, float] = {}
    for node_id, duration in load_node_durations(duration_path).items():
        file_path = normalise_node_path(node_id)
        if not file_path:
            raise FileShardingError(f"duration file contains an invalid test node ID: {node_id!r}")
        file_durations[file_path] = file_durations.get(file_path, 0.0) + duration
    return file_durations


def merge_node_durations(
    existing_durations: Mapping[str, float],
    observed_durations: Mapping[str, float],
    *,
    clean: bool = False,
) -> dict[str, float]:
    """Merge newly observed timings into an existing duration map."""

    if clean:
        return dict(observed_durations)
    merged = dict(existing_durations)
    merged.update(observed_durations)
    return merged


def write_node_durations(duration_path: Path, durations: Mapping[str, float]) -> None:
    duration_path.write_text(json.dumps(dict(durations), sort_keys=True, indent=4))

