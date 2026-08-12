"""Atomic publication of validated ViewPlan artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from drawing_planner.planning_models import PublishedPlan


class PlanStore:
    def publish(self, plan: Mapping[str, Any], directory: str) -> PublishedPlan:
        root = Path(directory).resolve()
        if not root.is_dir():
            raise ValueError(f"publication directory does not exist: {root}")
        target = root / "view_plan.json"
        if target.exists():
            raise FileExistsError(f"refusing to overwrite frozen plan: {target}")
        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            raise ValueError("validated plan must contain plan_id")

        payload = (
            json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".view_plan.", suffix=".tmp", dir=str(root)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # Linking a fully flushed same-directory temporary file publishes atomically and,
            # unlike replace(), fails if another writer won the target-name race.
            os.link(temporary_name, target)
            os.unlink(temporary_name)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return PublishedPlan(
            plan_id=plan_id,
            path=str(target),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
