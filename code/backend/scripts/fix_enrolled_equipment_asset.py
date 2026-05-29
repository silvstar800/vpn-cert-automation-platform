from __future__ import annotations

import sys
from datetime import datetime, timezone

from db import SessionLocal
import models


def main() -> int:
    serial = "S180A0232B9B1E5"
    expected_model = "SG 105"
    with SessionLocal() as db:  # type: Session
        asset = db.query(models.EquipmentAsset).filter_by(serial_number=serial).first()
        if asset is None:
            print(f"asset not found for serial {serial}", file=sys.stderr)
            return 1

        changed = False
        notes: list[str] = []

        if asset.device_model != expected_model:
            notes.append(f"model {asset.device_model or '-'} -> {expected_model}")
            asset.device_model = expected_model
            changed = True

        if asset.asset_status != 1:
            notes.append(f"status {asset.asset_status} -> 1")
            asset.asset_status = 1
            changed = True

        if asset.sale_type != 1:
            notes.append(f"sale_type {asset.sale_type} -> 1")
            asset.sale_type = 1
            changed = True

        if changed:
            asset.updated_at = datetime.now(tz=timezone.utc)
            db.add(
                models.EquipmentAssetHistory(
                    asset_id=asset.id,
                    event_type=4,
                    summary="Equipment asset was manually normalized.",
                    detail=", ".join(notes),
                    created_by="system-fix",
                )
            )
            db.commit()
            print(f"fixed asset {serial}")
        else:
            print(f"asset {serial} already normalized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
