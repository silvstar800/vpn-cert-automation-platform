from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import models
from db import SessionLocal


NS_MAIN = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}

ASSET_SALE_TYPE_LEASE = 1
ASSET_STATUS_STOCK_NEW = 3
ASSET_CUSTOMER_SYNC_PENDING = 0
ASSET_HISTORY_IMPORT = 5


def read_xlsx_rows(path: Path) -> list[tuple[int, list[str]]]:
    rows_out: list[tuple[int, list[str]]] = []
    with ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS_MAIN):
                text = "".join(t.text or "" for t in si.iterfind(".//m:t", NS_MAIN))
                shared.append(text)

        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("r:Relationship", NS_REL)}
        first_sheet = wb.findall("m:sheets/m:sheet", NS_MAIN)[0]
        rid = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        if not rid or rid not in rel_map:
            return rows_out

        root = ET.fromstring(zf.read("xl/" + rel_map[rid]))
        for idx, row in enumerate(root.findall("m:sheetData/m:row", NS_MAIN), start=1):
            vals: list[str] = []
            for cell in row.findall("m:c", NS_MAIN):
                cell_type = cell.attrib.get("t")
                value = cell.find("m:v", NS_MAIN)
                inline = cell.find("m:is/m:t", NS_MAIN)
                if cell_type == "s" and value is not None:
                    vals.append(shared[int(value.text)])
                elif inline is not None:
                    vals.append(inline.text or "")
                elif value is not None:
                    vals.append(value.text or "")
                else:
                    vals.append("")
            if any(v.strip() for v in vals):
                rows_out.append((idx, vals))
    return rows_out


def normalize_serial(value: str) -> str:
    return (value or "").strip().upper()


def normalize_model(value: str) -> str:
    return " ".join((value or "").strip().split())


def main() -> int:
    parser = argparse.ArgumentParser(description="Import equipment asset master rows from xlsx")
    parser.add_argument("xlsx_path", help="Path to source xlsx")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing to DB")
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx_path)
    if not xlsx_path.exists():
        raise SystemExit(f"xlsx not found: {xlsx_path}")

    rows = read_xlsx_rows(xlsx_path)
    deduped: dict[str, tuple[int, str]] = {}
    duplicates: dict[str, list[tuple[int, str]]] = defaultdict(list)

    for row_idx, values in rows:
        serial = normalize_serial(values[0] if len(values) > 0 else "")
        model = normalize_model(values[1] if len(values) > 1 else "")
        if not serial:
            continue
        if serial in deduped:
            duplicates[serial].append((row_idx, model))
            continue
        deduped[serial] = (row_idx, model)

    print(f"parsed rows      : {len(rows)}")
    print(f"unique serials   : {len(deduped)}")
    print(f"duplicate serials: {len(duplicates)}")
    for serial, entries in sorted(duplicates.items()):
        print(f"  - {serial}: {entries} (kept first row {deduped[serial][0]})")

    if args.dry_run:
        return 0

    db = SessionLocal()
    created = 0
    updated = 0
    history_added = 0
    try:
        for serial, (_row_idx, model) in deduped.items():
            asset = db.query(models.EquipmentAsset).filter_by(serial_number=serial).first()
            if asset is None:
                asset = models.EquipmentAsset(
                    serial_number=serial,
                    customer_name="",
                    customer_sync_status=ASSET_CUSTOMER_SYNC_PENDING,
                    device_model=model,
                    license_flags=0,
                    sale_type=ASSET_SALE_TYPE_LEASE,
                    asset_status=ASSET_STATUS_STOCK_NEW,
                )
                db.add(asset)
                db.flush()
                db.add(
                    models.EquipmentAssetHistory(
                        asset_id=asset.id,
                        event_type=ASSET_HISTORY_IMPORT,
                        summary="장비 시리얼 마스터가 초기 적재되었습니다.",
                        detail=f"엑셀 기준 시리얼 {serial} / 모델 {model or '-'} 정보가 적재되었습니다.",
                        created_by="import-script",
                    )
                )
                created += 1
                history_added += 1
            else:
                changed = False
                if model and asset.device_model != model:
                    asset.device_model = model
                    changed = True
                if changed:
                    db.add(
                        models.EquipmentAssetHistory(
                            asset_id=asset.id,
                            event_type=ASSET_HISTORY_IMPORT,
                            summary="장비 시리얼 마스터 기준으로 모델 정보가 보정되었습니다.",
                            detail=f"엑셀 기준 모델 값으로 {model} 정보가 반영되었습니다.",
                            created_by="import-script",
                        )
                    )
                    updated += 1
                    history_added += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"created assets   : {created}")
    print(f"updated assets   : {updated}")
    print(f"history inserted : {history_added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
