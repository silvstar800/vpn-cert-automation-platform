"""Mock runtime helpers for Docker/demo execution."""

from __future__ import annotations

import io
import json
import tarfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import models


MOCK_CLIENT_ROWS = [
    {
        "hostname": "yein-uiwon",
        "mac": "00:1A:8C:40:10:01",
        "vpn_type": "openvpn",
        "cert_cn": "yein-uiwon",
        "is_legacy": False,
        "status": "active",
        "assigned_ip": "172.23.208.37",
        "serial_number": "S181102RB8C9TD9",
        "device_model": "SG 105",
        "customer_name": "연세예인의원",
        "customer_sync_status": 1,
        "asset_status": 1,
        "license_flags": 0,
        "certificate_days_left": 180,
        "license_days_left": 240,
    },
    {
        "hostname": "suwon-hyo-yoyang",
        "mac": "00:1A:8C:40:10:02",
        "vpn_type": "openvpn",
        "cert_cn": "suwon-hyo-yoyang",
        "is_legacy": True,
        "status": "active",
        "assigned_ip": "172.23.212.1",
        "serial_number": "S18109CKJK78V35",
        "device_model": "SG 105",
        "customer_name": "수원효요양병원",
        "customer_sync_status": 1,
        "asset_status": 1,
        "license_flags": 0,
        "certificate_days_left": 14,
        "license_days_left": 45,
    },
    {
        "hostname": "miso-1004-clinic",
        "mac": "00:1A:8C:40:10:03",
        "vpn_type": "openvpn",
        "cert_cn": "miso-1004-clinic",
        "is_legacy": True,
        "status": "active",
        "assigned_ip": "172.23.212.7",
        "serial_number": "S181102TK9GQXC6",
        "device_model": "SG 105",
        "customer_name": "외부 연동 대기",
        "customer_sync_status": 0,
        "asset_status": 1,
        "license_flags": 0,
        "certificate_days_left": -3,
        "license_days_left": -15,
    },
    {
        "hostname": "atech-HQ",
        "mac": "00:1A:8C:40:10:11",
        "vpn_type": "sfos",
        "cert_cn": "atech-HQ",
        "is_legacy": False,
        "status": "active",
        "assigned_ip": "172.23.220.1",
        "serial_number": "C4207AP68XWWW8A",
        "device_model": "XGS 88",
        "customer_name": "에이텍 본사",
        "customer_sync_status": 1,
        "asset_status": 1,
        "license_flags": 0,
        "certificate_days_left": 62,
        "license_days_left": 20,
    },
    {
        "hostname": "atech-gwangju",
        "mac": "00:1A:8C:40:10:12",
        "vpn_type": "sfos",
        "cert_cn": "atech-gwangju",
        "is_legacy": False,
        "status": "active",
        "assigned_ip": "172.23.220.5",
        "serial_number": "C4207TPQ6J7DKM8",
        "device_model": "XGS 108",
        "customer_name": "외부 연동 대기",
        "customer_sync_status": 0,
        "asset_status": 1,
        "license_flags": 1,
        "certificate_days_left": 5,
        "license_days_left": 5,
    },
]


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _create_tar_archive(path: Path, name: str, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        data = content.encode("utf-8")
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mtime = int(datetime.now(tz=timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(data))


def _build_status_file(clients: list[dict[str, Any]]) -> str:
    lines = [
        "TITLE,OpenVPN 2.6 mock status",
        f"TIME,{datetime.now(tz=timezone.utc).strftime('%a %b %d %H:%M:%S %Y')},0",
        "HEADER,CLIENT_LIST,Common Name,Real Address,Virtual Address,Bytes Received,Bytes Sent,Connected Since",
    ]
    for row in clients:
        lines.append(
            "CLIENT_LIST,{cn},{real},{vip},10240,20480,{since}".format(
                cn=row["cert_cn"],
                real=f"203.0.113.{10 + len(lines)}:1194",
                vip=row["assigned_ip"],
                since=datetime.now(tz=timezone.utc).strftime("%a %b %d %H:%M:%S %Y"),
            )
        )
    lines.extend(
        [
            "ROUTING_TABLE,Virtual Address,Common Name,Real Address,Last Ref",
            "GLOBAL_STATS,Max bcast/mcast queue length,0",
            "END",
        ]
    )
    return "\n".join(lines) + "\n"


def create_mock_backup_bundle(backup_base_dir: Path, backup_id: str | None = None) -> tuple[Path, dict[str, Any]]:
    backup_base_dir.mkdir(parents=True, exist_ok=True)
    bundle_id = backup_id or datetime.now().strftime("backup_%Y%m%d_%H%M%S")
    bundle_dir = backup_base_dir / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    (bundle_dir / "db.dump").write_text("mock database dump\n", encoding="utf-8")
    _create_tar_archive(bundle_dir / "pki.tar.gz", "README.txt", "mock pki archive")
    _create_tar_archive(bundle_dir / "ccd.tar.gz", "README.txt", "mock ccd archive")
    _create_tar_archive(bundle_dir / "ccd_legacy.tar.gz", "README.txt", "mock ccd legacy archive")
    _create_tar_archive(bundle_dir / "ccd_sfos.tar.gz", "README.txt", "mock ccd sfos archive")
    _create_tar_archive(bundle_dir / "openvpn_conf.tar.gz", "server.conf", "status /opt/certsvc/mock_data/status-server.log\n")
    _create_tar_archive(bundle_dir / "env.tar.gz", ".env", "MOCK_MODE=1\n")
    _create_tar_archive(bundle_dir / "ui.tar.gz", "README.txt", "mock ui archive")

    files = sorted(path.name for path in bundle_dir.iterdir() if path.is_file())
    manifest = {
        "backupId": bundle_id,
        "createdAt": datetime.now(tz=timezone.utc).isoformat(),
        "hostname": "mock-certsvc",
        "scheduleType": "weekly",
        "scheduleTime": "07:00",
        "scheduleWeekday": 5,
        "scheduleMonthday": None,
        "ftpHost": "mock-ftp.local",
        "ftpRemotePath": "/SSL_VPN_Server_Backup",
        "uiIncluded": True,
        "files": files + ["manifest.json"],
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle_dir, manifest


def seed_mock_environment(
    *,
    db: Any,
    encrypt_secret,
    backup_base_dir: str,
    client_backup_base_dir: str,
    pki_dir: str,
    ccd_dir: str,
    ccd_legacy_dir: str,
    ccd_sfos_dir: str,
    openvpn_status_file: str,
    openvpn_legacy_status_file: str,
    sfos_status_file: str,
    openvpn_server_conf: str,
    sfos_server_conf: str,
    security_state_file: str,
    runtime_guard_log_file: str,
    feature_log_dir: str,
) -> None:
    """Populate a runnable demo environment when MOCK_MODE is enabled."""
    backup_root = Path(backup_base_dir)
    client_backup_root = Path(client_backup_base_dir)
    pki_root = Path(pki_dir)
    feature_root = Path(feature_log_dir)
    now_seoul = datetime.now().astimezone()

    for path in [backup_root, client_backup_root, pki_root, feature_root, Path(ccd_dir), Path(ccd_legacy_dir), Path(ccd_sfos_dir)]:
        path.mkdir(parents=True, exist_ok=True)

    _write_text(Path(openvpn_server_conf), f"status {openvpn_status_file}\n")
    _write_text(Path(sfos_server_conf), f"status {sfos_status_file}\n")
    _write_text(Path(openvpn_status_file), _build_status_file([row for row in MOCK_CLIENT_ROWS if row["vpn_type"] == "openvpn" and not row["is_legacy"]]))
    _write_text(Path(openvpn_legacy_status_file), _build_status_file([row for row in MOCK_CLIENT_ROWS if row["vpn_type"] == "openvpn" and row["is_legacy"]]))
    _write_text(Path(sfos_status_file), _build_status_file([row for row in MOCK_CLIENT_ROWS if row["vpn_type"] == "sfos"]))

    _write_text(pki_root / "ta.key", "mock-ta-key\n")
    _write_text(pki_root / "ca.crt", "-----BEGIN CERTIFICATE-----\nMOCK-CA\n-----END CERTIFICATE-----\n")
    _write_text(pki_root / "pki" / "issued" / "README.txt", "mock issued certs\n")

    for row in MOCK_CLIENT_ROWS:
        client = db.query(models.Client).filter_by(hostname=row["hostname"], vpn_type=row["vpn_type"]).first()
        if client is None:
            client = models.Client(
                hostname=row["hostname"],
                mac=row["mac"],
                vpn_type=row["vpn_type"],
                cert_cn=row["cert_cn"],
                is_legacy=row["is_legacy"],
                status=row["status"],
            )
            db.add(client)
            db.flush()
        else:
            client.mac = row["mac"]
            client.cert_cn = row["cert_cn"]
            client.is_legacy = row["is_legacy"]
            client.status = row["status"]
        certificate_days_left = int(row.get("certificate_days_left", 180))
        client.created_at = now_seoul - timedelta(days=(3650 - certificate_days_left))
        client.updated_at = now_seoul - timedelta(minutes=3 if row["status"] == "active" else 120)

        lease = db.query(models.IPLease).filter_by(client_id=client.id).first()
        if lease is None:
            lease = models.IPLease(client_id=client.id, assigned_ip=row["assigned_ip"], is_active=True)
            db.add(lease)
        else:
            lease.assigned_ip = row["assigned_ip"]
            lease.is_active = True

        asset = db.query(models.EquipmentAsset).filter_by(serial_number=row["serial_number"]).first()
        if asset is None:
            asset = models.EquipmentAsset(
                serial_number=row["serial_number"],
                customer_name=row["customer_name"],
                customer_sync_status=row["customer_sync_status"],
                device_model=row["device_model"],
                license_flags=row["license_flags"],
                license_start_date=date.today() - timedelta(days=120),
                license_end_date=date.today() + timedelta(days=int(row.get("license_days_left", 320))),
                sale_type=1,
                asset_status=row["asset_status"],
                client_id=client.id,
            )
            db.add(asset)
            db.flush()
        else:
            asset.customer_name = row["customer_name"]
            asset.customer_sync_status = row["customer_sync_status"]
            asset.device_model = row["device_model"]
            asset.license_flags = row["license_flags"]
            asset.license_start_date = date.today() - timedelta(days=120)
            asset.license_end_date = date.today() + timedelta(days=int(row.get("license_days_left", 320)))
            asset.sale_type = 1
            asset.asset_status = row["asset_status"]
            asset.client_id = client.id

        backup_dir = client_backup_root / row["serial_number"].lower()
        backup_dir.mkdir(parents=True, exist_ok=True)
        sample_backup = backup_dir / f"{row['hostname']}_backup.tar.gz"
        if not sample_backup.exists():
            _create_tar_archive(sample_backup, "README.txt", f"mock client backup for {row['hostname']}")

    record = db.query(models.BackupSetting).filter_by(id=1).first()
    if record is None:
        record = models.BackupSetting(id=1, job_logs_json="[]")
        db.add(record)
        db.flush()

    record.ftp_host = "mock-ftp.local"
    record.ftp_username = "mock-user"
    record.ftp_password_enc = encrypt_secret("mock-password")
    record.ftp_remote_path = "/SSL_VPN_Server_Backup"
    record.schedule_type = "weekly"
    record.schedule_time = "07:00"
    record.schedule_weekday = 5
    record.schedule_monthday = None
    record.slack_enabled = True
    record.slack_bot_token_enc = encrypt_secret("xoxb-mock-token")
    record.slack_channel = "#01-alert"
    record.slack_notify_certificate_expiry = True
    record.slack_notify_backup_completed = True
    record.slack_notify_security_alert = True
    record.slack_notify_service_down = True
    record.slack_notify_resource_threshold = True
    record.slack_cpu_threshold = 80
    record.slack_memory_threshold = 85
    record.slack_disk_threshold = 80
    record.updated_by = "mock-seed"

    existing_logs = []
    try:
        existing_logs = json.loads(record.job_logs_json or "[]")
    except json.JSONDecodeError:
        existing_logs = []
    if not existing_logs:
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        record.job_logs_json = json.dumps(
            [
                {
                    "id": "mock-alert-1",
                    "jobType": "alert_backup_completed",
                    "status": "success",
                    "message": "백업 완료 알림 전송",
                    "detail": json.dumps({"backupId": "backup_20260504_090000"}, ensure_ascii=False),
                    "trigger": "auto",
                    "ts": now_ts - 1800,
                },
                {
                    "id": "mock-backup-1",
                    "jobType": "backup",
                    "status": "success",
                    "message": "백업 실행 완료 (backup_20260504_090000)",
                    "detail": json.dumps({"backupId": "backup_20260504_090000"}, ensure_ascii=False),
                    "trigger": "scheduled",
                    "ts": now_ts - 2400,
                },
                {
                    "id": "mock-slack-test",
                    "jobType": "slack_test",
                    "status": "success",
                    "message": "슬랙 테스트 메시지 전송 완료",
                    "detail": json.dumps({"templateType": "backup_completed", "channel": "#01-alert"}, ensure_ascii=False),
                    "trigger": "manual",
                    "ts": now_ts - 600,
                },
            ],
            ensure_ascii=False,
        )

    create_mock_backup_bundle(backup_root, "backup_20260504_090000")

    _write_text(
        Path(runtime_guard_log_file),
        "\n".join(
            [
                "[2026-05-04 09:00:00] runtime guard started",
                "[2026-05-04 09:00:02] openvpn-server@server.service active",
                "[2026-05-04 09:00:03] openvpn-server@server-legacy.service active",
                "[2026-05-04 09:00:04] openvpn-server@server-sfos.service disconnected",
            ]
        )
        + "\n",
    )

    _write_text(
        Path(security_state_file),
        json.dumps(
            {
                "settings": {"windowSeconds": 600, "alertThreshold": 5, "banThreshold": 10},
                "ip_stats": {
                    "203.0.113.250": {
                        "failures": [int(datetime.now(tz=timezone.utc).timestamp()) - 120],
                        "lastFailureReason": "bad signature",
                        "lastHostname": "unknown",
                        "lastEndpoint": "/enroll",
                        "status": "banned",
                    }
                },
                "events": [
                    {
                        "ts": int(datetime.now(tz=timezone.utc).timestamp()) - 120,
                        "type": "ban",
                        "ip": "203.0.113.250",
                        "endpoint": "/enroll",
                        "reason": "bad signature",
                        "failureCount": 10,
                    }
                ],
                "dailyOverflow": {},
                "dailySummary": {
                    "enabled": False,
                    "scheduleHour": 8,
                    "minOverflowFailures": 10,
                    "timezone": "Asia/Seoul",
                    "lastSentDate": "",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    _write_text(feature_root / "certsvc-backup.log", '{"event":"mock-seed","status":"ok"}\n')
    db.commit()


def build_mock_health(*, db_ok: bool, checks: dict[str, bool], now_label: str) -> dict[str, Any]:
    return {
        "status": "ok" if db_ok and all(checks.values()) else "degraded",
        "database": db_ok,
        "checks": checks,
        "time": now_label,
    }


def build_mock_system_status(*, openvpn_port: int, legacy_port: int, sfos_port: int, dirs: dict[str, bool]) -> dict[str, Any]:
    return {
        "services": [
            {"name": "openvpn-server@server", "status": "active", "port": f"{openvpn_port}/udp"},
            {"name": "openvpn-server@server-legacy", "status": "active", "port": f"{legacy_port}/udp"},
            {"name": "openvpn-server@sfos", "status": "disconnected", "port": f"{sfos_port}/tcp"},
            {"name": "certsvc-db", "status": "running", "port": "5432/tcp"},
        ],
        "resources": {
            "cpu": {"usage_percent": 17.4},
            "memory": {"used_percent": 42.1, "used_gb": 3.4, "total_gb": 8.0},
            "disk": {"used_percent": 38.2, "used_gb": 24.0, "total_gb": 64.0},
        },
        "dirs": dirs,
    }


def build_mock_backup_status(*, backup_base_dir: str, now_label: str) -> dict[str, Any]:
    backup_root = Path(backup_base_dir)
    latest_bundle = next((path for path in sorted(backup_root.glob("backup_*"), key=lambda p: p.name, reverse=True) if path.is_dir()), None)
    backup_id = latest_bundle.name if latest_bundle else "backup_20260504_090000"
    return {
        "latestLocalBackup": {
            "exists": latest_bundle is not None,
            "backupId": backup_id,
            "valid": True,
            "missingFiles": [],
            "validatedArchives": ["pki.tar.gz", "ccd.tar.gz", "ccd_legacy.tar.gz", "ccd_sfos.tar.gz", "openvpn_conf.tar.gz", "env.tar.gz", "ui.tar.gz"],
            "dbObjectCountHint": 24,
            "uiIncluded": True,
            "checkedAt": now_label,
        },
        "restoreEnvironment": {
            "ready": True,
            "checkedAt": now_label,
            "stageDir": str(Path(backup_base_dir).parent / "restore_staging"),
            "bundleDir": str(latest_bundle) if latest_bundle else "",
            "bundleSizeBytes": 1024 * 256,
            "freeBytes": 1024 * 1024 * 1024 * 8,
            "requiredFreeBytes": 1024 * 1024 * 512,
            "serviceChecklistOk": True,
            "failedChecks": [],
            "validationOk": True,
            "missingFiles": [],
            "uiIncluded": True,
        },
        "lastSuccess": {
            "jobType": "backup",
            "status": "success",
            "message": f"백업 실행 완료 ({backup_id})",
            "trigger": "scheduled",
            "ts": int(datetime.now(tz=timezone.utc).timestamp()) - 2400,
        },
        "lastFailure": None,
        "recentLogCount": 3,
    }


def build_mock_restore_list(*, backup_base_dir: str, remote_path: str) -> dict[str, Any]:
    backup_root = Path(backup_base_dir)
    backups = [path.name for path in sorted(backup_root.glob("backup_*"), key=lambda p: p.name, reverse=True) if path.is_dir()]
    return {"ok": True, "remotePath": remote_path or "/SSL_VPN_Server_Backup", "backups": backups}


def build_mock_backup_result(*, backup_base_dir: str) -> dict[str, Any]:
    bundle_dir, manifest = create_mock_backup_bundle(Path(backup_base_dir))
    uploaded = sorted(path.name for path in bundle_dir.iterdir() if path.is_file())
    validation = {
        "backupId": bundle_dir.name,
        "valid": True,
        "missingFiles": [],
        "validatedArchives": [name for name in uploaded if name.endswith(".tar.gz")],
        "dbObjectCountHint": 24,
        "uiIncluded": True,
        "checkedAt": datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return {"backupId": bundle_dir.name, "uploaded": uploaded, "manifest": manifest, "validation": validation}


def build_mock_restore_result(*, backup_base_dir: str, backup_id: str, mode: str, now_label: str) -> dict[str, Any]:
    bundle_dir = Path(backup_base_dir) / backup_id
    if not bundle_dir.exists():
        raise RuntimeError(f"backup bundle not found: {backup_id}")
    validation = {
        "backupId": backup_id,
        "valid": True,
        "missingFiles": [],
        "validatedArchives": ["pki.tar.gz", "ccd.tar.gz", "ccd_legacy.tar.gz", "ccd_sfos.tar.gz", "openvpn_conf.tar.gz", "env.tar.gz", "ui.tar.gz"],
        "dbObjectCountHint": 24,
        "uiIncluded": True,
    }
    preflight = {
        "ready": True,
        "checkedAt": now_label,
        "stageDir": str(Path(backup_base_dir).parent / "restore_staging"),
        "bundleDir": str(bundle_dir),
        "bundleSizeBytes": 1024 * 256,
        "freeBytes": 1024 * 1024 * 1024 * 8,
        "requiredFreeBytes": 1024 * 1024 * 512,
        "serviceChecklistOk": True,
        "failedChecks": [],
        "validationOk": True,
        "missingFiles": [],
        "uiIncluded": True,
    }
    result = {"mode": mode, "validation": validation, "preflight": preflight}
    if mode == "restore":
        result["restore"] = {
            "restorePoint": f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "stages": [
                {"name": "복구 전 자동 백업 생성", "status": "success", "detail": "mock restore point created"},
                {"name": "DB 복구", "status": "success", "detail": "mock db restore applied"},
                {"name": "서비스 점검", "status": "success", "detail": "mock services healthy"},
            ],
            "serviceChecklistOk": True,
        }
    return result


def build_mock_slack_response(*, channel: str, template_type: str) -> dict[str, Any]:
    return {
        "ok": True,
        "channel": channel,
        "templateType": template_type,
        "response": {
            "ok": True,
            "channel": channel,
            "ts": str(datetime.now(tz=timezone.utc).timestamp()),
            "mock": True,
        },
    }
