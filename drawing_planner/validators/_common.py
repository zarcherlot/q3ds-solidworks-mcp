"""Shared deterministic primitives for ViewPlan semantic validators."""

from __future__ import annotations

import math
import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from drawing_planner.planning_models import ValidationIssue


Gate = Literal["semantics", "coverage", "layout"]
Rect = tuple[float, float, float, float]


def validation_issue(
    code: str,
    gate: Gate,
    message: str,
    pointer: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        gate=gate,
        message=message,
        json_pointer=pointer,
    )


def stable_issues(issues: Iterable[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    return tuple(
        sorted(
            issues,
            key=lambda item: (
                item.json_pointer or "",
                item.code,
                item.message,
            ),
        )
    )


def pointer(*parts: object) -> str:
    if not parts:
        return ""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def rect(value: Any) -> Rect | None:
    if not isinstance(value, Mapping):
        return None
    values = tuple(value.get(key) for key in ("x_min_m", "y_min_m", "x_max_m", "y_max_m"))
    if not all(finite_number(item) for item in values):
        return None
    x_min, y_min, x_max, y_max = (float(item) for item in values)
    if not x_min < x_max or not y_min < y_max:
        return None
    return x_min, y_min, x_max, y_max


def rect_contains(outer: Rect, inner: Rect, tolerance: float = 1e-12) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def rects_overlap(left: Rect, right: Rect, tolerance: float = 1e-12) -> bool:
    return (
        min(left[2], right[2]) - max(left[0], right[0]) > tolerance
        and min(left[3], right[3]) - max(left[1], right[1]) > tolerance
    )


def point_in_rect(point: Sequence[Any], bounds: Rect, tolerance: float = 1e-12) -> bool:
    if len(point) != 2 or not all(finite_number(value) for value in point):
        return False
    x, y = (float(value) for value in point)
    return (
        bounds[0] - tolerance <= x <= bounds[2] + tolerance
        and bounds[1] - tolerance <= y <= bounds[3] + tolerance
    )


def same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def resolve_json_pointer(document: Any, value: str) -> tuple[bool, Any]:
    if not isinstance(value, str):
        return False, None
    if value == "":
        return True, document
    if not value.startswith("/"):
        return False, None
    current = document
    for encoded in value[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            if token == "0":
                index = 0
            elif not token.startswith("0") and token.isdigit():
                index = int(token)
            else:
                return False, None
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current
