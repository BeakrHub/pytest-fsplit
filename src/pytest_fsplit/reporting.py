"""Human-readable pytest-fsplit reporting helpers."""

from __future__ import annotations

from pytest_fsplit.models import FileShardPlan


def format_file_shard_plan_summary(plan: FileShardPlan, *, prefix: str = "pytest-fsplit") -> str:
    return (
        f"{prefix} {plan.shard_index}/{plan.shard_count}: "
        f"{plan.splitting_algorithm}, "
        f"{len(plan.assigned_files)}/{len(plan.candidate_files)} files assigned, "
        f"{len(plan.selected_files)} collected, "
        f"{plan.estimated_seconds:.2f}s estimated, "
        f"{len(plan.untimed_files)} untimed, "
        f"{len(plan.zero_weight_files)} marker-excluded before collection"
    )
