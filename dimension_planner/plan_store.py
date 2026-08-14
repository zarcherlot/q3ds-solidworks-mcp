"""No-overwrite atomic publication for schema-valid DimensionPlan 1.0 files."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .planning_models import DimensionPlan, PublishedDimensionPlan, dimension_plan_from_mapping


class PlanStore:
    def publish(
        self, plan: DimensionPlan | Mapping[str, Any], directory: str
    ) -> PublishedDimensionPlan:
        normalized = (
            plan if isinstance(plan, DimensionPlan) else dimension_plan_from_mapping(plan)
        )
        root = Path(directory).resolve()
        if not root.is_dir():
            raise ValueError(f"publication directory does not exist: {root}")
        validation_root = (Path(__file__).resolve().parents[1] / "validation").resolve()
        if root == validation_root or validation_root in root.parents:
            raise ValueError("publication directory must not be validation or its descendant")
        target = root / "dimension_plan.json"
        if target.exists():
            raise FileExistsError(f"refusing to overwrite frozen DimensionPlan: {target}")

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
            prefix=".dimension_plan.", suffix=".tmp", dir=str(root)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary_name, target)
            os.unlink(temporary_name)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return PublishedDimensionPlan(
            plan_id=normalized.plan_id,
            path=str(target),
            sha256=hashlib.sha256(payload).hexdigest(),
        )


DimensionPlanStore = PlanStore
