"""3D model generation pipeline (Phase 10): create → poll → download → ready.

Kept out of workers/tasks.py per the repo split: tasks.py holds thin Celery
wrappers, pipeline modules hold the logic. `run_generation` raises on
transient/provider errors so the Celery wrapper's retry/dead-letter machinery
(same shape as fetch_offer) owns the failure policy.
"""

from __future__ import annotations

import pathlib
import time

from sqlalchemy.orm import Session

from core import meshy
from core.logging import get_logger
from core.models import ProductModel3D
from core.settings import get_settings

log = get_logger(__name__)

POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 300


def _storage_dir() -> pathlib.Path:
    return pathlib.Path(get_settings().model3d_storage_dir)


class GenerationFailed(Exception):
    """Provider says this generation is definitively failed (no retry value)."""


def run_generation(db: Session, row: ProductModel3D) -> None:
    """Drive one generation to completion. Mutates row; caller commits.

    Raises GenerationFailed on a definitive provider failure and any other
    exception on transient trouble — the Celery wrapper decides retry policy.
    """
    row.status = "generating"
    db.flush()

    task_id = row.provider_task_id
    if not task_id:
        task_id = meshy.create_image_to_3d_task(row.source_image_url)
        row.provider_task_id = task_id
        db.flush()

    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while True:
        task = meshy.get_task(task_id)
        if task.status == "SUCCEEDED":
            break
        if task.status in ("FAILED", "CANCELED"):
            row.status = "failed"
            row.error = task.error or "generation failed"
            db.flush()
            raise GenerationFailed(row.error)
        if time.monotonic() > deadline:
            raise TimeoutError(f"meshy task {task_id} still {task.status}")
        time.sleep(POLL_INTERVAL_SECONDS)

    pid = str(row.tracked_product_id)
    for fmt in ("glb", "usdz"):
        url = task.model_urls.get(fmt)
        if not url:
            raise meshy.MeshyError(f"no {fmt} url in result")
        meshy.download_file(url, _storage_dir() / f"{pid}.{fmt}")

    row.glb_path = f"{pid}.glb"
    row.usdz_path = f"{pid}.usdz"
    row.status = "ready"
    row.error = None
    db.flush()
    log.info("model3d.ready", tracked_product_id=pid)
