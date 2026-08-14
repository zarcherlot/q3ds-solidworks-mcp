"""Shared deterministic helpers for DimensionPlan validation."""

from __future__ import annotations

import math
import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from dimension_planner.planning_models import DimensionValidationIssue


Rect = tuple[float, float, float, float]


def issue(
    code: str,
    gate: str,
    message: str,
    json_pointer: str | None = None,
) -> DimensionValidationIssue:
    return DimensionValidationIssue(
        code=code,
        gate=gate,
        message=message[:2000],
        json_pointer=json_pointer,
    )


def stable_issues(
    values: Iterable[DimensionValidationIssue],
) -> tuple[DimensionValidationIssue, ...]:
    return tuple(
        sorted(
            values,
            key=lambda item: (item.json_pointer or "", item.code, item.message),
        )
    )


def pointer(*parts: object) -> str:
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "" if not escaped else "/" + "/".join(escaped)


def same_path(left: object, right: object) -> bool:
    return isinstance(left, str) and isinstance(right, str) and os.path.normcase(
        os.path.abspath(left)
    ) == os.path.normcase(os.path.abspath(right))


def finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def rect(value: object) -> Rect | None:
    if isinstance(value, Mapping):
        if set(value) != {"x_min_m", "y_min_m", "x_max_m", "y_max_m"}:
            return None
        value = (
            value["x_min_m"],
            value["y_min_m"],
            value["x_max_m"],
            value["y_max_m"],
        )
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) != 4 or not all(finite(item) for item in value):
        return None
    left, bottom, right, top = (float(item) for item in value)
    if left >= right or bottom >= top:
        return None
    return left, bottom, right, top


def point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) != 2 or not all(finite(item) for item in value):
        return None
    return float(value[0]), float(value[1])


def contains(bounds: Rect, value: tuple[float, float], tolerance: float = 1e-12) -> bool:
    return (
        bounds[0] - tolerance <= value[0] <= bounds[2] + tolerance
        and bounds[1] - tolerance <= value[1] <= bounds[3] + tolerance
    )


def rect_contains(outer: Rect, inner: Rect, tolerance: float = 1e-12) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def overlaps(left: Rect, right: Rect, tolerance: float = 1e-12) -> bool:
    return (
        min(left[2], right[2]) - max(left[0], right[0]) > tolerance
        and min(left[3], right[3]) - max(left[1], right[1]) > tolerance
    )


def by_id(rows: object, key: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {}
    return {
        str(row[key]): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get(key), str)
    }
