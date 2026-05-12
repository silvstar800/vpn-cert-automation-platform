"""Backup and restore management."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request
from datetime import datetime, timezone
from ftplib import FTP, all_errors as FTP_ERRORS
from pathlib import Path
from sqlalchemy.engine.url import make_url
from typing import Any, Dict, Optional, Tuple


class BackupManager:
    """Manages backup creation, listing, logging, and restore helpers."""

    def __init__(self, backup_base_dir: str):
        """Initialize with backup directory."""
        self.backup_dir = Path(backup_base_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def list_backups(self) -> list[Dict[str, Any]]:
        """List all available backups with metadata."""
        backups = []
        try:
            for backup_path in sorted(self.backup_dir.glob("backup_*"), reverse=True):
                if backup_path.is_dir():
                    backups.append(
                        {
                            "name": backup_path.name,
                            "created_at": backup_path.stat().st_mtime,
                            "manifest": self.get_backup_manifest(backup_path.name) or {},
                        }
                    )
        except Exception:
            pass
        return backups

    def get_backup_size(self, backup_name: str) -> int:
        """Get size of backup directory in bytes."""
        backup_path = self.backup_dir / backup_name
        total_size = 0
        try:
            if backup_path.exists():
                for item in backup_path.rglob("*"):
                    if item.is_file():
                        total_size += item.stat().st_size
        except Exception:
            pass
        return total_size

    def get_backup_manifest(self, backup_name: str) -> Optional[Dict[str, Any]]:
        """Get backup manifest information."""
        backup_path = self.backup_dir / backup_name
        manifest_path = backup_path / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_backup_path(self, backup_name: str) -> Optional[Path]:
        """Get backup directory path if it exists."""
        backup_path = self.backup_dir / backup_name
        if backup_path.exists() and backup_path.is_dir():
            return backup_path
        return None

    def cleanup_old_backups(self, keep_count: int = 5) -> int:
        """Clean up old backups, keeping only the most recent ones."""
        deleted_count = 0
        try:
            bundles = sorted(self.backup_dir.glob("backup_*"), key=lambda p: p.name, reverse=True)
            for old_bundle in bundles[keep_count:]:
                try:
                    shutil.rmtree(old_bundle, ignore_errors=True)
                    deleted_count += 1
                except Exception:
                    pass
        except Exception:
            pass
        return deleted_count

    def build_backup_manifest(self, record: Any, backup_id: str, files: list[str]) -> Dict[str, Any]:
        """Create a manifest matching the API/UI payload format."""
        return {
            "backupId": backup_id,
            "createdAt": datetime.now(tz=timezone.utc).isoformat(),
            "hostname": "",
            "scheduleType": getattr(record, "schedule_type", "manual") or "manual",
            "scheduleTime": getattr(record, "schedule_time", "") or "",
            "scheduleWeekday": getattr(record, "schedule_weekday", None),
            "scheduleMonthday": getattr(record, "schedule_monthday", None),
            "ftpHost": getattr(record, "ftp_host", "") or "",
            "ftpRemotePath": getattr(record, "ftp_remote_path", "") or "",
            "uiIncluded": "ui.tar.gz" in files,
            "files": files,
        }

    def parse_job_logs(self, record: Any) -> list[dict[str, Any]]:
        """Load backup job logs stored inline in the singleton settings row."""
        raw = getattr(record, "job_logs_json", "[]") or "[]"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def save_job_logs(self, record: Any, logs: list[dict[str, Any]]) -> None:
        """Persist bounded backup job log history."""
        record.job_logs_json = json.dumps(logs[-200:], ensure_ascii=False)

    def append_backup_log(
        self,
        db: Any,
        record: Any,
        *,
        job_type: str,
        status: str,
        message: str,
        detail: str = "",
        trigger: str = "manual",
    ) -> dict[str, Any]:
        """Append a backup-related log entry into the singleton record."""
        logs = self.parse_job_logs(record)
        entry = {
            "id": __import__("secrets").token_hex(8),
            "jobType": job_type,
            "status": status,
            "message": message,
            "detail": detail,
            "trigger": trigger,
            "ts": int(datetime.now(tz=timezone.utc).timestamp()),
        }
        logs.append(entry)
        self.save_job_logs(record, logs)
        db.add(record)
        db.commit()
        return entry

    def is_alert_log_entry(self, entry: dict[str, Any]) -> bool:
        """Return True when a backup log entry represents an alarm or notification."""
        job_type = str((entry or {}).get("jobType", "")).strip()
        return job_type in {
            "slack_test",
            "alert_backup_completed",
            "alert_restore_validated",
            "alert_restore_completed",
            "alert_restore_failed",
            "alert_security",
            "alert_service_down",
            "alert_service_recovered",
            "alert_resource_threshold",
            "alert_certificate_expiry",
            "alert_daily_summary",
        }

    def serialize_backup_settings(self, record: Any) -> dict[str, Any]:
        """Return safe backup settings payload for the UI without exposing passwords."""
        return {
            "ftpHost": getattr(record, "ftp_host", "") or "",
            "ftpUsername": getattr(record, "ftp_username", "") or "",
            "ftpRemotePath": getattr(record, "ftp_remote_path", "") or "",
            "scheduleType": getattr(record, "schedule_type", "manual") or "manual",
            "scheduleTime": getattr(record, "schedule_time", "") or "",
            "scheduleWeekday": getattr(record, "schedule_weekday", None),
            "scheduleMonthday": getattr(record, "schedule_monthday", None),
            "passwordConfigured": bool(getattr(record, "ftp_password_enc", "")),
            "passwordMasked": "********" if getattr(record, "ftp_password_enc", "") else "",
            "lastRunKey": getattr(record, "last_run_key", "") or "",
            "updatedAt": int(record.updated_at.timestamp()) if getattr(record, "updated_at", None) else None,
            "updatedBy": getattr(record, "updated_by", "system") or "system",
        }

    def serialize_slack_settings(self, record: Any, default_channel: str) -> dict[str, Any]:
        """Return safe Slack alert settings payload for the UI without exposing tokens."""
        return {
            "enabled": bool(getattr(record, "slack_enabled", False)),
            "channel": getattr(record, "slack_channel", "") or default_channel,
            "notifyCertificateExpiry": bool(getattr(record, "slack_notify_certificate_expiry", True)),
            "notifyBackupCompleted": bool(getattr(record, "slack_notify_backup_completed", True)),
            "notifySecurityAlert": bool(getattr(record, "slack_notify_security_alert", True)),
            "notifyServiceDown": bool(getattr(record, "slack_notify_service_down", True)),
            "notifyResourceThreshold": bool(getattr(record, "slack_notify_resource_threshold", True)),
            "cpuThreshold": int(getattr(record, "slack_cpu_threshold", 90) or 90),
            "memoryThreshold": int(getattr(record, "slack_memory_threshold", 90) or 90),
            "diskThreshold": int(getattr(record, "slack_disk_threshold", 90) or 90),
            "tokenConfigured": bool(getattr(record, "slack_bot_token_enc", "")),
            "tokenMasked": "xoxb-********" if getattr(record, "slack_bot_token_enc", "") else "",
        }

    def build_schedule_run_key(self, record: Any, now_local: datetime) -> str:
        """Return the dedupe key for the current schedule window."""
        schedule_type = getattr(record, "schedule_type", "manual")
        if schedule_type == "daily":
            return now_local.strftime("daily:%Y%m%d")
        if schedule_type == "weekly":
            return f"weekly:{now_local.strftime('%Y%W')}:{getattr(record, 'schedule_weekday', None)}"
        if schedule_type == "monthly":
            return now_local.strftime("monthly:%Y%m")
        return ""

    def should_run_scheduled_backup(self, record: Any, now_local: datetime) -> bool:
        """Check whether a scheduled backup is due at the current local time."""
        if getattr(record, "schedule_type", "manual") == "manual" or not getattr(record, "schedule_time", ""):
            return False
        hour, minute = [int(part) for part in str(record.schedule_time).split(":", 1)]
        if (now_local.hour, now_local.minute) < (hour, minute):
            return False
        if getattr(record, "schedule_type", "") == "weekly" and now_local.weekday() != getattr(record, "schedule_weekday", None):
            return False
        if getattr(record, "schedule_type", "") == "monthly" and now_local.day != getattr(record, "schedule_monthday", None):
            return False
        run_key = self.build_schedule_run_key(record, now_local)
        return bool(run_key and run_key != (getattr(record, "last_run_key", "") or ""))

    def validate_restore_backup(self, backup_name: str) -> Tuple[bool, str]:
        """Validate that backup is restorable."""
        backup_path = self.get_backup_path(backup_name)
        if not backup_path:
            return False, "Backup directory not found"

        manifest = self.get_backup_manifest(backup_name)
        if not manifest:
            return False, "Manifest not found"

        for filename in ["db.dump", "manifest.json"]:
            if not (backup_path / filename).exists():
                return False, f"Required file missing: {filename}"

        return True, ""

    # ========================================
    # Encryption/Decryption (Phase 1-A)
    # ========================================

    def derive_backup_cipher_key(self, config_secret: str) -> bytes:
        """Derive a stable symmetric key for backup setting encryption."""
        if not config_secret:
            raise RuntimeError("BACKUP_CONFIG_SECRET must not be empty")
        return hashlib.sha256(config_secret.encode("utf-8")).digest()

    @staticmethod
    def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
        """XOR-stream cipher for backup secrets."""
        chunks = []
        counter = 0
        offset = 0
        while offset < len(data):
            block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
            counter += 1
            part = data[offset : offset + len(block)]
            chunks.append(bytes(a ^ b for a, b in zip(part, block)))
            offset += len(block)
        return b"".join(chunks)

    def encrypt_backup_secret(self, secret_value: str, config_secret: str) -> str:
        """Encrypt backup-related secrets before persisting in DB."""
        if not secret_value:
            return ""
        key = self.derive_backup_cipher_key(config_secret)
        nonce = secrets.token_bytes(16)
        plaintext = secret_value.encode("utf-8")
        ciphertext = self._xor_stream(plaintext, key, nonce)
        mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(nonce + ciphertext + mac).decode("ascii")
        return f"enc1${token}"

    def decrypt_backup_secret(self, encrypted_value: str, config_secret: str) -> str:
        """Decrypt backup-related secrets stored by encrypt_backup_secret."""
        if not encrypted_value:
            return ""
        if not encrypted_value.startswith("enc1$"):
            return encrypted_value
        try:
            raw = base64.urlsafe_b64decode(encrypted_value.split("$", 1)[1].encode("ascii"))
            if len(raw) < 48:
                raise ValueError("invalid encrypted backup secret")
            nonce = raw[:16]
            mac = raw[-32:]
            ciphertext = raw[16:-32]
            key = self.derive_backup_cipher_key(config_secret)
            expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(mac, expected):
                raise ValueError("backup secret integrity check failed")
            plaintext = self._xor_stream(ciphertext, key, nonce)
            return plaintext.decode("utf-8")
        except (ValueError, KeyError, binascii.Error) as exc:
            raise ValueError(f"Failed to decrypt backup secret: {exc}") from exc

    # ========================================
    # FTP Operations (Phase 1-B)
    # ========================================

    @staticmethod
    def ftp_connect(host: str, username: str, password: str, timeout: int = 10) -> FTP:
        """Open and authenticate an FTP session."""
        ftp = FTP()
        ftp.connect(host=host, port=21, timeout=timeout)
        ftp.login(user=username, passwd=password)
        ftp.set_pasv(True)
        return ftp

    @staticmethod
    def ensure_ftp_remote_dir(ftp: FTP, remote_dir: str) -> str:
        """Ensure nested remote directories exist before file upload."""
        normalized = "/" + "/".join(part for part in (remote_dir or "").split("/") if part)
        if normalized == "/":
            ftp.cwd("/")
            return "/"
        ftp.cwd("/")
        for part in [segment for segment in normalized.split("/") if segment]:
            try:
                ftp.cwd(part)
            except FTP_ERRORS:
                try:
                    ftp.mkd(part)
                    ftp.cwd(part)
                except FTP_ERRORS as exc:
                    raise RuntimeError(f"failed to ensure ftp directory segment: {part}") from exc
        return ftp.pwd()

    @staticmethod
    def upload_dir_via_ftp(ftp: FTP, local_dir: Path, remote_dir: str) -> list[str]:
        """Upload a local directory tree to an FTP directory."""
        uploaded = []
        local_dir = local_dir.resolve()
        for path in sorted(local_dir.rglob("*")):
            rel = path.relative_to(local_dir).as_posix()
            target_parent = BackupManager.ensure_ftp_remote_dir(ftp, f"{remote_dir}/{Path(rel).parent.as_posix()}")
            if path.is_dir():
                continue
            ftp.cwd(target_parent)
            with open(path, "rb") as fh:
                ftp.storbinary(f"STOR {Path(rel).name}", fh)
            uploaded.append(rel)
        return uploaded

    @staticmethod
    def list_ftp_backup_directories(ftp: FTP, remote_root: str) -> list[str]:
        """Return backup bundle directory names from an FTP remote path."""
        resolved = BackupManager.ensure_ftp_remote_dir(ftp, remote_root)
        directories = []
        try:
            entries = list(ftp.mlsd(resolved))
            for name, facts in entries:
                if name in {".", ".."}:
                    continue
                if facts.get("type") == "dir" and name.startswith("backup_"):
                    directories.append(name)
            return sorted(directories, reverse=True)
        except FTP_ERRORS:
            current = ftp.pwd()
            ftp.cwd(resolved)
            try:
                for entry in ftp.nlst():
                    name = Path(entry).name
                    if not name.startswith("backup_"):
                        continue
                    try:
                        ftp.cwd(name)
                        directories.append(name)
                        ftp.cwd(resolved)
                    except FTP_ERRORS:
                        ftp.cwd(resolved)
            finally:
                ftp.cwd(current)
        return sorted(set(directories), reverse=True)

    @staticmethod
    def download_ftp_backup_bundle(ftp: FTP, remote_root: str, backup_id: str, local_root: Path) -> Tuple[Path, list[str]]:
        """Download one FTP backup bundle directory into local staging."""
        resolved_root = BackupManager.ensure_ftp_remote_dir(ftp, remote_root)
        remote_dir = f"{resolved_root.rstrip('/')}/{backup_id}"
        stage_dir = local_root / f"{backup_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        stage_dir.mkdir(parents=True, exist_ok=True)

        current = ftp.pwd()
        downloaded = []
        ftp.cwd(remote_dir)
        try:
            for entry in ftp.nlst():
                name = Path(entry).name
                if name in {".", ".."}:
                    continue
                local_path = stage_dir / name
                with open(local_path, "wb") as fh:
                    ftp.retrbinary(f"RETR {name}", fh.write)
                downloaded.append(name)
        finally:
            ftp.cwd(current)
        return stage_dir, sorted(downloaded)

    # ========================================
    # PostgreSQL & Archive Operations (Phase 1-C)
    # ========================================

    @staticmethod
    def build_pg_dump_command(database_url: str, output_path: Path) -> Tuple[list[str], dict[str, str]]:
        """Convert SQLAlchemy DATABASE_URL into pg_dump CLI arguments."""
        parsed = make_url(database_url)
        if not parsed.drivername.startswith("postgresql"):
            raise RuntimeError("DATABASE_URL must use a PostgreSQL scheme")

        database = parsed.database or ""
        username = parsed.username or ""
        if not database or not username:
            raise RuntimeError("DATABASE_URL must include username and database name")

        command = [
            "pg_dump",
            "--format=custom",
            "--file",
            str(output_path),
            "-h",
            parsed.host or "127.0.0.1",
            "-p",
            str(parsed.port or 5432),
            "-U",
            username,
            "-d",
            database,
        ]
        env = os.environ.copy()
        env["PGPASSWORD"] = parsed.password or ""
        return command, env

    @staticmethod
    def build_pg_restore_command(database_url: str, input_path: Path) -> Tuple[list[str], dict[str, str]]:
        """Convert SQLAlchemy DATABASE_URL into pg_restore CLI arguments."""
        parsed = make_url(database_url)
        if not parsed.drivername.startswith("postgresql"):
            raise RuntimeError("DATABASE_URL must use a PostgreSQL scheme")

        database = parsed.database or ""
        username = parsed.username or ""
        if not database or not username:
            raise RuntimeError("DATABASE_URL must include username and database name")

        command = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "-h",
            parsed.host or "127.0.0.1",
            "-p",
            str(parsed.port or 5432),
            "-U",
            username,
            "-d",
            database,
            str(input_path),
        ]
        env = os.environ.copy()
        env["PGPASSWORD"] = parsed.password or ""
        return command, env

    @staticmethod
    def validate_tar_archive(path: Path) -> None:
        """Open a tar archive to verify it is readable."""
        with tarfile.open(path, "r:gz") as tar:
            tar.getmembers()

    @staticmethod
    def archive_path_to_bundle(bundle_dir: Path, archive_name: str, source_path: Path, *, exclude_names: set[str] | None = None) -> Path:
        """Create a gzipped tar archive for a file or directory with optional exclusions."""
        exclude_names = set(exclude_names or set())
        archive_path = bundle_dir / archive_name
        with tarfile.open(archive_path, "w:gz") as tar:
            if source_path.is_dir():
                tar.add(source_path, arcname=source_path.name, recursive=False)
                root_parent = source_path.parent
                for item in sorted(source_path.rglob("*")):
                    rel = item.relative_to(root_parent)
                    if any(part in exclude_names for part in rel.parts):
                        continue
                    tar.add(item, arcname=str(rel), recursive=False)
            else:
                tar.add(source_path, arcname=source_path.name)
        return archive_path

    @staticmethod
    def validate_downloaded_backup_bundle(bundle_dir: Path, downloaded: list[str]) -> dict:
        """Validate manifest, dump, and archive files after download."""
        checks = []

        manifest_path = bundle_dir / "manifest.json"
        checks.append({"name": "manifest.json 존재", "status": "success" if manifest_path.exists() else "failed", "detail": str(manifest_path)})
        if not manifest_path.exists():
            raise RuntimeError("manifest.json is missing")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            checks.append({"name": "manifest.json 읽기", "status": "success", "detail": "manifest loaded"})
        except json.JSONDecodeError as exc:
            checks.append({"name": "manifest.json 읽기", "status": "failed", "detail": str(exc)})
            raise RuntimeError(f"manifest.json parse failed: {exc}") from exc

        expected_files = list(manifest.get("files", []))
        missing_files = [name for name in expected_files if not (bundle_dir / name).exists()]
        checks.append({
            "name": "manifest 파일 검증",
            "status": "success" if not missing_files else "failed",
            "detail": f"expected={len(expected_files)}, missing={len(missing_files)}",
        })

        db_dump_path = bundle_dir / "db.dump"
        checks.append({"name": "db.dump 존재", "status": "success" if db_dump_path.exists() else "failed", "detail": str(db_dump_path)})
        if not db_dump_path.exists():
            raise RuntimeError("db.dump is missing")

        restore_list = subprocess.run(
            ["pg_restore", "--list", str(db_dump_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        restore_lines = [line for line in restore_list.stdout.splitlines() if line.strip()]
        checks.append({
            "name": "DB 덤프 검증",
            "status": "success",
            "detail": f"pg_restore --list ok ({len(restore_lines)} lines)",
        })

        validated_archives = []
        archive_checks = []
        for archive_name in [
            "pki.tar.gz",
            "ccd.tar.gz",
            "ccd_legacy.tar.gz",
            "ccd_sfos.tar.gz",
            "openvpn_conf.tar.gz",
            "env.tar.gz",
            "ui.tar.gz",
        ]:
            archive_path = bundle_dir / archive_name
            if archive_path.exists():
                BackupManager.validate_tar_archive(archive_path)
                validated_archives.append(archive_name)
                archive_checks.append({"name": archive_name, "status": "success", "detail": "archive ok"})
            else:
                archive_checks.append({"name": archive_name, "status": "skipped", "detail": "not present"})

        checks.extend(archive_checks)

        return {
            "backupId": manifest.get("backupId") or bundle_dir.name,
            "stagingDir": str(bundle_dir),
            "downloadedFiles": downloaded,
            "expectedFiles": expected_files,
            "missingFiles": missing_files,
            "validatedArchives": validated_archives,
            "manifest": manifest,
            "valid": not missing_files,
            "checks": checks,
            "dbObjectCountHint": len(restore_lines),
        }

    # ========================================
    # Restore Pipeline (Phase 1-D)
    # ========================================

    @staticmethod
    def safe_extract_archive(archive_path: Path, target_dir: Path) -> None:
        """Extract a tar.gz archive after checking for path traversal."""
        target_dir.mkdir(parents=True, exist_ok=True)
        resolved_target = target_dir.resolve()
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                member_path = (resolved_target / member.name).resolve()
                if not member_path.is_relative_to(resolved_target):
                    raise RuntimeError(f"unsafe archive member detected: {member.name}")
            tar.extractall(path=str(target_dir))

    @staticmethod
    def append_restore_stage(stages: list[dict], name: str, status: str, detail: str) -> dict:
        """Append a restore stage entry in a UI-friendly shape."""
        entry = {"name": name, "status": status, "detail": detail}
        stages.append(entry)
        return entry

    @staticmethod
    def run_systemctl_action(service_name: str, action: str) -> Tuple[bool, str]:
        """Run a systemctl action and return success + readable detail."""
        result = subprocess.run(
            ["systemctl", action, service_name],
            check=False,
            capture_output=True,
            text=True,
        )
        detail = (result.stderr or result.stdout or "").strip()
        if not detail:
            detail = f"systemctl {action} {service_name} -> rc={result.returncode}"
        return result.returncode == 0, detail

    @staticmethod
    def create_restore_point_bundle(pki_dir: str, ccd_dir: str, ccd_legacy_dir: str, ccd_sfos_dir: str, openvpn_conf_path: str, app_env_file: str, app_ui_dir: str, restore_point_dir: str, database_url: str) -> Path:
        """Create a local pre-restore snapshot before applying a bundle."""
        restore_point_root = Path(restore_point_dir)
        restore_point_root.mkdir(parents=True, exist_ok=True)
        bundle_dir = restore_point_root / datetime.now().strftime("pre_restore_%Y%m%d_%H%M%S")
        bundle_dir.mkdir(parents=True, exist_ok=True)

        db_dump_path = bundle_dir / "db.dump"
        dump_command, dump_env = BackupManager.build_pg_dump_command(database_url, db_dump_path)
        subprocess.run(dump_command, check=True, capture_output=True, text=True, env=dump_env)

        archive_targets = [
            ("pki.tar.gz", Path(pki_dir), set()),
            ("ccd.tar.gz", Path(ccd_dir), set()),
            ("ccd_legacy.tar.gz", Path(ccd_legacy_dir), set()),
            ("ccd_sfos.tar.gz", Path(ccd_sfos_dir), set()),
            ("openvpn_conf.tar.gz", Path(openvpn_conf_path).parent, set()),
            ("env.tar.gz", Path(app_env_file), set()),
            ("ui.tar.gz", app_ui_dir, {"node_modules", "release_snapshots", "dist.bak", "dist.pre_rollback", "dist.bad_rollback", "dist.current_wrong", "__pycache__"}),
        ]

        for archive_name, source_path, exclude_names in archive_targets:
            if not source_path.exists():
                continue
            BackupManager.archive_path_to_bundle(bundle_dir, archive_name, source_path, exclude_names=exclude_names)
        return bundle_dir

    @staticmethod
    def apply_restored_backup_bundle(bundle_dir: Path, pki_dir: str, ccd_dir: str, ccd_legacy_dir: str, ccd_sfos_dir: str, openvpn_conf_path: str, app_root_dir: str, app_env_file: str, database_url: str) -> dict:
        """Apply a downloaded backup bundle to the current server."""
        stages = []
        restore_point = None

        stopped_services = [
            "nginx.service",
            "certsvc.service",
            "openvpn-server@server.service",
            "openvpn-server@server-legacy.service",
            "openvpn-server@server-sfos.service",
        ]
        restarted_services = []
        try:
            restore_point = BackupManager.create_restore_point_bundle(
                pki_dir, ccd_dir, ccd_legacy_dir, ccd_sfos_dir, openvpn_conf_path, app_env_file, app_root_dir, "/var/lib/certsvc/restore_point", database_url
            )
            BackupManager.append_restore_stage(stages, "복구 전 자동 백업 생성", "success", str(restore_point))

            for service in stopped_services:
                ok, detail = BackupManager.run_systemctl_action(service, "stop")
                BackupManager.append_restore_stage(stages, f"{service} 중지", "success" if ok else "failed", detail)
                if not ok:
                    raise RuntimeError(f"{service} 중지 실패")

            db_dump_path = bundle_dir / "db.dump"
            restore_command, restore_env = BackupManager.build_pg_restore_command(database_url, db_dump_path)
            subprocess.run(restore_command, check=True, capture_output=True, text=True, env=restore_env)
            BackupManager.append_restore_stage(stages, "DB 복구", "success", str(db_dump_path))

            archive_map = [
                ("pki.tar.gz", Path("/etc/openvpn")),
                ("ccd.tar.gz", Path("/etc/openvpn")),
                ("ccd_legacy.tar.gz", Path("/etc/openvpn")),
                ("ccd_sfos.tar.gz", Path("/etc/openvpn")),
                ("openvpn_conf.tar.gz", Path("/etc/openvpn")),
                ("ui.tar.gz", app_root_dir),
            ]
            for archive_name, target_dir in archive_map:
                archive_path = bundle_dir / archive_name
                if archive_path.exists():
                    BackupManager.safe_extract_archive(archive_path, target_dir)
                    BackupManager.append_restore_stage(stages, f"{archive_name} 적용", "success", f"{archive_path} -> {target_dir}")
                else:
                    BackupManager.append_restore_stage(stages, f"{archive_name} 적용", "skipped", "백업본에 파일이 없어 건너뜀")

            env_archive = bundle_dir / "env.tar.gz"
            if env_archive.exists():
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_root = Path(tmpdir)
                    BackupManager.safe_extract_archive(env_archive, tmp_root)
                    extracted_env = tmp_root / ".env"
                    if extracted_env.exists():
                        shutil.copy2(extracted_env, app_env_file)
                        BackupManager.append_restore_stage(stages, "애플리케이션 설정(.env) 적용", "success", app_env_file)
                    else:
                        BackupManager.append_restore_stage(stages, "애플리케이션 설정(.env) 적용", "failed", "env.tar.gz 내부에 .env 파일이 없습니다.")
                        raise RuntimeError("env.tar.gz 내부에 .env 파일이 없습니다.")
            else:
                BackupManager.append_restore_stage(stages, "애플리케이션 설정(.env) 적용", "skipped", "백업본에 env.tar.gz가 없어 건너뜀")
        except RuntimeError:
            raise
        except Exception as exc:
            BackupManager.append_restore_stage(stages, "복구 적용", "failed", f"{type(exc).__name__}: {exc}")
            raise RuntimeError(f"복구 적용 중 실패: {type(exc).__name__}: {exc}") from exc
        finally:
            for service in [
                "openvpn-server@server.service",
                "openvpn-server@server-legacy.service",
                "openvpn-server@server-sfos.service",
                "certsvc.service",
                "nginx.service",
            ]:
                ok, detail = BackupManager.run_systemctl_action(service, "start")
                BackupManager.append_restore_stage(stages, f"{service} 시작", "success" if ok else "failed", detail)
                restarted_services.append(service)

        return {
            "restorePoint": str(restore_point) if restore_point else "",
            "restartedServices": restarted_services,
            "stages": stages,
        }

    # ========================================
    # Backup Bundle Creation
    # ========================================

    def create_backup_bundle(self, record: Any, pki_dir: str, ccd_dir: str, ccd_legacy_dir: str, ccd_sfos_dir: str, openvpn_conf_path: str, app_env_file: str, app_ui_dir: str, database_url: str) -> Tuple[Path, dict]:
        """Create a local backup bundle directory with DB/config artifacts."""
        backup_id = datetime.now().strftime("backup_%Y%m%d_%H%M%S")
        bundle_dir = self.backup_dir / backup_id
        bundle_dir.mkdir(parents=True, exist_ok=True)

        files = []
        db_dump_path = bundle_dir / "db.dump"
        dump_command, dump_env = self.build_pg_dump_command(database_url, db_dump_path)
        subprocess.run(dump_command, check=True, capture_output=True, text=True, env=dump_env)
        files.append(db_dump_path.name)

        archive_targets = [
            ("pki.tar.gz", Path(pki_dir), set()),
            ("ccd.tar.gz", Path(ccd_dir), set()),
            ("ccd_legacy.tar.gz", Path(ccd_legacy_dir), set()),
            ("ccd_sfos.tar.gz", Path(ccd_sfos_dir), set()),
            ("openvpn_conf.tar.gz", Path(openvpn_conf_path).parent, set()),
            ("env.tar.gz", Path(app_env_file), set()),
            ("ui.tar.gz", app_ui_dir, {"node_modules", "release_snapshots", "dist.bak", "dist.pre_rollback", "dist.bad_rollback", "dist.current_wrong", "__pycache__"}),
        ]

        for archive_name, source_path, exclude_names in archive_targets:
            if not source_path.exists():
                continue
            final_path = self.archive_path_to_bundle(bundle_dir, archive_name, source_path, exclude_names=exclude_names)
            files.append(final_path.name)

        manifest = self.build_backup_manifest(record, backup_id, files)
        manifest_path = bundle_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        files.append(manifest_path.name)
        manifest["files"] = files
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return bundle_dir, manifest

