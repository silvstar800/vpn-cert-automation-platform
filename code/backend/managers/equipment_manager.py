"""Equipment asset and inventory management helpers."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

import models

ASSET_SALE_TYPE_LEASE = 1
ASSET_SALE_TYPE_SALE = 2
ASSET_STATUS_LEASED = 1
ASSET_STATUS_UNRETURNED = 2
ASSET_STATUS_STOCK_NEW = 3
ASSET_STATUS_STOCK_OLD = 4
ASSET_STATUS_SOLD = 5
ASSET_STATUS_DISPOSED = 6
ASSET_STATUS_RMA = 7
ASSET_CUSTOMER_SYNC_PENDING = 0
ASSET_CUSTOMER_SYNC_SYNCED = 1
ASSET_HISTORY_REGISTER = 1
ASSET_HISTORY_ENROLL = 2
ASSET_HISTORY_APC = 3
ASSET_HISTORY_CUSTOMER_SYNC = 4
ASSET_HISTORY_IMPORT = 5
ASSET_HISTORY_MANUAL = 6

ASSET_SALE_TYPE_LABELS = {
    ASSET_SALE_TYPE_LEASE: "임대",
    ASSET_SALE_TYPE_SALE: "판매",
}

ASSET_STATUS_LABELS = {
    ASSET_STATUS_LEASED: "임대",
    ASSET_STATUS_UNRETURNED: "미회수",
    ASSET_STATUS_STOCK_NEW: "재고(New)",
    ASSET_STATUS_STOCK_OLD: "재고(Old)",
    ASSET_STATUS_SOLD: "판매",
    ASSET_STATUS_DISPOSED: "폐기",
    ASSET_STATUS_RMA: "RMA대상",
}

ASSET_CUSTOMER_SYNC_LABELS = {
    ASSET_CUSTOMER_SYNC_PENDING: "외부 연동 대기",
    ASSET_CUSTOMER_SYNC_SYNCED: "외부 연동 완료",
}

PLACEHOLDER_CUSTOMER_NAMES = {
    "",
    "-",
    "외부 연동 대기",
    "등록 대기",
    "pending",
    "unknown",
    "n/a",
}

ASSET_EVENT_TYPE_LABELS = {
    ASSET_HISTORY_REGISTER: "register",
    ASSET_HISTORY_ENROLL: "enroll",
    ASSET_HISTORY_APC: "apc",
    ASSET_HISTORY_CUSTOMER_SYNC: "customer-sync",
    ASSET_HISTORY_IMPORT: "import",
    ASSET_HISTORY_MANUAL: "manual",
}

ASSET_STATUS_ALIASES = {
    "leased": ASSET_STATUS_LEASED,
    "lease": ASSET_STATUS_LEASED,
    "임대": ASSET_STATUS_LEASED,
    "unreturned": ASSET_STATUS_UNRETURNED,
    "미회수": ASSET_STATUS_UNRETURNED,
    "stock": ASSET_STATUS_STOCK_NEW,
    "stock_new": ASSET_STATUS_STOCK_NEW,
    "new": ASSET_STATUS_STOCK_NEW,
    "재고": ASSET_STATUS_STOCK_NEW,
    "stock_old": ASSET_STATUS_STOCK_OLD,
    "old": ASSET_STATUS_STOCK_OLD,
    "sold": ASSET_STATUS_SOLD,
    "판매": ASSET_STATUS_SOLD,
    "disposed": ASSET_STATUS_DISPOSED,
    "dispose": ASSET_STATUS_DISPOSED,
    "폐기": ASSET_STATUS_DISPOSED,
    "rma": ASSET_STATUS_RMA,
}

SALE_TYPE_ALIASES = {
    "lease": ASSET_SALE_TYPE_LEASE,
    "leased": ASSET_SALE_TYPE_LEASE,
    "임대": ASSET_SALE_TYPE_LEASE,
    "sale": ASSET_SALE_TYPE_SALE,
    "sold": ASSET_SALE_TYPE_SALE,
    "판매": ASSET_SALE_TYPE_SALE,
}

DISPLAY_TIMEZONE = ZoneInfo("Asia/Seoul")


class EquipmentAssetManager:
    """Handles equipment asset persistence and serialization."""

    def append_history(
        self,
        db: Session,
        *,
        asset_id: int,
        event_type: int,
        summary: str,
        detail: str = "",
        created_by: str = "system",
    ) -> None:
        """Persist an equipment asset history entry."""
        db.add(
            models.EquipmentAssetHistory(
                asset_id=asset_id,
                event_type=event_type,
                summary=(summary or "").strip(),
                detail=(detail or "").strip(),
                created_by=(created_by or "system").strip() or "system",
            )
        )

    def format_license_flags(self, flags: int | None) -> list[str]:
        """Convert bitmask license flags into UI labels."""
        value = int(flags or 0)
        labels = ["기본"]
        if value & 1:
            labels.append("Enhanced")
        if value & 2:
            labels.append("NP")
        if value & 4:
            labels.append("WP")
        return labels

    def _to_kst_iso(self, value: datetime | None) -> str | None:
        """Convert datetimes to ISO strings in Asia/Seoul for display."""
        if not value:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(DISPLAY_TIMEZONE).isoformat(timespec="seconds")

    def is_meaningful_customer_name(self, value: str | None) -> bool:
        """Return True only for real synced customer names, not placeholders."""
        normalized = " ".join((value or "").strip().split())
        return normalized.lower() not in PLACEHOLDER_CUSTOMER_NAMES

    def serialize_asset(
        self,
        asset: models.EquipmentAsset,
        history_rows: list[models.EquipmentAssetHistory] | None = None,
        *,
        hostname: str = "",
    ) -> dict:
        """Serialize an equipment asset and optional history rows for the frontend."""
        effective_hostname = (hostname or "").strip()

        def decorate_history_detail(detail: str | None) -> str:
            text = (detail or "").strip()
            if not effective_hostname:
                return text
            if not text:
                return f"호스트명 {effective_hostname}"
            if "호스트명" in text or effective_hostname in text:
                return text
            return f"호스트명 {effective_hostname} | {text}"

        raw_customer_name = " ".join((asset.customer_name or "").strip().split())
        has_real_customer_name = self.is_meaningful_customer_name(raw_customer_name)
        sync_status = int(asset.customer_sync_status or 0)
        effective_sync_status = (
            ASSET_CUSTOMER_SYNC_SYNCED if (sync_status == ASSET_CUSTOMER_SYNC_SYNCED and has_real_customer_name) else ASSET_CUSTOMER_SYNC_PENDING
        )
        asset_status_label = (
            "등록 대기"
            if effective_sync_status != ASSET_CUSTOMER_SYNC_SYNCED
            else ASSET_STATUS_LABELS.get(int(asset.asset_status or 0), "-")
        )
        return {
            "id": asset.id,
            "serialNumber": asset.serial_number,
            "hostname": effective_hostname,
            "customerName": raw_customer_name or "외부 연동 대기",
            "customerSyncStatus": effective_sync_status,
            "customerSyncLabel": ASSET_CUSTOMER_SYNC_LABELS.get(
                effective_sync_status,
                "외부 연동 대기",
            ),
            "deviceModel": asset.device_model or "-",
            "licenseFlags": int(asset.license_flags or 0),
            "licenseLabels": self.format_license_flags(asset.license_flags),
            "licenseStartDate": asset.license_start_date.isoformat() if asset.license_start_date else None,
            "licenseEndDate": asset.license_end_date.isoformat() if asset.license_end_date else None,
            "saleType": int(asset.sale_type or 0),
            "saleTypeLabel": ASSET_SALE_TYPE_LABELS.get(int(asset.sale_type or 0), "-"),
            "assetStatus": int(asset.asset_status or 0),
            "assetStatusLabel": asset_status_label,
            "clientId": asset.client_id,
            "updatedAt": self._to_kst_iso(asset.updated_at),
            "history": [
                {
                    "id": row.id,
                    "eventType": int(row.event_type or 0),
                    "eventLabel": ASSET_EVENT_TYPE_LABELS.get(int(row.event_type or 0), "history"),
                    "summary": row.summary or "",
                    "detail": decorate_history_detail(row.detail),
                    "createdBy": row.created_by or "system",
                    "createdAt": self._to_kst_iso(row.created_at),
                    "hostname": effective_hostname,
                }
                for row in (history_rows or [])
            ],
        }

    def normalize_asset_status(self, value: int | str | None) -> int | None:
        """Normalize external asset status text/number into an internal code."""
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        text_value = str(value).strip().lower()
        if text_value.isdigit():
            return int(text_value)
        return ASSET_STATUS_ALIASES.get(text_value)

    def normalize_sale_type(self, value: int | str | None) -> int | None:
        """Normalize external sale type text/number into an internal code."""
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        text_value = str(value).strip().lower()
        if text_value.isdigit():
            return int(text_value)
        return SALE_TYPE_ALIASES.get(text_value)

    def build_sync_payload(
        self,
        asset: models.EquipmentAsset,
        *,
        client: models.Client | None = None,
        assigned_ip: str = "",
        license_info: dict | None = None,
        trigger: str = "auto",
    ) -> dict:
        """Build a flexible JSON payload for external inventory sync."""
        return {
            "serialNumber": asset.serial_number,
            "hostname": (client.hostname if client else "") or "",
            "assignedIp": assigned_ip or "",
            "vpnType": (client.vpn_type if client else "") or "",
            "deviceModel": asset.device_model or "",
            "customerName": asset.customer_name or "",
            "customerSyncStatus": int(asset.customer_sync_status or 0),
            "assetStatus": int(asset.asset_status or 0),
            "saleType": int(asset.sale_type or 0),
            "licenseInfo": license_info,
            "trigger": trigger,
        }

    def apply_sync_payload(
        self,
        db: Session,
        payload: dict,
        *,
        created_by: str = "inventory-sync",
    ) -> models.EquipmentAsset:
        """Apply external inventory sync data to an existing equipment asset by serial only."""
        serial_number = str(
            payload.get("serialNumber")
            or payload.get("serial_number")
            or ""
        ).strip().upper()

        if not serial_number:
            raise ValueError("serialNumber is required")

        asset = db.query(models.EquipmentAsset).filter_by(serial_number=serial_number).first()
        if asset is None:
            raise ValueError(f"matching equipment asset not found for serial: {serial_number}")

        changed_fields: list[str] = []
        sync_status_value = payload.get("customerSyncStatus")
        if sync_status_value is None:
            sync_status_value = payload.get("customer_sync_status")
        explicit_sync_status = None
        if sync_status_value not in (None, ""):
            try:
                explicit_sync_status = int(sync_status_value)
            except (TypeError, ValueError):
                explicit_sync_status = None

        customer_name = " ".join(str(payload.get("customerName") or payload.get("customer_name") or "").strip().split())
        has_real_customer_name = self.is_meaningful_customer_name(customer_name)
        if has_real_customer_name and asset.customer_name != customer_name:
            asset.customer_name = customer_name
            changed_fields.append(f"customerName={customer_name}")

        device_model = " ".join(str(payload.get("deviceModel") or payload.get("device_model") or "").strip().split())
        if device_model and asset.device_model != device_model:
            asset.device_model = device_model
            changed_fields.append(f"deviceModel={device_model}")

        asset_status = self.normalize_asset_status(payload.get("assetStatus") or payload.get("asset_status"))
        if asset_status is not None and asset.asset_status != asset_status:
            asset.asset_status = asset_status
            changed_fields.append(f"assetStatus={asset_status}")

        sale_type = self.normalize_sale_type(payload.get("saleType") or payload.get("sale_type"))
        if sale_type is not None and asset.sale_type != sale_type:
            asset.sale_type = sale_type
            changed_fields.append(f"saleType={sale_type}")

        license_info = payload.get("licenseInfo") or payload.get("license_info") or {}
        if isinstance(license_info, dict):
            license_flags = license_info.get("flags")
            if license_flags is not None:
                try:
                    license_flags = int(license_flags)
                except (TypeError, ValueError):
                    license_flags = None
                if license_flags is not None and int(asset.license_flags or 0) != license_flags:
                    asset.license_flags = license_flags
                    changed_fields.append(f"licenseFlags={license_flags}")

            start_date = license_info.get("startDate") or license_info.get("licenseStartDate")
            end_date = license_info.get("endDate") or license_info.get("licenseEndDate")
            if start_date:
                try:
                    parsed_start = date.fromisoformat(str(start_date))
                    if asset.license_start_date != parsed_start:
                        asset.license_start_date = parsed_start
                        changed_fields.append(f"licenseStartDate={parsed_start.isoformat()}")
                except ValueError:
                    pass
            if end_date:
                try:
                    parsed_end = date.fromisoformat(str(end_date))
                    if asset.license_end_date != parsed_end:
                        asset.license_end_date = parsed_end
                        changed_fields.append(f"licenseEndDate={parsed_end.isoformat()}")
                except ValueError:
                    pass

        effective_has_customer_name = has_real_customer_name or self.is_meaningful_customer_name(asset.customer_name)

        desired_sync_status = asset.customer_sync_status
        if explicit_sync_status == ASSET_CUSTOMER_SYNC_PENDING:
            desired_sync_status = ASSET_CUSTOMER_SYNC_PENDING
        elif explicit_sync_status == ASSET_CUSTOMER_SYNC_SYNCED:
            desired_sync_status = (
                ASSET_CUSTOMER_SYNC_SYNCED
                if effective_has_customer_name
                else ASSET_CUSTOMER_SYNC_PENDING
            )
        elif effective_has_customer_name:
            desired_sync_status = ASSET_CUSTOMER_SYNC_SYNCED
        else:
            desired_sync_status = ASSET_CUSTOMER_SYNC_PENDING

        if int(asset.customer_sync_status or 0) != int(desired_sync_status or 0):
            asset.customer_sync_status = int(desired_sync_status or 0)
            changed_fields.append(f"customerSyncStatus={int(desired_sync_status or 0)}")

        if changed_fields:
            asset.updated_at = datetime.now(tz=timezone.utc)
            self.append_history(
                db,
                asset_id=asset.id,
                event_type=ASSET_HISTORY_CUSTOMER_SYNC,
                summary="외부 재고 동기화 정보가 반영되었습니다.",
                detail=", ".join(changed_fields),
                created_by=created_by,
            )
        else:
            self.append_history(
                db,
                asset_id=asset.id,
                event_type=ASSET_HISTORY_CUSTOMER_SYNC,
                summary="외부 재고 동기화를 수신했지만 변경점은 없습니다.",
                detail=str(payload),
                created_by=created_by,
            )
        db.flush()
        return asset

    def upsert_asset(
        self,
        db: Session,
        *,
        serial_number: str,
        device_model: str = "",
        client_id: int | None = None,
        event_type: int = ASSET_HISTORY_ENROLL,
        event_summary: str = "",
        event_detail: str = "",
        created_by: str = "system",
    ) -> models.EquipmentAsset | None:
        """Create or update an equipment asset linked to a client/enroll event."""
        serial = (serial_number or "").strip().upper()
        model_name = " ".join((device_model or "").strip().split())
        if not serial:
            return None

        asset = db.query(models.EquipmentAsset).filter_by(serial_number=serial).first()
        if asset is None:
            asset = models.EquipmentAsset(
                serial_number=serial,
                customer_name="",
                customer_sync_status=ASSET_CUSTOMER_SYNC_PENDING,
                device_model=model_name,
                license_flags=0,
                sale_type=ASSET_SALE_TYPE_LEASE,
                asset_status=ASSET_STATUS_STOCK_NEW,
                client_id=client_id,
            )
            db.add(asset)
            db.flush()
            self.append_history(
                db,
                asset_id=asset.id,
                event_type=ASSET_HISTORY_REGISTER,
                summary="장비 자산이 자동 등록되었습니다.",
                detail=f"시리얼 {serial} 기준으로 장비 관리 자산이 생성되었습니다.",
                created_by=created_by,
            )

        changed = False
        if model_name and asset.device_model != model_name:
            asset.device_model = model_name
            changed = True
        if client_id and asset.client_id != client_id:
            asset.client_id = client_id
            changed = True
        if asset.asset_status == 0:
            asset.asset_status = ASSET_STATUS_STOCK_NEW
            changed = True
        if asset.sale_type == 0:
            asset.sale_type = ASSET_SALE_TYPE_LEASE
            changed = True

        self.append_history(
            db,
            asset_id=asset.id,
            event_type=event_type,
            summary=(event_summary or "장비 정보가 동기화되었습니다.").strip(),
            detail=(event_detail or f"시리얼 {serial} 기준 장비 정보가 반영되었습니다.").strip(),
            created_by=created_by,
        )
        if changed:
            asset.updated_at = datetime.now(tz=timezone.utc)
        db.flush()
        return asset
