"""Static pytest marker-expression analysis."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from pytest_fsplit.models import MarkerExpressionAnalysis


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
