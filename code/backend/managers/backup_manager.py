"""Backup and restore management."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request
from datetime import datetime, timezone
from ftplib import FTP, all_errors as FTP_ERRORS
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.engine import make_url
from zoneinfo import ZoneInfo


class RestoreExecutionError(RuntimeError):
    """Raised when restore execution fails after partial stage progress."""

    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class BackupManager:
    """Manages backup creation, listing, restore helpers, and history formatting."""

    def __init__(
        self,
        *,
        backup_base_dir: str,
        restore_stage_dir: str = "",
        restore_point_dir: str = "",
        database_url: str = "",
        pki_dir: str = "",
        ccd_dir: str = "",
        ccd_legacy_dir: str = "",
        ccd_sfos_dir: str = "",
        openvpn_server_conf: str = "",
        app_env_file: str = "",
        app_root_dir: str = "",
        app_ui_dir: str = "",
        internal_api_base_url: str = "http://127.0.0.1:8443",
        internal_api_token: str = "",
        runtime_guard_log_file: str = "",
        display_timezone: str = "Asia/Seoul",
    ) -> None:
        self.backup_dir = Path(backup_base_dir)
        self.restore_stage_dir = Path(restore_stage_dir) if restore_stage_dir else None
        self.restore_point_dir = Path(restore_point_dir) if restore_point_dir else None
        self.database_url = database_url
        self.pki_dir = Path(pki_dir) if pki_dir else None
        self.ccd_dir = Path(ccd_dir) if ccd_dir else None
        self.ccd_legacy_dir = Path(ccd_legacy_dir) if ccd_legacy_dir else None
        self.ccd_sfos_dir = Path(ccd_sfos_dir) if ccd_sfos_dir else None
        self.openvpn_server_conf = Path(openvpn_server_conf) if openvpn_server_conf else None
        self.app_env_file = Path(app_env_file) if app_env_file else None
        self.app_root_dir = Path(app_root_dir) if app_root_dir else None
        self.app_ui_dir = Path(app_ui_dir) if app_ui_dir else None
        self.internal_api_base_url = internal_api_base_url.rstrip("/")
        self.internal_api_token = internal_api_token
        self.runtime_guard_log_file = Path(runtime_guard_log_file) if runtime_guard_log_file else None
        self.display_timezone = ZoneInfo(display_timezone or "Asia/Seoul")

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        if self.restore_stage_dir:
            self.restore_stage_dir.mkdir(parents=True, exist_ok=True)
        if self.restore_point_dir:
            self.restore_point_dir.mkdir(parents=True, exist_ok=True)

    def now_kst_display(self) -> str:
        """Return a standard Korea-time display string."""
        return datetime.now(tz=self.display_timezone).strftime("%Y-%m-%d %H:%M:%S KST")

    def list_backups(self) -> list[dict[str, Any]]:
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

    def get_backup_manifest(self, backup_name: str) -> dict[str, Any] | None:
        """Get backup manifest information."""
        backup_path = self.backup_dir / backup_name
        manifest_path = backup_path / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_backup_path(self, backup_name: str) -> Path | None:
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

    def build_backup_manifest(self, record: Any, backup_id: str, files: list[str]) -> dict[str, Any]:
        """Create a manifest matching the API/UI payload format."""
        hostname = os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown")
        return {
            "backupId": backup_id,
            "createdAt": datetime.now(tz=timezone.utc).isoformat(),
            "hostname": hostname,
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

    def validate_restore_backup(self, backup_name: str) -> tuple[bool, str]:
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

    def build_pg_dump_command(self, output_path: Path) -> tuple[list[str], dict[str, str]]:
        """Convert DATABASE_URL into pg_dump CLI arguments."""
        parsed = make_url(self.database_url)
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

    def build_pg_restore_command(self, input_path: Path) -> tuple[list[str], dict[str, str]]:
        """Convert DATABASE_URL into pg_restore CLI arguments."""
        parsed = make_url(self.database_url)
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

    def ensure_ftp_remote_dir(self, ftp: FTP, remote_dir: str) -> str:
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

    def ftp_connect(self, host: str, username: str, password: str, timeout: int = 10) -> FTP:
        """Open and authenticate an FTP session."""
        ftp = FTP()
        ftp.connect(host=host, port=21, timeout=timeout)
        ftp.login(user=username, passwd=password)
        ftp.set_pasv(True)
        return ftp

    def upload_dir_via_ftp(self, ftp: FTP, local_dir: Path, remote_dir: str) -> list[str]:
        """Upload a local directory tree to an FTP directory."""
        uploaded = []
        local_dir = local_dir.resolve()
        for path in sorted(local_dir.rglob("*")):
            rel = path.relative_to(local_dir).as_posix()
            target_parent = self.ensure_ftp_remote_dir(ftp, f"{remote_dir}/{Path(rel).parent.as_posix()}")
            if path.is_dir():
                continue
            ftp.cwd(target_parent)
            with open(path, "rb") as handle:
                ftp.storbinary(f"STOR {Path(rel).name}", handle)
            uploaded.append(rel)
        return uploaded

    def list_ftp_backup_directories(self, ftp: FTP, remote_root: str) -> list[str]:
        """Return backup bundle directory names from an FTP remote path."""
        resolved = self.ensure_ftp_remote_dir(ftp, remote_root)
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

    def download_ftp_backup_bundle(self, ftp: FTP, remote_root: str, backup_id: str) -> tuple[Path, list[str]]:
        """Download one FTP backup bundle directory into local staging."""
        if not self.restore_stage_dir:
            raise RuntimeError("restore staging directory is not configured")
        resolved_root = self.ensure_ftp_remote_dir(ftp, remote_root)
        remote_dir = f"{resolved_root.rstrip('/')}/{backup_id}"
        stage_dir = self.restore_stage_dir / f"{backup_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
                with open(local_path, "wb") as handle:
                    ftp.retrbinary(f"RETR {name}", handle.write)
                downloaded.append(name)
        finally:
            ftp.cwd(current)
        return stage_dir, sorted(downloaded)

    def validate_tar_archive(self, path: Path) -> None:
        """Open a tar archive to verify it is readable."""
        with tarfile.open(path, "r:gz") as tar:
            tar.getmembers()

    def archive_path_to_bundle(
        self,
        bundle_dir: Path,
        archive_name: str,
        source_path: Path,
        *,
        exclude_names: set[str] | None = None,
    ) -> Path:
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

    def validate_downloaded_backup_bundle(self, bundle_dir: Path, downloaded: list[str]) -> dict[str, Any]:
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
        checks.append(
            {
                "name": "manifest 파일 검증",
                "status": "success" if not missing_files else "failed",
                "detail": f"expected={len(expected_files)}, missing={len(missing_files)}",
            }
        )

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
        checks.append({"name": "DB 덤프 검증", "status": "success", "detail": f"pg_restore --list ok ({len(restore_lines)} lines)"})

        validated_archives = []
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
                self.validate_tar_archive(archive_path)
                validated_archives.append(archive_name)
                checks.append({"name": archive_name, "status": "success", "detail": "archive ok"})
            else:
                checks.append({"name": archive_name, "status": "skipped", "detail": "not present"})

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

    def build_local_backup_validation(self, bundle_dir: Path) -> dict[str, Any]:
        """Validate a locally created backup bundle and return a compact status payload."""
        downloaded = sorted(path.name for path in bundle_dir.iterdir() if path.is_file())
        validation = self.validate_downloaded_backup_bundle(bundle_dir, downloaded)
        return {
            "backupId": validation.get("backupId") or bundle_dir.name,
            "valid": bool(validation.get("valid")),
            "missingFiles": validation.get("missingFiles", []),
            "validatedArchives": validation.get("validatedArchives", []),
            "dbObjectCountHint": int(validation.get("dbObjectCountHint", 0) or 0),
            "uiIncluded": bool(
                (validation.get("manifest") or {}).get("uiIncluded")
                or "ui.tar.gz" in (validation.get("expectedFiles") or [])
            ),
            "checkedAt": self.now_kst_display(),
        }

    def create_restore_point_bundle(self) -> Path:
        """Create a local pre-restore snapshot before applying a bundle."""
        if not self.restore_point_dir:
            raise RuntimeError("restore point directory is not configured")
        bundle_dir = self.restore_point_dir / datetime.now().strftime("pre_restore_%Y%m%d_%H%M%S")
        bundle_dir.mkdir(parents=True, exist_ok=True)

        db_dump_path = bundle_dir / "db.dump"
        dump_command, dump_env = self.build_pg_dump_command(db_dump_path)
        subprocess.run(dump_command, check=True, capture_output=True, text=True, env=dump_env)

        archive_targets = [
            ("pki.tar.gz", self.pki_dir, set()),
            ("ccd.tar.gz", self.ccd_dir, set()),
            ("ccd_legacy.tar.gz", self.ccd_legacy_dir, set()),
            ("ccd_sfos.tar.gz", self.ccd_sfos_dir, set()),
            ("openvpn_conf.tar.gz", self.openvpn_server_conf.parent if self.openvpn_server_conf else None, set()),
            ("env.tar.gz", self.app_env_file, set()),
            ("ui.tar.gz", self.app_ui_dir, {"node_modules", "release_snapshots", "dist.bak", "dist.pre_rollback", "dist.bad_rollback", "dist.current_wrong", "__pycache__"}),
        ]

        for archive_name, source_path, exclude_names in archive_targets:
            if not source_path or not source_path.exists():
                continue
            self.archive_path_to_bundle(bundle_dir, archive_name, source_path, exclude_names=exclude_names)
        return bundle_dir

    def safe_extract_archive(self, archive_path: Path, target_dir: Path) -> None:
        """Extract a tar.gz archive after checking for path traversal."""
        target_dir.mkdir(parents=True, exist_ok=True)
        resolved_target = target_dir.resolve()
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                member_path = (resolved_target / member.name).resolve()
                if not member_path.is_relative_to(resolved_target):
                    raise RuntimeError(f"unsafe archive member detected: {member.name}")
            tar.extractall(path=str(target_dir))

    def append_restore_stage(self, stages: list[dict[str, Any]], name: str, status: str, detail: str) -> dict[str, Any]:
        """Append a restore stage entry in a UI-friendly shape."""
        entry = {"name": name, "status": status, "detail": detail}
        stages.append(entry)
        return entry

    def run_systemctl_action(self, service_name: str, action: str) -> tuple[bool, str]:
        """Run a systemctl action and return success plus readable detail."""
        result = subprocess.run(["systemctl", action, service_name], check=False, capture_output=True, text=True)
        detail = (result.stderr or result.stdout or "").strip()
        if not detail:
            detail = f"systemctl {action} {service_name} -> rc={result.returncode}"
        return result.returncode == 0, detail

    def fetch_local_json(self, path: str, timeout: int = 3) -> dict[str, Any] | list[Any]:
        """Call a local API endpoint, using the internal token when available."""
        request = urllib.request.Request(f"{self.internal_api_base_url}{path}")
        if self.internal_api_token:
            request.add_header("X-Internal-Token", self.internal_api_token)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def build_restore_service_checklist(self) -> list[dict[str, Any]]:
        """Check core services and API endpoints after restore."""
        def active(service_name: str) -> bool:
            result = subprocess.run(["systemctl", "is-active", service_name], check=False, capture_output=True, text=True)
            return result.returncode == 0 and result.stdout.strip() == "active"

        checklist = [
            {"name": "postgresql.service", "ok": active("postgresql.service"), "detail": "PostgreSQL 데이터베이스 서비스 상태"},
            {"name": "nginx.service", "ok": active("nginx.service"), "detail": "HTTPS 프록시 및 정적 UI 서비스 상태"},
            {"name": "certsvc.service", "ok": active("certsvc.service"), "detail": "FastAPI 백엔드 서비스 상태"},
            {"name": "openvpn-server@server.service", "ok": active("openvpn-server@server.service"), "detail": "일반 SG OpenVPN 서비스 상태"},
            {"name": "openvpn-server@server-legacy.service", "ok": active("openvpn-server@server-legacy.service"), "detail": "레거시 SG OpenVPN 서비스 상태"},
            {"name": "openvpn-server@server-sfos.service", "ok": active("openvpn-server@server-sfos.service"), "detail": "SFOS OpenVPN 서비스 상태"},
            {"name": "PKI 디렉터리", "ok": bool(self.pki_dir and self.pki_dir.exists()), "detail": str(self.pki_dir or "")},
            {"name": "CCD 디렉터리", "ok": bool(self.ccd_dir and self.ccd_dir.exists()), "detail": str(self.ccd_dir or "")},
            {"name": "CCD Legacy 디렉터리", "ok": bool(self.ccd_legacy_dir and self.ccd_legacy_dir.exists()), "detail": str(self.ccd_legacy_dir or "")},
            {"name": "CCD SFOS 디렉터리", "ok": bool(self.ccd_sfos_dir and self.ccd_sfos_dir.exists()), "detail": str(self.ccd_sfos_dir or "")},
            {"name": ".env 파일", "ok": bool(self.app_env_file and self.app_env_file.exists()), "detail": str(self.app_env_file or "")},
        ]

        health_ok = False
        health_detail = "certsvc health check unavailable"
        for _ in range(5):
            try:
                body = self.fetch_local_json("/health")
                if isinstance(body, dict):
                    health_ok = bool(body.get("database"))
                    health_detail = json.dumps(body, ensure_ascii=False)
                    break
            except Exception as exc:
                health_detail = str(exc)
                threading.Event().wait(1)
        checklist.append({"name": "certsvc /health", "ok": health_ok, "detail": health_detail})

        for path, label in [("/clients", "클라이언트 목록 API"), ("/leases", "IP 임대 목록 API")]:
            ok = False
            detail = "endpoint unavailable"
            for _ in range(5):
                try:
                    body = self.fetch_local_json(path)
                    count = len(body) if isinstance(body, list) else (len(body.get("items", [])) if isinstance(body, dict) else 0)
                    ok = True
                    detail = f"{path} 응답 정상, 항목 수={count}"
                    break
                except Exception as exc:
                    detail = str(exc)
                    threading.Event().wait(1)
            checklist.append({"name": label, "ok": ok, "detail": detail})
        return checklist

    def build_restore_preflight_summary(self, bundle_dir: Path | None, validation: dict[str, Any] | None = None) -> dict[str, Any]:
        """Summarize whether the current host is ready for a restore operation."""
        validation = validation or {}
        if not self.restore_stage_dir:
            raise RuntimeError("restore staging directory is not configured")
        self.restore_stage_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.restore_stage_dir)
        bundle_size_bytes = 0
        if bundle_dir and bundle_dir.exists():
            try:
                bundle_size_bytes = sum(item.stat().st_size for item in bundle_dir.rglob("*") if item.is_file())
            except OSError:
                bundle_size_bytes = 0
        required_free_bytes = max(bundle_size_bytes * 2, 512 * 1024 * 1024)
        service_checklist = self.build_restore_service_checklist()
        failed_checks = [item.get("name") for item in service_checklist if not item.get("ok")]
        ready = bool(validation.get("valid", True)) and usage.free >= required_free_bytes and not failed_checks
        return {
            "ready": ready,
            "checkedAt": self.now_kst_display(),
            "stageDir": str(self.restore_stage_dir),
            "bundleDir": str(bundle_dir) if bundle_dir else "",
            "bundleSizeBytes": bundle_size_bytes,
            "freeBytes": usage.free,
            "requiredFreeBytes": required_free_bytes,
            "serviceChecklistOk": not failed_checks,
            "failedChecks": failed_checks,
            "validationOk": bool(validation.get("valid", True)),
            "missingFiles": validation.get("missingFiles", []),
            "uiIncluded": bool(
                validation.get("uiIncluded")
                or (validation.get("manifest") or {}).get("uiIncluded")
                or "ui.tar.gz" in (validation.get("expectedFiles") or [])
            ),
        }

    def apply_restored_backup_bundle(self, bundle_dir: Path) -> dict[str, Any]:
        """Apply a downloaded backup bundle to the current server."""
        stages: list[dict[str, Any]] = []
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
            restore_point = self.create_restore_point_bundle()
            self.append_restore_stage(stages, "복구 전 자동 백업 생성", "success", str(restore_point))

            for service in stopped_services:
                ok, detail = self.run_systemctl_action(service, "stop")
                self.append_restore_stage(stages, f"{service} 중지", "success" if ok else "failed", detail)
                if not ok:
                    raise RestoreExecutionError(
                        f"{service} 중지 실패",
                        {"restorePoint": str(restore_point), "stages": stages, "restartedServices": restarted_services},
                    )

            db_dump_path = bundle_dir / "db.dump"
            restore_command, restore_env = self.build_pg_restore_command(db_dump_path)
            subprocess.run(restore_command, check=True, capture_output=True, text=True, env=restore_env)
            self.append_restore_stage(stages, "DB 복구", "success", str(db_dump_path))

            archive_map = [
                ("pki.tar.gz", Path("/etc/openvpn")),
                ("ccd.tar.gz", Path("/etc/openvpn")),
                ("ccd_legacy.tar.gz", Path("/etc/openvpn")),
                ("ccd_sfos.tar.gz", Path("/etc/openvpn")),
                ("openvpn_conf.tar.gz", Path("/etc/openvpn")),
                ("ui.tar.gz", self.app_root_dir or Path("/opt/certsvc")),
            ]
            for archive_name, target_dir in archive_map:
                archive_path = bundle_dir / archive_name
                if archive_path.exists():
                    self.safe_extract_archive(archive_path, target_dir)
                    self.append_restore_stage(stages, f"{archive_name} 적용", "success", f"{archive_path} -> {target_dir}")
                else:
                    self.append_restore_stage(stages, f"{archive_name} 적용", "skipped", "백업본에 파일이 없어 건너뜀")

            env_archive = bundle_dir / "env.tar.gz"
            if env_archive.exists():
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_root = Path(tmpdir)
                    self.safe_extract_archive(env_archive, tmp_root)
                    extracted_env = tmp_root / ".env"
                    if extracted_env.exists() and self.app_env_file:
                        shutil.copy2(extracted_env, self.app_env_file)
                        self.append_restore_stage(stages, "애플리케이션 설정(.env) 적용", "success", str(self.app_env_file))
                    else:
                        self.append_restore_stage(stages, "애플리케이션 설정(.env) 적용", "failed", "env.tar.gz 내부에 .env 파일이 없습니다.")
                        raise RestoreExecutionError(
                            "env.tar.gz 내부에 .env 파일이 없습니다.",
                            {"restorePoint": str(restore_point), "stages": stages, "restartedServices": restarted_services},
                        )
            else:
                self.append_restore_stage(stages, "애플리케이션 설정(.env) 적용", "skipped", "백업본에 env.tar.gz가 없어 건너뜀")
        except RestoreExecutionError:
            raise
        except Exception as exc:
            self.append_restore_stage(stages, "복구 적용", "failed", f"{type(exc).__name__}: {exc}")
            raise RestoreExecutionError(
                f"복구 적용 중 실패: {type(exc).__name__}: {exc}",
                {"restorePoint": str(restore_point) if restore_point else "", "stages": stages, "restartedServices": restarted_services},
            ) from exc
        finally:
            for service in [
                "openvpn-server@server.service",
                "openvpn-server@server-legacy.service",
                "openvpn-server@server-sfos.service",
                "certsvc.service",
                "nginx.service",
            ]:
                ok, detail = self.run_systemctl_action(service, "start")
                self.append_restore_stage(stages, f"{service} 시작", "success" if ok else "failed", detail)
                restarted_services.append(service)

        service_checklist = self.build_restore_service_checklist()
        return {
            "restorePoint": str(restore_point) if restore_point else "",
            "restartedServices": restarted_services,
            "stages": stages,
            "serviceChecklist": service_checklist,
            "serviceChecklistOk": all(item.get("ok") for item in service_checklist),
        }

    def create_backup_bundle(self, record: Any) -> tuple[Path, dict[str, Any]]:
        """Create a local backup bundle directory with DB/config artifacts."""
        backup_id = datetime.now().strftime("backup_%Y%m%d_%H%M%S")
        bundle_dir = self.backup_dir / backup_id
        bundle_dir.mkdir(parents=True, exist_ok=True)

        files = []
        db_dump_path = bundle_dir / "db.dump"
        dump_command, dump_env = self.build_pg_dump_command(db_dump_path)
        subprocess.run(dump_command, check=True, capture_output=True, text=True, env=dump_env)
        files.append(db_dump_path.name)

        archive_targets = [
            ("pki.tar.gz", self.pki_dir, set()),
            ("ccd.tar.gz", self.ccd_dir, set()),
            ("ccd_legacy.tar.gz", self.ccd_legacy_dir, set()),
            ("ccd_sfos.tar.gz", self.ccd_sfos_dir, set()),
            ("openvpn_conf.tar.gz", self.openvpn_server_conf.parent if self.openvpn_server_conf else None, set()),
            ("env.tar.gz", self.app_env_file, set()),
            ("ui.tar.gz", self.app_ui_dir, {"node_modules", "release_snapshots", "dist.bak", "dist.pre_rollback", "dist.bad_rollback", "dist.current_wrong", "__pycache__"}),
        ]
        for archive_name, source_path, exclude_names in archive_targets:
            if not source_path or not source_path.exists():
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

    def run_backup_test_connection(self, *, host: str, username: str, password: str, remote_path: str) -> dict[str, Any]:
        """Validate FTP connectivity with the current form values before saving."""
        if not host or not username or not password:
            raise RuntimeError("ftp host, username, and password are required")
        ftp = self.ftp_connect(host, username, password)
        try:
            resolved = self.ensure_ftp_remote_dir(ftp, remote_path)
            listing = ftp.nlst()[:10]
        finally:
            try:
                ftp.quit()
            except FTP_ERRORS:
                ftp.close()
        return {"ok": True, "remotePath": resolved, "sample": listing}

    def run_backup_job(
        self,
        db: Any,
        record: Any,
        *,
        decrypt_secret: Callable[[str], str],
        append_log: Callable[..., dict[str, Any]],
        send_slack_message: Callable[[Any, str, dict[str, Any] | None], Any],
        trigger: str = "manual",
    ) -> dict[str, Any]:
        """Create and upload a backup bundle using current persisted settings."""
        host = (record.ftp_host or "").strip()
        username = (record.ftp_username or "").strip()
        remote_path = (record.ftp_remote_path or "").strip() or "/"
        if not host or not username or not record.ftp_password_enc:
            raise RuntimeError("backup settings are incomplete")

        password = decrypt_secret(record.ftp_password_enc)
        bundle_dir: Path | None = None
        manifest: dict[str, Any] = {}
        uploaded: list[str] = []
        validation: dict[str, Any] = {}
        try:
            bundle_dir, manifest = self.create_backup_bundle(record)
            validation = self.build_local_backup_validation(bundle_dir)
            append_log(
                db,
                record,
                job_type="backup_verify",
                status="success" if validation.get("valid") else "failed",
                trigger=trigger,
                message="백업 자동 검증 완료" if validation.get("valid") else "백업 자동 검증 실패",
                detail=json.dumps(validation, ensure_ascii=False, indent=2),
            )
            if not validation.get("valid"):
                raise RuntimeError(f"backup verification failed: {validation.get('missingFiles', [])}")

            ftp = self.ftp_connect(host, username, password)
            try:
                remote_dir = f"{remote_path.rstrip('/')}/{bundle_dir.name}" if remote_path.strip() else f"/{bundle_dir.name}"
                uploaded = self.upload_dir_via_ftp(ftp, bundle_dir, remote_dir)
            finally:
                try:
                    ftp.quit()
                except FTP_ERRORS:
                    ftp.close()
        except (FTP_ERRORS, OSError, TimeoutError, ConnectionError, EOFError, RuntimeError, subprocess.SubprocessError) as exc:
            append_log(
                db,
                record,
                job_type="backup",
                status="failed",
                trigger=trigger,
                message="백업 실행 실패",
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            self.cleanup_old_backups(keep_count=5)

        log_entry = append_log(
            db,
            record,
            job_type="backup",
            status="success",
            trigger=trigger,
            message=f"백업 실행 완료 ({bundle_dir.name})",
            detail=json.dumps({"backupId": bundle_dir.name, "uploaded": uploaded, "manifest": manifest, "validation": validation}, ensure_ascii=False, indent=2),
        )
        if record.slack_notify_backup_completed:
            send_slack_message(record, "backup_completed", None)
        return {"backupId": bundle_dir.name, "uploaded": uploaded, "manifest": manifest, "validation": validation, "log": log_entry}

    def run_restore_job(
        self,
        db: Any,
        *,
        host: str,
        username: str,
        password: str,
        remote_path: str,
        backup_id: str,
        mode: str,
        record: Any,
        append_log: Callable[..., dict[str, Any]],
        send_slack_message: Callable[[Any, str, dict[str, Any] | None], Any],
    ) -> dict[str, Any]:
        """Download a selected backup from FTP and validate or restore it."""
        if mode not in {"validate", "restore"}:
            raise RuntimeError("mode must be validate or restore")
        if not host or not username or not password or not backup_id:
            raise RuntimeError("ftp host, username, password, and backupId are required")

        ftp = self.ftp_connect(host, username, password)
        try:
            bundle_dir, downloaded = self.download_ftp_backup_bundle(ftp, remote_path, backup_id)
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

        validation = self.validate_downloaded_backup_bundle(bundle_dir, downloaded)
        preflight = self.build_restore_preflight_summary(bundle_dir, validation)
        append_log(
            db,
            record,
            job_type="restore_preflight",
            status="success" if preflight.get("ready") else "failed",
            trigger="manual",
            message="복구 사전 점검 완료" if preflight.get("ready") else "복구 사전 점검 실패",
            detail=json.dumps(preflight, ensure_ascii=False, indent=2),
        )

        result = {"mode": mode, "validation": validation, "preflight": preflight}
        if mode == "restore":
            if not validation.get("valid"):
                missing = validation.get("missingFiles", [])
                raise RuntimeError(f"백업 검증 실패: 누락 파일 {missing} — 불완전한 백업은 복구할 수 없습니다.")
            if not preflight.get("ready"):
                raise RuntimeError(
                    f"복구 사전 점검 실패: {', '.join(preflight.get('failedChecks', [])) or 'free space or validation issue'}"
                )
            try:
                result["restore"] = self.apply_restored_backup_bundle(bundle_dir)
            except RestoreExecutionError as exc:
                failed_result = {"mode": mode, "validation": validation, "restore": exc.payload}
                append_log(
                    db,
                    record,
                    job_type="restore",
                    status="failed",
                    trigger="manual",
                    message="백업 복구 실패",
                    detail=json.dumps(failed_result, ensure_ascii=False, indent=2),
                )
                if record.slack_notify_backup_completed:
                    send_slack_message(
                        record,
                        "restore_failed",
                        {
                            "backupId": validation.get("backupId") or backup_id,
                            "modeLabel": "복구모드",
                            "error": str(exc),
                            "restorePoint": (exc.payload or {}).get("restorePoint", "-"),
                            "failedStage": next(
                                (
                                    stage.get("name")
                                    for stage in reversed((exc.payload or {}).get("stages", []))
                                    if stage.get("status") == "failed"
                                ),
                                "-",
                            ),
                        },
                    )
                raise

        append_log(
            db,
            record,
            job_type="restore_validate" if mode == "validate" else "restore",
            status="success",
            trigger="manual",
            message="백업 검증 완료" if mode == "validate" else "백업 복구 완료",
            detail=json.dumps(result, ensure_ascii=False, indent=2),
        )
        if record.slack_notify_backup_completed:
            if mode == "validate":
                send_slack_message(
                    record,
                    "restore_validated",
                    {
                        "backupId": validation.get("backupId") or backup_id,
                        "dbObjectCountHint": validation.get("dbObjectCountHint", 0),
                        "missingFileCount": len(validation.get("missingFiles", [])),
                    },
                )
            else:
                restore_result = result.get("restore") or {}
                send_slack_message(
                    record,
                    "restore_completed",
                    {
                        "backupId": validation.get("backupId") or backup_id,
                        "restorePoint": restore_result.get("restorePoint", "-"),
                        "stageCount": len(restore_result.get("stages", [])),
                        "serviceChecklistOk": bool(restore_result.get("serviceChecklistOk")),
                    },
                )

        try:
            shutil.rmtree(bundle_dir, ignore_errors=True)
        except Exception:
            pass
        return result
