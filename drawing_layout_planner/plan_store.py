"""No-overwrite atomic publication for DrawingLayoutPlan 1.0 files."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .planning_models import (
    DrawingLayoutPlan,
    PublishedDrawingLayoutPlan,
    drawing_layout_plan_from_mapping,
)


class PlanStore:
    """Publish one immutable, validated layout plan into an existing directory."""

    def publish(
        self, plan: DrawingLayoutPlan | Mapping[str, Any], directory: str
    ) -> PublishedDrawingLayoutPlan:
        normalized = (
            plan
            if isinstance(plan, DrawingLayoutPlan)
            else drawing_layout_plan_from_mapping(plan)
        )
        root = Path(directory).resolve()
        if not root.is_dir():
            raise ValueError(f"publication directory does not exist: {root}")
        validation_root = (Path(__file__).resolve().parents[1] / "validation").resolve()
        if root == validation_root or validation_root in root.parents:
            raise ValueError("publication directory must not be validation or its descendant")

        target = root / "drawing_layout_plan.json"
        if target.exists():
            raise FileExistsError(
                f"refusing to overwrite frozen DrawingLayoutPlan: {target}"
            )
        payload = (
            json.dumps(
                normalized.execution_dict(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".drawing_layout_plan.", suffix=".tmp", dir=str(root)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # A same-directory hard link is atomic and fails if another publisher won.
            os.link(temporary_name, target)
            os.unlink(temporary_name)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return PublishedDrawingLayoutPlan(
            plan_id=normalized.plan_id,
            path=str(target),
            sha256=hashlib.sha256(payload).hexdigest(),
        )


DrawingLayoutPlanStore = PlanStore
