"""Pre-generate 3D models for chosen items so demos never wait or spend cap.

Usage: python -m scripts.pregen_models <tracked_product_id> [...]
Runs synchronously (no Celery) — intended for one-off local/prod seeding.
Requires MESHY_API_KEY; each item that generates spends one monthly credit.
"""

from __future__ import annotations

import sys
import uuid

from core.db import SessionLocal
from core.models import ProductModel3D, TrackedProduct
from workers.model3d import run_generation


def main(ids: list[str]) -> None:
    if not ids:
        print(__doc__)
        raise SystemExit(1)
    db = SessionLocal()
    try:
        for raw in ids:
            tp = db.get(TrackedProduct, uuid.UUID(raw))
            if tp is None:
                print(f"skip {raw}: not found")
                continue
            offer = next(
                (o for o in tp.offers if o.image_url and o.is_available), None
            )
            if offer is None:
                print(f"skip {raw}: no offer image")
                continue
            row = (
                db.query(ProductModel3D)
                .filter_by(tracked_product_id=tp.id)
                .one_or_none()
            )
            if row is not None and row.status == "ready":
                print(f"skip {tp.title}: already ready")
                continue
            if row is None:
                row = ProductModel3D(
                    tracked_product_id=tp.id, source_image_url=offer.image_url
                )
                db.add(row)
                db.flush()
            print(f"generating {tp.title} …")
            run_generation(db, row)
            db.commit()
            print(f"  ready: {row.glb_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
