from datetime import datetime, timezone

import models
from db import SessionLocal


TARGET_FROM_STATUS = 1
TARGET_TO_STATUS = 3
HISTORY_EVENT_TYPE = 6


def main() -> int:
    db = SessionLocal()
    updated = 0
    now = datetime.now(timezone.utc)
    try:
        rows = (
            db.query(models.EquipmentAsset)
            .filter(models.EquipmentAsset.asset_status == TARGET_FROM_STATUS)
            .filter(models.EquipmentAsset.client_id.is_(None))
            .filter(models.EquipmentAsset.customer_name == "")
            .all()
        )
        for asset in rows:
            asset.asset_status = TARGET_TO_STATUS
            asset.updated_at = now
            db.add(
                models.EquipmentAssetHistory(
                    asset_id=asset.id,
                    event_type=HISTORY_EVENT_TYPE,
                    summary="장비 기본 상태가 재고(New)로 보정되었습니다.",
                    detail="기본 정책 변경에 따라 자동 생성 자산의 초기 상태를 임대에서 재고(New)로 조정했습니다.",
                    created_by="status-migration",
                )
            )
            updated += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
