import base64
import glob
import hashlib
import hmac
import ipaddress
import io
import json
import logging
import os
import psutil
import re
import secrets
import string
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from ftplib import FTP, all_errors as FTP_ERRORS
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

import models
from db import Base as DBBase, engine as DB_ENGINE, get_db
from managers import AlertManager, MonitoringManager, SecurityManager, IPLeaseManager, EnrollManager, VPNConfigManager, BackupManager, EquipmentAssetManager, OfflineQueueManager, RestoreExecutionError
from managers.security_manager import RateLimitExceeded
from schemas import (
    EnrollPayload, EnrollApcPayload, AdminApcPayload,
    SecuritySettingsPayload, SecurityUnbanPayload, SecurityUnenrollPayload,
    DebugAccessPayload, WebLoginPayload,
    BackupSettingsPayload, BackupRunPayload, RestoreBrowsePayload, RestoreRunPayload,
    SlackSettingsPayload, SlackTestPayload,
    InventorySyncPayload, InventorySyncTriggerPayload,
    EquipmentAssetManualCreatePayload,
)
from utils.cert_utils import get_certificate_expire_at as get_certificate_expire_at_util
from utils.network_utils import canonical_ip as canonical_ip_util
from utils.time_utils import (
    format_display_datetime as format_display_datetime_util,
    now_in_timezone as now_in_timezone_util,
)

app = FastAPI(
    title="SSLAuthenticator",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
LOGGER = logging.getLogger("certsvc")
LOGGER.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# Initialize MonitoringManager
MONITOR = MonitoringManager(config={
    "cpu_threshold": 90,
    "memory_threshold": 90,
    "disk_threshold": 90,
})

# Initialize IPLeaseManager
IP_LEASE_MGR = IPLeaseManager()

BACKUP_LOCK = threading.Lock()
BACKUP_SCHEDULER_THREAD = None
ALERT_MONITOR_THREAD = None
ALERT_MONITOR_INTERVAL_SECONDS = int(os.environ.get("ALERT_MONITOR_INTERVAL_SECONDS", "30"))
CERTIFICATE_EXPIRY_ALERT_DAYS = int(os.environ.get("CERTIFICATE_EXPIRY_ALERT_DAYS", "30"))
RUNTIME_GUARD_LOG_FILE = os.environ.get("RUNTIME_GUARD_LOG_FILE", "/var/log/certsvc-runtime-guard.log")

PKI = os.environ.get("PKI_DIR", "/etc/openvpn/pki")
SECRET = os.environ.get("ENROLL_SECRET")
if not SECRET:
    raise RuntimeError("ENROLL_SECRET must be set")

ALLOWED_DRIFT_SECONDS = int(os.environ.get("ENROLL_TS_DRIFT_SECONDS", "300"))
BACKUP_UPLOAD_MAX_BYTES = int(os.environ.get("BACKUP_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024)))
BACKUP_UPLOAD_REPLAY_TTL_SECONDS = int(os.environ.get("BACKUP_UPLOAD_REPLAY_TTL_SECONDS", str(ALLOWED_DRIFT_SECONDS + 60)))
BACKUP_UPLOAD_HMAC_KEY_ID = os.environ.get("BACKUP_UPLOAD_HMAC_KEY_ID", "global-v1").strip() or "global-v1"
BACKUP_UPLOAD_REPLAY_CACHE: dict[str, float] = {}
BACKUP_UPLOAD_REPLAY_LOCK = threading.Lock()

CCD = os.environ.get("CCD_DIR", "/etc/openvpn/ccd")
CCD_LEGACY = os.environ.get("CCD_LEGACY_DIR", "/etc/openvpn/ccd-legacy")
CCD_SFOS = os.environ.get("CCD_SFOS_DIR", "/etc/openvpn/ccd-sfos")
OPENVPN_SERVER_IP = os.environ.get("OPENVPN_SERVER_IP", "127.0.0.1")
OPENVPN_PORT = int(os.environ.get("OPENVPN_PORT", "1194"))
OPENVPN_LEGACY_PORT = int(os.environ.get("OPENVPN_LEGACY_PORT", "1195"))
OPENVPN_CIDR = os.environ.get("OPENVPN_CIDR", "172.23.208.0/22")
OPENVPN_LEGACY_CIDR = os.environ.get("OPENVPN_LEGACY_CIDR", "172.23.212.0/22")
OPENVPN_NETMASK = os.environ.get("OPENVPN_NETMASK", "255.255.252.0")
OPENVPN_TUN_SERIAL_IP = os.environ.get("OPENVPN_TUN_SERIAL_IP", "10.242.254.1")
OPENVPN_GATEWAY_IP = os.environ.get("OPENVPN_GATEWAY_IP", OPENVPN_TUN_SERIAL_IP)
OPENVPN_LEGACY_TUN_SERIAL_IP = os.environ.get("OPENVPN_LEGACY_TUN_SERIAL_IP", "10.242.253.1")
OPENVPN_LEGACY_GATEWAY_IP = os.environ.get("OPENVPN_LEGACY_GATEWAY_IP", OPENVPN_LEGACY_TUN_SERIAL_IP)
OPENVPN_PUSH_REMOTE_NETWORK_1 = os.environ.get("OPENVPN_PUSH_REMOTE_NETWORK_1", "10.0.200.4")
SFOS_SERVER_IP = os.environ.get("SFOS_SERVER_IP", "127.0.0.1")
SFOS_VPN_PORT = int(os.environ.get("SFOS_VPN_PORT", "4443"))
SFOS_CIDR = os.environ.get("SFOS_CIDR", "172.23.220.0/22")
SFOS_NETMASK = os.environ.get("SFOS_NETMASK", "255.255.252.0")
SFOS_TUN_SERIAL_IP = os.environ.get("SFOS_TUN_SERIAL_IP", "10.242.255.1")
SFOS_GATEWAY_IP = os.environ.get("SFOS_GATEWAY_IP", SFOS_TUN_SERIAL_IP)
OPENVPN_SERVER_CONF = os.environ.get("OPENVPN_SERVER_CONF", "/etc/openvpn/server/server.conf")
OPENVPN_LEGACY_SERVER_CONF = os.environ.get("OPENVPN_LEGACY_SERVER_CONF", "/etc/openvpn/server/server-legacy.conf")
SFOS_SERVER_CONF = os.environ.get("SFOS_SERVER_CONF", "/etc/openvpn/server/server-sfos.conf")
OPENVPN_STATUS_FILE = os.environ.get("OPENVPN_STATUS_FILE", "/run/openvpn-server/status-server.log")
OPENVPN_LEGACY_STATUS_FILE = os.environ.get("OPENVPN_LEGACY_STATUS_FILE", "/var/log/openvpn/status-legacy.log")
SFOS_STATUS_FILE = os.environ.get("SFOS_STATUS_FILE", "/run/openvpn-server/status-server-sfos.log")
SERVER_DN = os.environ.get("SERVER_DN", "CN=OpenVPN-CA")
SFOS_SERVER_DN = os.environ.get("SFOS_SERVER_DN", "CN=server")
ENROLL_SCRIPT_SERVER_IP = os.environ.get("ENROLL_SCRIPT_SERVER_IP", "").strip()
ENROLL_PUBLIC_BASE_URL = os.environ.get("ENROLL_PUBLIC_BASE_URL", "").strip().rstrip("/")
SERVER_DOMAIN = os.environ.get("SERVER_DOMAIN", "").strip()
if not ENROLL_PUBLIC_BASE_URL and SERVER_DOMAIN:
    ENROLL_PUBLIC_BASE_URL = f"https://{SERVER_DOMAIN}"
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
INTERNAL_API_ALLOWED_CIDRS = [
    cidr.strip()
    for cidr in os.environ.get(
        "INTERNAL_API_ALLOWED_CIDRS",
        "127.0.0.1/32,10.10.105.0/24,10.0.253.0/24,10.242.2.0/24",
    ).split(",")
    if cidr.strip()
]
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "").strip()
DEBUG_MODE_PASSWORD = os.environ.get("DEBUG_MODE_PASSWORD", "").strip()
INVENTORY_SYNC_PUSH_URL = os.environ.get("INVENTORY_SYNC_PUSH_URL", "").strip()
INVENTORY_SYNC_AUTH_TOKEN = os.environ.get("INVENTORY_SYNC_AUTH_TOKEN", "").strip()
INVENTORY_SYNC_CALLBACK_TOKEN = os.environ.get("INVENTORY_SYNC_CALLBACK_TOKEN", "").strip() or INTERNAL_API_TOKEN
INVENTORY_SYNC_TIMEOUT_SECONDS = int(os.environ.get("INVENTORY_SYNC_TIMEOUT_SECONDS", "10"))
CERTSVC_FEATURE_LOG_DIR = Path(os.environ.get("CERTSVC_FEATURE_LOG_DIR", "/var/log"))
INVENTORY_SYNC_LOG_FILE = os.environ.get("INVENTORY_SYNC_LOG_FILE", "/var/log/certsvc-inventory-sync.log").strip() or "/var/log/certsvc-inventory-sync.log"
ENROLL_LOG_FILE = os.environ.get("ENROLL_LOG_FILE", str(CERTSVC_FEATURE_LOG_DIR / "certsvc-enroll.log")).strip() or str(CERTSVC_FEATURE_LOG_DIR / "certsvc-enroll.log")
APC_LOG_FILE = os.environ.get("APC_LOG_FILE", str(CERTSVC_FEATURE_LOG_DIR / "certsvc-apc.log")).strip() or str(CERTSVC_FEATURE_LOG_DIR / "certsvc-apc.log")
ADMIN_APC_LOG_FILE = os.environ.get("ADMIN_APC_LOG_FILE", str(CERTSVC_FEATURE_LOG_DIR / "certsvc-admin-apc.log")).strip() or str(CERTSVC_FEATURE_LOG_DIR / "certsvc-admin-apc.log")
AUTH_LOG_FILE = os.environ.get("AUTH_LOG_FILE", str(CERTSVC_FEATURE_LOG_DIR / "certsvc-auth.log")).strip() or str(CERTSVC_FEATURE_LOG_DIR / "certsvc-auth.log")
SECURITY_LOG_FILE = os.environ.get("SECURITY_LOG_FILE", str(CERTSVC_FEATURE_LOG_DIR / "certsvc-security.log")).strip() or str(CERTSVC_FEATURE_LOG_DIR / "certsvc-security.log")
BACKUP_FEATURE_LOG_FILE = os.environ.get("BACKUP_FEATURE_LOG_FILE", str(CERTSVC_FEATURE_LOG_DIR / "certsvc-backup.log")).strip() or str(CERTSVC_FEATURE_LOG_DIR / "certsvc-backup.log")
FEATURE_LOG_FILES = {
    "inventory-sync": INVENTORY_SYNC_LOG_FILE,
    "enroll": ENROLL_LOG_FILE,
    "apc": APC_LOG_FILE,
    "admin-apc": ADMIN_APC_LOG_FILE,
    "auth": AUTH_LOG_FILE,
    "security": SECURITY_LOG_FILE,
    "backup": BACKUP_FEATURE_LOG_FILE,
}
INVENTORY_SYNC_ERROR_TYPES = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
    OSError,
    ValueError,
)
BACKUP_IO_ERROR_TYPES = FTP_ERRORS + (
    OSError,
    TimeoutError,
    ConnectionError,
    EOFError,
    RuntimeError,
    subprocess.SubprocessError,
)
INVENTORY_SYNC_DRY_RUN = os.environ.get("INVENTORY_SYNC_DRY_RUN", "1").strip() != "0"
DISPLAY_TIMEZONE = ZoneInfo(os.environ.get("DISPLAY_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul")
TRUST_PROXY_CIDRS = [
    cidr.strip()
    for cidr in os.environ.get("TRUST_PROXY_CIDRS", "127.0.0.1/32,::1/128").split(",")
    if cidr.strip()
]
SECURITY_STATE_FILE = os.environ.get("SECURITY_STATE_FILE", "/opt/certsvc/state/security_monitor.json")
SECURITY_WEBHOOK_URL = os.environ.get("SECURITY_WEBHOOK_URL", "").strip()
SECURITY_SLACK_DEFAULT_CHANNEL = os.environ.get("SECURITY_SLACK_DEFAULT_CHANNEL", "#01-alert").strip() or "#01-alert"
SECURITY_MONITOR_WINDOW_SECONDS = int(os.environ.get("SECURITY_MONITOR_WINDOW_SECONDS", "600"))
SECURITY_MONITOR_ALERT_THRESHOLD = int(os.environ.get("SECURITY_MONITOR_ALERT_THRESHOLD", "5"))
SECURITY_MONITOR_BAN_THRESHOLD = int(os.environ.get("SECURITY_MONITOR_BAN_THRESHOLD", "10"))
SECURITY_DAILY_SUMMARY_ENABLED = os.environ.get("SECURITY_DAILY_SUMMARY_ENABLED", "0").strip() == "1"
SECURITY_DAILY_SUMMARY_HOUR = int(os.environ.get("SECURITY_DAILY_SUMMARY_HOUR", "8"))
SECURITY_DAILY_SUMMARY_MIN_OVERFLOW = int(os.environ.get("SECURITY_DAILY_SUMMARY_MIN_OVERFLOW", "10"))
SECURITY_DAILY_SUMMARY_TIMEZONE = os.environ.get("SECURITY_DAILY_SUMMARY_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul"
SECURITY_MONITOR_MONITORED_PATHS = {"/enroll"}
BACKUP_BASE_DIR = os.environ.get("BACKUP_BASE_DIR", "/opt/certsvc/backups")
CLIENT_BACKUP_BASE_DIR = os.environ.get("CLIENT_BACKUP_BASE_DIR", f"{BACKUP_BASE_DIR}/client_backups")
SFOS_BACKUP_DIR_CANDIDATES = [
    path.strip()
    for path in os.environ.get(
        "SFOS_BACKUP_DIR_CANDIDATES",
        "/var/conf/backupdata,/_conf/backupdata,/content/conf/backupdata",
    ).split(",")
    if path.strip()
]
RESTORE_STAGE_DIR = os.environ.get("RESTORE_STAGE_DIR", "/opt/certsvc/restore_staging")
RESTORE_POINT_DIR = os.environ.get("RESTORE_POINT_DIR", "/opt/certsvc/restore_points")
APP_ENV_FILE = os.environ.get("APP_ENV_FILE", "/opt/certsvc/.env")
APP_ROOT_DIR = Path("/opt/certsvc")
APP_UI_DIR = APP_ROOT_DIR / "ui"
BACKUP_CONFIG_SECRET = os.environ.get("BACKUP_CONFIG_SECRET", SECRET).strip()
SLACK_ALERT_BRAND = os.environ.get("SLACK_ALERT_BRAND", "SSL 인증서 관리").strip() or "SSL 인증서 관리"
WEB_LOGIN_USERNAME = os.environ.get("WEB_LOGIN_USERNAME", "admin").strip() or "admin"
WEB_LOGIN_PASSWORD_HASH = os.environ.get("WEB_LOGIN_PASSWORD_HASH", "").strip()
WEB_LOGIN_PASSWORD = os.environ.get("WEB_LOGIN_PASSWORD", "").strip()
WEB_SESSION_COOKIE_NAME = os.environ.get("WEB_SESSION_COOKIE_NAME", "certsvc_session").strip() or "certsvc_session"
WEB_SESSION_SECRET = (
    os.environ.get("WEB_SESSION_SECRET", "").strip()
    or INTERNAL_API_TOKEN
    or SECRET
)
WEB_SESSION_TTL_SECONDS = int(os.environ.get("WEB_SESSION_TTL_SECONDS", "28800"))
WEB_LOGIN_MAX_ATTEMPTS = int(os.environ.get("WEB_LOGIN_MAX_ATTEMPTS", "5"))
WEB_LOGIN_WINDOW_SECONDS = int(os.environ.get("WEB_LOGIN_WINDOW_SECONDS", "600"))

HOSTNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
WEB_LOGIN_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
SERIAL_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,63}$")
DEVICE_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/+-]{0,127}$")

# Initialize SecurityManager after all config is loaded
SECURITY = SecurityManager(secret=WEB_SESSION_SECRET)

# Initialize EnrollManager
ENROLL_MGR = EnrollManager(pki_dir=PKI, secret=SECRET)

# Initialize VPNConfigManager
VPN_CFG_MGR = VPNConfigManager(
    ccd_dir=CCD,
    ccd_legacy_dir=CCD_LEGACY,
    ccd_sfos_dir=CCD_SFOS,
    openvpn_conf_path=OPENVPN_SERVER_CONF
)

# Initialize BackupManager
BACKUP_MGR = BackupManager(backup_base_dir=BACKUP_BASE_DIR, cipher_secret=BACKUP_CONFIG_SECRET)

# Initialize OfflineQueueManager
OFFLINE_QUEUE_MGR = OfflineQueueManager(queue_dir="/opt/certsvc/offline-queue")

# Initialize AlertManager
ALERT_MGR = AlertManager(
    state_file=SECURITY_STATE_FILE,
    runtime_guard_log_file=RUNTIME_GUARD_LOG_FILE,
    monitor_window_seconds=SECURITY_MONITOR_WINDOW_SECONDS,
    alert_threshold=SECURITY_MONITOR_ALERT_THRESHOLD,
    ban_threshold=SECURITY_MONITOR_BAN_THRESHOLD,
    daily_summary_enabled=SECURITY_DAILY_SUMMARY_ENABLED,
    daily_summary_hour=SECURITY_DAILY_SUMMARY_HOUR,
    daily_summary_min_overflow=SECURITY_DAILY_SUMMARY_MIN_OVERFLOW,
    daily_summary_timezone=SECURITY_DAILY_SUMMARY_TIMEZONE,
    cert_expiry_alert_days=CERTIFICATE_EXPIRY_ALERT_DAYS,
)

# Initialize EquipmentAssetManager
EQUIPMENT_MGR = EquipmentAssetManager()

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
TEMPLATES_DIR = Path("/opt/certsvc/templates")
VPN_ENROLL_TEMPLATE_PATH = TEMPLATES_DIR / "vpn_enroll.sh"
EQUIPMENT_ASSETS_TEMPLATE_PATH = TEMPLATES_DIR / "equipment_assets_template.xlsx"

# File-based template is loaded from /opt/certsvc/templates/vpn_enroll.sh at runtime.

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Content-Type",
        "X-Admin-Token",
        "X-Internal-Token",
        "X-Inventory-Sync-Token",
        "X-Enroll-Token",
    ],
)

SECURITY_RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' http: https: ws: wss:; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Apply baseline browser security headers to all responses."""
    response = await call_next(request)
    for header, value in SECURITY_RESPONSE_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.url.path.startswith(("/auth/", "/backup/", "/security/")):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Pragma", "no-cache")
    return response



def load_template_text(template_path: Path) -> str:
    """Load UTF-8 template text from disk."""
    try:
        return template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"template not found or unreadable: {template_path}") from exc


def render_template_text(template_path: Path, replacements: dict[str, str]) -> str:
    """Render a text template using plain placeholder replacements."""
    content = load_template_text(template_path)
    for key, value in replacements.items():
        content = content.replace(key, value)
    return content


def sign(msg: str) -> str:
    """Return HMAC-SHA256 signature for a message using ENROLL_SECRET."""
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


def normalize_hostname(hostname: str) -> str:
    """Validate and normalize hostname input."""
    value = hostname.strip()
    if not HOSTNAME_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid hostname format")
    return value


def normalize_mac(mac: str | None) -> str:
    """Normalize MAC to lowercase and validate its format."""
    value = (mac or "").strip().lower()
    if not value:
        return ""
    if not MAC_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid mac format")
    return value


def normalize_serial_number(serial_number: str | None) -> str:
    """Normalize equipment serial numbers to an uppercase safe token."""
    value = (serial_number or "").strip().upper()
    if not value:
        return ""
    if not SERIAL_NUMBER_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid serial number format")
    return value


def normalize_device_model(device_model: str | None) -> str:
    """Normalize device model text while preserving vendor spacing."""
    value = " ".join((device_model or "").strip().split())
    if not value:
        return ""
    if not DEVICE_MODEL_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid device model format")
    return value


def normalize_login_username(username: str) -> str:
    """Validate login username against a safe, small character set."""
    value = (username or "").strip()
    if not WEB_LOGIN_USERNAME_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid username format")
    return value


def normalize_login_password(password: str) -> str:
    """Reject obviously malformed login passwords before verification."""
    value = str(password or "")
    if not value or len(value) > 256:
        raise HTTPException(status_code=400, detail="invalid password format")
    return value


def verify_pbkdf2_password(password: str, encoded: str) -> bool:
    """Verify `pbkdf2_sha256$iterations$salt$hash` encoded password strings."""
    return SECURITY.verify_pbkdf2_password(password, encoded)


def verify_web_login_password(password: str) -> bool:
    """Verify web login password using only dedicated web login credentials."""
    if WEB_LOGIN_PASSWORD_HASH:
        if verify_pbkdf2_password(password, WEB_LOGIN_PASSWORD_HASH):
            return True
        if WEB_LOGIN_PASSWORD_HASH.startswith("sha256$"):
            expected = WEB_LOGIN_PASSWORD_HASH.split("$", 1)[1]
            digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
            return hmac.compare_digest(digest, expected)
        return False
    if not WEB_LOGIN_PASSWORD:
        return False
    return hmac.compare_digest(password, WEB_LOGIN_PASSWORD)


def get_forwarded_proto(request: Request) -> str:
    """Resolve forwarded proto only when the direct client is a trusted reverse proxy."""
    direct_host = request.client.host if request.client else ""
    forwarded_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
    if forwarded_proto and client_ip_allowed(direct_host, TRUST_PROXY_CIDRS):
        return forwarded_proto
    return (request.url.scheme or "").lower()


def session_cookie_secure(request: Request) -> bool:
    """Return True when the current request is effectively HTTPS."""
    return get_forwarded_proto(request) == "https"


def sign_session_payload(payload_b64: str) -> str:
    """Return HMAC signature for a session payload."""
    return SECURITY.sign(payload_b64)


def build_session_cookie(username: str) -> str:
    """Build a signed session cookie payload with expiry."""
    return SECURITY.build_session_cookie(username, ttl_seconds=WEB_SESSION_TTL_SECONDS)


def parse_session_cookie(cookie_value: str | None) -> dict | None:
    """Parse and validate the signed session cookie."""
    return SECURITY.parse_session_cookie(cookie_value)


def clear_expired_login_attempts(source_ip: str) -> list[float]:
    """Return recent login failures for an IP after pruning stale entries."""
    return SECURITY.clear_expired_login_attempts(source_ip, WEB_LOGIN_WINDOW_SECONDS)


def ensure_login_rate_limit(source_ip: str) -> None:
    """Throttle repeated failed login attempts from the same IP."""
    try:
        SECURITY.check_login_rate_limit(source_ip, WEB_LOGIN_MAX_ATTEMPTS, WEB_LOGIN_WINDOW_SECONDS)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="too many login attempts") from exc


def register_login_failure(source_ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    SECURITY.register_login_failure(source_ip)


def clear_login_failures(source_ip: str) -> None:
    """Reset failed login history after a successful login."""
    SECURITY.clear_login_attempts(source_ip)


def require_web_session(request: Request) -> dict:
    """Require a valid authenticated web session cookie."""
    claims = parse_session_cookie(request.cookies.get(WEB_SESSION_COOKIE_NAME))
    if not claims:
        raise HTTPException(status_code=401, detail="login required")
    return claims


def require_web_or_internal_access(
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict | None:
    """Allow access from either authenticated web sessions or trusted internal callers."""
    claims = parse_session_cookie(request.cookies.get(WEB_SESSION_COOKIE_NAME))
    if claims:
        return claims
    client_host = resolve_request_ip(request)
    if client_ip_allowed(client_host):
        return None
    if INTERNAL_API_TOKEN and hmac.compare_digest((x_internal_token or "").strip(), INTERNAL_API_TOKEN):
        return None
    raise HTTPException(status_code=401, detail="login required")


def resolve_enroll_script_target(request: Request | None = None) -> tuple[str, str, str]:
    """Resolve the externally visible enroll base URL while keeping a concrete IP for firewall rules."""
    script_server_ip = ENROLL_SCRIPT_SERVER_IP
    if not script_server_ip:
        if OPENVPN_SERVER_IP and OPENVPN_SERVER_IP != "127.0.0.1":
            script_server_ip = OPENVPN_SERVER_IP
        elif SFOS_SERVER_IP and SFOS_SERVER_IP != "127.0.0.1":
            script_server_ip = SFOS_SERVER_IP
        else:
            script_server_ip = OPENVPN_SERVER_IP or SFOS_SERVER_IP or "127.0.0.1"

    if ENROLL_PUBLIC_BASE_URL:
        parsed = urlsplit(ENROLL_PUBLIC_BASE_URL)
        port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
        return ENROLL_PUBLIC_BASE_URL, script_server_ip, port

    if request is not None:
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        host = forwarded_host or (request.headers.get("host") or "").strip()
        scheme = forwarded_proto or request.url.scheme or "http"
        if host:
            base_url = f"{scheme}://{host}"
            parsed = urlsplit(base_url)
            port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
            return base_url.rstrip("/"), script_server_ip, port

    return f"http://{script_server_ip}:8443", script_server_ip, "8443"


def render_vpn_enroll_script(default_hostname: str = "", request: Request | None = None) -> str:
    """Render vpn_enroll.sh with runtime values."""
    host_for_script = default_hostname.strip()
    if host_for_script and not HOSTNAME_PATTERN.fullmatch(host_for_script):
        host_for_script = ""

    enroll_base_url, script_server_ip, script_server_port = resolve_enroll_script_target(request)

    return render_template_text(
        VPN_ENROLL_TEMPLATE_PATH,
        {
            "__SERVER_IP__": script_server_ip,
            "__SERVER_API_PORT__": script_server_port,
            "__ENROLL_BASE_URL__": enroll_base_url,
            "__VPN_PORT__": str(OPENVPN_PORT),
            "__LEGACY_VPN_PORT__": str(OPENVPN_LEGACY_PORT),
            "__BACKUP_KEY_ID__": BACKUP_UPLOAD_HMAC_KEY_ID,
            "__DEFAULT_HOSTNAME__": host_for_script,
            "__IPT_FILE__": "/var/mdw/etc/iptables/iptable.filter",
        },
    )


def verify_fresh_timestamp(timestamp: int):
    """Ensure timestamp is within allowed drift window."""
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    if abs(now_ts - int(timestamp)) > ALLOWED_DRIFT_SECONDS:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "timestamp_out_of_range",
                "message": "client and server time drift exceeded allowed range",
                "serverTimestamp": now_ts,
                "clientTimestamp": int(timestamp),
                "allowedDriftSeconds": ALLOWED_DRIFT_SECONDS,
            },
        )


def verify_openvpn_enroll(data: EnrollPayload):
    """Verify timestamp and signature for OpenVPN enroll request."""
    verify_fresh_timestamp(data.timestamp)
    msg = f"{data.hostname}:{data.timestamp}"
    if not hmac.compare_digest(sign(msg), data.signature):
        raise HTTPException(status_code=400, detail="bad signature")


def guard_backup_upload_replay(hostname: str, timestamp: int, signature: str) -> None:
    """Prevent replay of identical backup upload signatures within a short TTL window."""
    now = time.time()
    replay_key = f"{hostname}:{int(timestamp)}:{signature}"
    with BACKUP_UPLOAD_REPLAY_LOCK:
        expired = [key for key, expires_at in BACKUP_UPLOAD_REPLAY_CACHE.items() if expires_at <= now]
        for key in expired:
            BACKUP_UPLOAD_REPLAY_CACHE.pop(key, None)
        if replay_key in BACKUP_UPLOAD_REPLAY_CACHE:
            raise HTTPException(status_code=409, detail="replayed backup upload request")
        BACKUP_UPLOAD_REPLAY_CACHE[replay_key] = now + max(BACKUP_UPLOAD_REPLAY_TTL_SECONDS, 60)


def verify_sfos_enroll(data: EnrollApcPayload):
    """Verify timestamp and signature for SFOS enroll request."""
    verify_fresh_timestamp(data.timestamp)
    candidates = [f"{data.hostname}:{data.timestamp}"]
    if (data.mac or "").strip():
        candidates.append(f"{data.hostname}:{data.mac}:{data.timestamp}")

    for msg in candidates:
        expected = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, data.signature):
            return
    raise HTTPException(status_code=400, detail="bad signature")


def client_ip_allowed(client_host: str | None, cidrs: list[str] | None = None) -> bool:
    """Check whether request source IP belongs to an allowed internal subnet."""
    if not client_host:
        return False
    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False

    for cidr in (cidrs or INTERNAL_API_ALLOWED_CIDRS):
        try:
            if client_ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def require_internal_access(
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Allow internal-only APIs from trusted subnets or a configured internal token."""
    client_host = resolve_request_ip(request)
    if client_ip_allowed(client_host):
        return
    if INTERNAL_API_TOKEN and hmac.compare_digest((x_internal_token or "").strip(), INTERNAL_API_TOKEN):
        return
    LOGGER.warning("blocked internal-only access path=%s source_ip=%s", request.url.path, client_host or "-")
    append_feature_log("security", {
        "event": "internal-access-blocked",
        "path": request.url.path,
        "sourceIp": client_host or "-",
    })
    raise HTTPException(status_code=403, detail="internal access only")


def require_internal_or_enroll_token(
    request: Request,
    token: str | None = None,
    x_enroll_token: str | None = Header(default=None, alias="X-Enroll-Token"),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Allow internal access or a valid enroll token for bootstrap script download."""
    client_host = resolve_request_ip(request)
    if client_ip_allowed(client_host):
        return
    if INTERNAL_API_TOKEN and hmac.compare_digest((x_internal_token or "").strip(), INTERNAL_API_TOKEN):
        return
    provided = (x_enroll_token or token or "").strip()
    if provided and hmac.compare_digest(provided, SECRET):
        return
    raise HTTPException(status_code=403, detail="internal access or enroll token required")


def require_security_console_token(
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Allow hidden security console actions for either web sessions or the internal API token."""
    claims = parse_session_cookie(request.cookies.get(WEB_SESSION_COOKIE_NAME))
    if claims:
        return claims
    if INTERNAL_API_TOKEN and hmac.compare_digest((x_internal_token or "").strip(), INTERNAL_API_TOKEN):
        return None
    source_ip = resolve_request_ip(request) or "-"
    LOGGER.warning("blocked security-console access path=%s source_ip=%s", request.url.path, source_ip)
    append_feature_log("security", {
        "event": "security-console-access-blocked",
        "path": request.url.path,
        "sourceIp": source_ip,
    })
    raise HTTPException(status_code=403, detail="security console token required")


def require_inventory_sync_token(
    request: Request,
    x_inventory_sync_token: str | None = Header(default=None, alias="X-Inventory-Sync-Token"),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Allow external inventory callback using a dedicated shared token or internal token."""
    if INTERNAL_API_TOKEN and hmac.compare_digest((x_internal_token or "").strip(), INTERNAL_API_TOKEN):
        return
    if INVENTORY_SYNC_CALLBACK_TOKEN and hmac.compare_digest((x_inventory_sync_token or "").strip(), INVENTORY_SYNC_CALLBACK_TOKEN):
        return
    LOGGER.warning("blocked inventory-callback access path=%s source_ip=%s", request.url.path, resolve_request_ip(request) or "-")
    raise HTTPException(status_code=403, detail="inventory sync token required")


def parse_cidr_network(cidr: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """Parse CIDR in non-strict mode to avoid host-bit input failures."""
    return ipaddress.ip_network(cidr, strict=False)


def alloc_ip_openvpn(db: Session, *, is_legacy: bool = False, client_id: int | None = None) -> str:
    """Allocate SG(OpenVPN) lease IP for hostname."""
    cidr = OPENVPN_LEGACY_CIDR if is_legacy else OPENVPN_CIDR
    gateway_ip = OPENVPN_LEGACY_GATEWAY_IP if is_legacy else OPENVPN_GATEWAY_IP
    return IP_LEASE_MGR.alloc_ip_openvpn(db, is_legacy=is_legacy, client_id=client_id, cidr=cidr, gateway_ip=gateway_ip)


def alloc_ip_sfos(db: Session, client_id: int | None = None) -> str:
    """Allocate XGS(SFOS) lease IP for hostname."""
    return IP_LEASE_MGR.alloc_ip_sfos(db, client_id=client_id, cidr=SFOS_CIDR, gateway_ip=SFOS_GATEWAY_IP)


def make_sfos_username(hostname: str) -> str:
    """Build SFOS APC username from hostname hash."""
    return ENROLL_MGR.make_sfos_username(hostname)


def make_sfos_password() -> str:
    """Build a high-entropy SFOS APC password."""
    return ENROLL_MGR.make_sfos_password()


def hash_stored_secret(secret_value: str) -> str:
    """Hash a secret before persisting it to reduce at-rest exposure."""
    return ENROLL_MGR.hash_stored_secret(secret_value)


def derive_backup_cipher_key() -> bytes:
    """Derive a stable symmetric key for backup setting encryption."""
    return BACKUP_MGR.derive_backup_cipher_key()


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    return BACKUP_MGR._xor_stream(data, key, nonce)


def encrypt_backup_secret(secret_value: str) -> str:
    """Encrypt backup-related secrets before persisting them in the DB."""
    return BACKUP_MGR.encrypt_backup_secret(secret_value)


def decrypt_backup_secret(encrypted_value: str) -> str:
    """Decrypt backup-related secrets stored by encrypt_backup_secret."""
    return BACKUP_MGR.decrypt_backup_secret(encrypted_value)


def ensure_backup_schema() -> None:
    """Create the backup settings table on startup when missing."""
    DBBase.metadata.create_all(bind=DB_ENGINE, tables=[models.BackupSetting.__table__])
    with DB_ENGINE.begin() as conn:
        inspector = inspect(conn)
        columns = {column["name"] for column in inspector.get_columns("backup_settings")}
        missing_columns = {
            "slack_enabled": "ALTER TABLE backup_settings ADD COLUMN slack_enabled BOOLEAN NOT NULL DEFAULT FALSE",
            "slack_bot_token_enc": "ALTER TABLE backup_settings ADD COLUMN slack_bot_token_enc TEXT NOT NULL DEFAULT ''",
            "slack_channel": f"ALTER TABLE backup_settings ADD COLUMN slack_channel VARCHAR(128) NOT NULL DEFAULT '{SECURITY_SLACK_DEFAULT_CHANNEL.replace(chr(39), chr(39) * 2)}'",
            "slack_notify_certificate_expiry": "ALTER TABLE backup_settings ADD COLUMN slack_notify_certificate_expiry BOOLEAN NOT NULL DEFAULT TRUE",
            "slack_notify_backup_completed": "ALTER TABLE backup_settings ADD COLUMN slack_notify_backup_completed BOOLEAN NOT NULL DEFAULT TRUE",
            "slack_notify_security_alert": "ALTER TABLE backup_settings ADD COLUMN slack_notify_security_alert BOOLEAN NOT NULL DEFAULT TRUE",
            "slack_notify_service_down": "ALTER TABLE backup_settings ADD COLUMN slack_notify_service_down BOOLEAN NOT NULL DEFAULT TRUE",
            "slack_notify_resource_threshold": "ALTER TABLE backup_settings ADD COLUMN slack_notify_resource_threshold BOOLEAN NOT NULL DEFAULT TRUE",
            "slack_cpu_threshold": "ALTER TABLE backup_settings ADD COLUMN slack_cpu_threshold INTEGER NOT NULL DEFAULT 90",
            "slack_memory_threshold": "ALTER TABLE backup_settings ADD COLUMN slack_memory_threshold INTEGER NOT NULL DEFAULT 90",
            "slack_disk_threshold": "ALTER TABLE backup_settings ADD COLUMN slack_disk_threshold INTEGER NOT NULL DEFAULT 90",
        }
        for column_name, ddl in missing_columns.items():
            if column_name not in columns:
                conn.execute(text(ddl))


def ensure_equipment_schema() -> None:
    """Create equipment asset tables used by the asset/license management view."""
    DBBase.metadata.create_all(
        bind=DB_ENGINE,
        tables=[models.EquipmentAsset.__table__, models.EquipmentAssetHistory.__table__],
    )


def append_equipment_history(
    db: Session,
    *,
    asset_id: int,
    event_type: int,
    summary: str,
    detail: str = "",
    created_by: str = "system",
) -> None:
    """Persist an equipment asset history entry."""
    EQUIPMENT_MGR.append_history(
        db,
        asset_id=asset_id,
        event_type=event_type,
        summary=summary,
        detail=detail,
        created_by=created_by,
    )


def upsert_equipment_asset(
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
    serial = normalize_serial_number(serial_number)
    model_name = normalize_device_model(device_model)
    return EQUIPMENT_MGR.upsert_asset(
        db,
        serial_number=serial,
        device_model=model_name,
        client_id=client_id,
        event_type=event_type,
        event_summary=event_summary,
        event_detail=event_detail,
        created_by=created_by,
    )


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

ASSET_EVENT_TYPE_LABELS = {
    ASSET_HISTORY_REGISTER: "register",
    ASSET_HISTORY_ENROLL: "enroll",
    ASSET_HISTORY_APC: "apc",
    ASSET_HISTORY_CUSTOMER_SYNC: "customer-sync",
    ASSET_HISTORY_IMPORT: "import",
    ASSET_HISTORY_MANUAL: "manual",
}


def format_license_flags(flags: int | None) -> list[str]:
    """Convert bitmask license flags into UI labels. Base is always included."""
    return EQUIPMENT_MGR.format_license_flags(flags)


def serialize_equipment_asset(
    asset: models.EquipmentAsset,
    history_rows: list[models.EquipmentAssetHistory] | None = None,
    *,
    hostname: str = "",
) -> dict:
    """Serialize an equipment asset and optional history rows for the frontend."""
    return EQUIPMENT_MGR.serialize_asset(asset, history_rows, hostname=hostname)


def build_inventory_license_info(client: models.Client | None) -> dict | None:
    """Return inventory sync license payload. XGS is placeholder until vendor API is defined."""
    if not client or client.vpn_type != "sfos":
        return None
    return {
        "available": False,
        "source": "placeholder",
        "note": "XGS 라이선스 연동은 추후 벤더 API 확정 후 연결 예정",
    }


def build_inventory_mock_response(payload: dict) -> dict:
    """Return a temporary mock response example for internal verification."""
    return {
        "serialNumber": payload.get("serialNumber", ""),
        "hostname": payload.get("hostname", ""),
        "customerName": payload.get("customerName") or "테스트 고객사",
        "assetStatus": payload.get("assetStatus") or "leased",
        "saleType": payload.get("saleType") or "lease",
        "deviceModel": payload.get("deviceModel", ""),
        "licenseInfo": payload.get("licenseInfo"),
        "note": "임시 더미 응답 예시",
    }


def now_kst() -> datetime:
    """Return the current display time in Asia/Seoul."""
    return now_in_timezone_util(DISPLAY_TIMEZONE)


def normalize_feature_log_name(feature: str) -> str:
    """Normalize a feature name used for per-feature log routing."""
    normalized = str(feature or "").strip().lower()
    aliases = {
        "inventory": "inventory-sync",
        "inventory_sync": "inventory-sync",
        "inventory-sync": "inventory-sync",
        "enroll": "enroll",
        "sg-enroll": "enroll",
        "apc": "apc",
        "apc-enroll": "apc",
        "admin-apc": "admin-apc",
        "admin_apc": "admin-apc",
    }
    return aliases.get(normalized, normalized)


def resolve_feature_log_path(feature: str) -> Path:
    """Resolve a concrete log file path for the given feature."""
    normalized = normalize_feature_log_name(feature)
    configured = FEATURE_LOG_FILES.get(normalized)
    if configured:
        return Path(configured)
    safe_name = re.sub(r"[^a-z0-9._-]+", "-", normalized).strip("-") or "general"
    return CERTSVC_FEATURE_LOG_DIR / f"certsvc-{safe_name}.log"


def append_feature_log(feature: str, entry: dict) -> None:
    """Append a JSON log block to the per-feature log file."""
    normalized_feature = normalize_feature_log_name(feature)
    logged_at = now_kst()
    normalized_entry = {
        "feature": normalized_feature,
        "loggedAtKst": logged_at.strftime("%Y-%m-%d %H:%M:%S KST"),
        "loggedAtIso": logged_at.isoformat(timespec="seconds"),
        **entry,
    }
    block = "\n".join(
        [
            "=" * 80,
            json.dumps(normalized_entry, ensure_ascii=False, indent=2),
        ]
    )
    path = resolve_feature_log_path(normalized_feature)
    LOGGER.info(
        "feature-log event=%s feature=%s path=%s",
        normalized_entry.get("event", "-"),
        normalized_feature,
        path,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block + "\n")
    except OSError as exc:
        LOGGER.exception("failed to append feature log feature=%s path=%s error=%s", normalized_feature, path, exc)


def read_feature_log_entries(feature: str, limit: int = 200) -> tuple[str, list[dict]]:
    """Read recent JSON blocks from a per-feature log file."""
    normalized_feature = normalize_feature_log_name(feature)
    path = resolve_feature_log_path(normalized_feature)
    if not path.exists():
        return str(path), []
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        LOGGER.exception("failed to read feature log feature=%s path=%s error=%s", normalized_feature, path, exc)
        return str(path), []

    blocks = [block.strip() for block in raw.split("=" * 80) if block.strip()]
    entries: list[dict] = []
    for block in blocks[-max(1, min(limit, 500)):]:
        try:
            entries.append(json.loads(block))
        except json.JSONDecodeError:
            entries.append({"feature": normalized_feature, "raw": block})
    entries.reverse()
    return str(path), entries


def append_inventory_sync_log(entry: dict, *, feature: str = "inventory-sync") -> None:
    """Append inventory sync logs in a readable JSON block using Korea time."""
    append_feature_log(feature, entry)


def try_send_inventory_sync(
    db: Session,
    *,
    asset: models.EquipmentAsset | None,
    client: models.Client | None,
    assigned_ip: str = "",
    trigger: str = "auto",
    force: bool = False,
) -> dict:
    """Best-effort inventory sync. In dry-run mode it logs JSON instead of sending it."""
    if asset is None:
        return {"ok": False, "reason": "asset-missing"}
    if not force and int(asset.customer_sync_status or 0) == ASSET_CUSTOMER_SYNC_SYNCED:
        return {"ok": True, "reason": "already-synced"}

    payload = EQUIPMENT_MGR.build_sync_payload(
        asset,
        client=client,
        assigned_ip=assigned_ip,
        license_info=build_inventory_license_info(client),
        trigger=trigger,
    )
    log_feature = normalize_feature_log_name(trigger or "inventory-sync")
    log_entry = {
        "event": "inventory-sync-request",
        "trigger": trigger,
        "mode": "dry-run" if INVENTORY_SYNC_DRY_RUN or not INVENTORY_SYNC_PUSH_URL else "live",
        "url": INVENTORY_SYNC_PUSH_URL or "",
        "payload": payload,
        "mockResponse": build_inventory_mock_response(payload),
    }
    append_inventory_sync_log(log_entry, feature=log_feature)

    if INVENTORY_SYNC_DRY_RUN or not INVENTORY_SYNC_PUSH_URL:
        append_equipment_history(
            db,
            asset_id=asset.id,
            event_type=ASSET_HISTORY_CUSTOMER_SYNC,
            summary="외부 재고 동기화 JSON이 로그에 기록되었습니다.",
            detail=json.dumps(log_entry, ensure_ascii=False),
            created_by="inventory-dry-run",
        )
        db.commit()
        return {"ok": True, "mode": "dry-run", "logged": True, "payload": payload}

    req = urllib.request.Request(
        INVENTORY_SYNC_PUSH_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    if INVENTORY_SYNC_AUTH_TOKEN:
        req.add_header("X-Inventory-Sync-Token", INVENTORY_SYNC_AUTH_TOKEN)

    try:
        with urllib.request.urlopen(req, timeout=INVENTORY_SYNC_TIMEOUT_SECONDS) as response:
            body_text = response.read().decode("utf-8", "ignore").strip()
        body = json.loads(body_text) if body_text else {}
        append_inventory_sync_log({
            "event": "inventory-sync-response",
            "trigger": trigger,
            "mode": "live-response",
            "url": INVENTORY_SYNC_PUSH_URL,
            "response": body,
        }, feature=log_feature)
        if isinstance(body, dict) and any(
            key in body
            for key in ["customerName", "customer_name", "assetStatus", "asset_status", "licenseInfo", "license_info"]
        ):
            EQUIPMENT_MGR.apply_sync_payload(db, body, created_by="inventory-api-response")
            db.commit()
        else:
            append_equipment_history(
                db,
                asset_id=asset.id,
                event_type=ASSET_HISTORY_CUSTOMER_SYNC,
                summary="외부 재고 동기화 요청을 전송했습니다.",
                detail=json.dumps(payload, ensure_ascii=False),
                created_by="inventory-push",
            )
            db.commit()
        return {"ok": True, "response": body}
    except INVENTORY_SYNC_ERROR_TYPES as exc:
        append_inventory_sync_log({
            "event": "inventory-sync-failed",
            "trigger": trigger,
            "mode": "live-failure",
            "url": INVENTORY_SYNC_PUSH_URL,
            "error": f"{type(exc).__name__}: {exc}",
        }, feature=log_feature)
        append_equipment_history(
            db,
            asset_id=asset.id,
            event_type=ASSET_HISTORY_CUSTOMER_SYNC,
            summary="외부 재고 동기화 요청 전송에 실패했습니다.",
            detail=f"{type(exc).__name__}: {exc}",
            created_by="inventory-push",
        )
        db.commit()
        return {"ok": False, "reason": str(exc)}


def ensure_client_schema() -> None:
    """Backfill client columns needed for legacy-runtime routing decisions."""
    models.Client.__table__.create(bind=DB_ENGINE, checkfirst=True)
    with DB_ENGINE.begin() as conn:
        inspector = inspect(conn)
        columns = {column["name"] for column in inspector.get_columns("clients")}
        unique_names = {item.get("name") for item in inspector.get_unique_constraints("clients")}
        if "is_legacy" not in columns:
            conn.execute(text("ALTER TABLE clients ADD COLUMN is_legacy BOOLEAN NOT NULL DEFAULT FALSE"))
        if "uq_clients_vpn_ip" in unique_names:
            conn.execute(text("ALTER TABLE clients DROP CONSTRAINT IF EXISTS uq_clients_vpn_ip"))
        if "assigned_ip" in columns:
            conn.execute(text("ALTER TABLE clients DROP COLUMN assigned_ip"))


def ensure_credential_schema() -> None:
    """Bind credentials to clients with strict FK semantics."""
    models.Credential.__table__.create(bind=DB_ENGINE, checkfirst=True)
    with DB_ENGINE.begin() as conn:
        inspector = inspect(conn)
        columns = {column["name"] for column in inspector.get_columns("credentials")}
        unique_names = {item.get("name") for item in inspector.get_unique_constraints("credentials")}
        foreign_keys = {item.get("name"): item for item in inspector.get_foreign_keys("credentials") if item.get("name")}

        if "sfos_admin_password_enc" not in columns:
            conn.execute(text("ALTER TABLE credentials ADD COLUMN sfos_admin_password_enc VARCHAR(512) NOT NULL DEFAULT ''"))

        if "client_id" in columns:
            conn.execute(text("DELETE FROM credentials WHERE client_id IS NULL"))
            conn.execute(text("""
                DELETE FROM credentials
                WHERE client_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM clients WHERE clients.id = credentials.client_id
                  )
            """))
            conn.execute(text("ALTER TABLE credentials ALTER COLUMN client_id SET NOT NULL"))

        if "credentials_client_id_key" not in unique_names:
            conn.execute(text("ALTER TABLE credentials ADD CONSTRAINT credentials_client_id_key UNIQUE (client_id)"))

        existing_fk = foreign_keys.get("credentials_client_id_fkey")
        needs_fk_refresh = True
        if existing_fk:
            options = existing_fk.get("options") or {}
            if str(options.get("ondelete", "")).upper() == "CASCADE":
                needs_fk_refresh = False

        if needs_fk_refresh:
            conn.execute(text("ALTER TABLE credentials DROP CONSTRAINT IF EXISTS credentials_client_id_fkey"))
            conn.execute(
                text("""
                    ALTER TABLE credentials
                    ADD CONSTRAINT credentials_client_id_fkey
                    FOREIGN KEY (client_id) REFERENCES clients(id)
                    ON DELETE CASCADE
                """)
            )


def ensure_ip_lease_schema() -> None:
    """Bind ip_leases to clients and strip redundant mirrored columns."""
    models.IPLease.__table__.create(bind=DB_ENGINE, checkfirst=True)
    with DB_ENGINE.begin() as conn:
        inspector = inspect(conn)
        columns = {column["name"] for column in inspector.get_columns("ip_leases")}
        if "client_id" not in columns:
            conn.execute(text("ALTER TABLE ip_leases ADD COLUMN client_id BIGINT"))

    with Session(DB_ENGINE) as db:
        clients = db.query(models.Client).all()
        client_by_id = {client.id: client for client in clients}
        client_lookup = {}
        for client in clients:
            lease = db.query(models.IPLease).filter_by(client_id=client.id).first()
            if lease and lease.assigned_ip:
                client_lookup[canonical_ip_util(lease.assigned_ip)] = client

        changed = False
        for lease in db.query(models.IPLease).all():
            desired = None
            if getattr(lease, "client_id", None):
                desired = client_by_id.get(lease.client_id)
            if desired is None and lease.assigned_ip:
                desired = client_lookup.get(canonical_ip_util(lease.assigned_ip))
            if desired is None:
                continue
            if lease.client_id != desired.id:
                lease.client_id = desired.id
                changed = True
        if changed:
            db.commit()

    with DB_ENGINE.begin() as conn:
        inspector = inspect(conn)
        columns = {column["name"] for column in inspector.get_columns("ip_leases")}
        unique_names = {item.get("name") for item in inspector.get_unique_constraints("ip_leases")}
        foreign_key_names = {
            item.get("name")
            for item in inspector.get_foreign_keys("ip_leases")
            if item.get("name")
        }
        if "uq_ip_leases_vpn_identity" in unique_names:
            conn.execute(text("ALTER TABLE ip_leases DROP CONSTRAINT IF EXISTS uq_ip_leases_vpn_identity"))
        if "uq_ip_leases_vpn_ip" in unique_names:
            conn.execute(text("ALTER TABLE ip_leases DROP CONSTRAINT IF EXISTS uq_ip_leases_vpn_ip"))
        if "uq_ip_leases_client" not in unique_names:
            conn.execute(text("ALTER TABLE ip_leases ADD CONSTRAINT uq_ip_leases_client UNIQUE (client_id)"))
        if "uq_ip_leases_assigned_ip" not in unique_names:
            conn.execute(text("ALTER TABLE ip_leases ADD CONSTRAINT uq_ip_leases_assigned_ip UNIQUE (assigned_ip)"))
        if "client_id" in columns:
            conn.execute(text("DELETE FROM ip_leases WHERE client_id IS NULL"))
            conn.execute(text("""
                DELETE FROM ip_leases
                WHERE client_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM clients WHERE clients.id = ip_leases.client_id
                  )
            """))
            conn.execute(text("ALTER TABLE ip_leases ALTER COLUMN client_id SET NOT NULL"))
        if "fk_ip_leases_client_id_clients" not in foreign_key_names:
            conn.execute(
                text("""
                    ALTER TABLE ip_leases
                    ADD CONSTRAINT fk_ip_leases_client_id_clients
                    FOREIGN KEY (client_id) REFERENCES clients(id)
                    ON DELETE CASCADE
                """)
            )
        if "identity" in columns:
            conn.execute(text("ALTER TABLE ip_leases DROP COLUMN identity"))
        if "mac" in columns:
            conn.execute(text("ALTER TABLE ip_leases DROP COLUMN mac"))
        if "vpn_type" in columns:
            conn.execute(text("ALTER TABLE ip_leases DROP COLUMN vpn_type"))


def sync_legacy_client_flags() -> None:
    """Backfill legacy flags for existing OpenVPN clients based on legacy CCD membership."""
    legacy_ccd_dir = Path(CCD_LEGACY)
    if not legacy_ccd_dir.exists():
        return
    with Session(DB_ENGINE) as db:
        changed = False
        for client in db.query(models.Client).filter_by(vpn_type="openvpn").all():
            cn = (client.cert_cn or client.hostname or "").strip()
            should_be_legacy = bool(cn) and (legacy_ccd_dir / cn).exists()
            if bool(getattr(client, "is_legacy", False)) != should_be_legacy:
                client.is_legacy = should_be_legacy
                changed = True
        if changed:
            db.commit()


def is_legacy_openvpn_client(client: models.Client) -> bool:
    """Return whether an OpenVPN client should use the legacy runtime/status file.

    Relies solely on the DB is_legacy flag which is authoritative after startup sync.
    Filesystem fallback removed to prevent silent reclassification when CCD files are deleted.
    """
    return bool(getattr(client, "is_legacy", False))


def sync_client_legacy_flag(client: models.Client) -> bool:
    """Persist legacy flag for an OpenVPN client when runtime detection disagrees with DB."""
    if client.vpn_type != "openvpn":
        return False
    detected = is_legacy_openvpn_client(client)
    current = bool(getattr(client, "is_legacy", False))
    if detected == current:
        return detected
    client.is_legacy = detected
    return detected


def sync_ip_lease_binding(
    db: Session,
    *,
    client: models.Client,
    assigned_ip: str,
) -> None:
    """Bind an ip_leases row back to its source client and keep it aligned."""
    if not client or not client.id or not assigned_ip:
        return
    normalized_ip = canonical_ip_util(assigned_ip)
    lease = db.query(models.IPLease).filter_by(client_id=client.id).first()
    if lease is None:
        lease = db.query(models.IPLease).filter_by(assigned_ip=normalized_ip).first()
    if lease is None:
        lease = models.IPLease(
            client_id=client.id,
            assigned_ip=normalized_ip,
            is_active=True,
        )
        db.add(lease)
        db.commit()
        return

    changed = False
    if lease.client_id != client.id:
        lease.client_id = client.id
        changed = True
    if canonical_ip_util(lease.assigned_ip) != normalized_ip:
        lease.assigned_ip = normalized_ip
        changed = True
    if not lease.is_active:
        lease.is_active = True
        changed = True
    if changed:
        db.commit()


def build_assigned_ip_map(db: Session) -> dict[int, str]:
    """Return assigned IPs keyed by client id."""
    rows = db.query(models.IPLease.client_id, models.IPLease.assigned_ip).all()
    return {
        int(client_id): canonical_ip_util(assigned_ip)
        for client_id, assigned_ip in rows
        if client_id is not None and assigned_ip
    }


def resolve_openvpn_runtime(is_legacy: bool) -> dict[str, object]:
    """Return per-runtime OpenVPN paths and gateway settings."""
    return VPN_CFG_MGR.resolve_openvpn_runtime(
        is_legacy,
        ccd_dir=CCD,
        ccd_legacy_dir=CCD_LEGACY,
        openvpn_tun_serial_ip=OPENVPN_TUN_SERIAL_IP,
        openvpn_legacy_tun_serial_ip=OPENVPN_LEGACY_TUN_SERIAL_IP,
        openvpn_server_conf=OPENVPN_SERVER_CONF,
        openvpn_legacy_server_conf=OPENVPN_LEGACY_SERVER_CONF,
        openvpn_port=OPENVPN_PORT,
        openvpn_legacy_port=OPENVPN_LEGACY_PORT,
        openvpn_status_file=OPENVPN_STATUS_FILE,
        openvpn_legacy_status_file=OPENVPN_LEGACY_STATUS_FILE,
    )


def get_backup_settings_record(db: Session) -> models.BackupSetting:
    """Return the singleton backup settings record, creating it on first access."""
    record = db.query(models.BackupSetting).filter_by(id=1).first()
    if record:
        return record
    record = models.BackupSetting(id=1, job_logs_json="[]")
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def parse_job_logs(record: models.BackupSetting) -> list[dict]:
    """Load backup job logs stored inline in the singleton settings row."""
    return BACKUP_MGR.parse_job_logs(record)


def save_job_logs(record: models.BackupSetting, logs: list[dict]) -> None:
    """Persist bounded backup job log history."""
    BACKUP_MGR.save_job_logs(record, logs)


def is_alert_log_entry(entry: dict) -> bool:
    """Return True when a backup log entry represents an alarm/notification event."""
    return BACKUP_MGR.is_alert_log_entry(entry)


def tail_log_lines(path: str, max_lines: int = 200) -> list[str]:
    """Return the last lines of a log file without raising on missing files."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    try:
        lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[-max_lines:]
    except OSError:
        return []


def parse_runtime_guard_entries(max_lines: int = 200) -> list[dict]:
    """Return runtime guard log lines as structured rows for the UI."""
    return ALERT_MGR.parse_runtime_guard_entries(max_lines=max_lines)


def append_backup_log(
    db: Session,
    record: models.BackupSetting,
    *,
    job_type: str,
    status: str,
    message: str,
    detail: str = "",
    trigger: str = "manual",
) -> dict:
    """Append a backup-related log entry into the singleton record."""
    entry = BACKUP_MGR.append_backup_log(
        db,
        record,
        job_type=job_type,
        status=status,
        message=message,
        detail=detail,
        trigger=trigger,
    )
    append_feature_log("backup", {
        "event": f"backup-{job_type}",
        "status": status,
        "trigger": trigger,
        "message": message,
        "detail": detail,
    })
    return entry


def build_local_backup_validation(bundle_dir: Path) -> dict:
    """Validate a locally created backup bundle and return a compact status payload."""
    downloaded = sorted(path.name for path in bundle_dir.iterdir() if path.is_file())
    validation = validate_downloaded_backup_bundle(bundle_dir, downloaded)
    return {
        "backupId": validation.get("backupId") or bundle_dir.name,
        "valid": bool(validation.get("valid")),
        "missingFiles": validation.get("missingFiles", []),
        "validatedArchives": validation.get("validatedArchives", []),
        "dbObjectCountHint": int(validation.get("dbObjectCountHint", 0) or 0),
        "uiIncluded": bool((validation.get("manifest") or {}).get("uiIncluded") or "ui.tar.gz" in (validation.get("expectedFiles") or [])),
        "checkedAt": now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
    }


def summarize_backup_status(record: models.BackupSetting) -> dict:
    """Summarize latest backup success/failure and local bundle integrity."""
    logs = list(reversed(parse_job_logs(record)))
    last_success = next((entry for entry in logs if entry.get("jobType") in {"backup", "backup_verify"} and entry.get("status") == "success"), None)
    last_failure = next((entry for entry in logs if entry.get("jobType") in {"backup", "backup_verify", "restore", "restore_validate"} and entry.get("status") == "failed"), None)

    latest_bundle = next((path for path in sorted(Path(BACKUP_BASE_DIR).glob("backup_*"), key=lambda p: p.name, reverse=True) if path.is_dir()), None)
    latest_local = {
        "exists": False,
        "backupId": "",
        "valid": None,
        "error": "",
    }
    if latest_bundle is not None:
        latest_local["exists"] = True
        latest_local["backupId"] = latest_bundle.name
        try:
            latest_local.update(build_local_backup_validation(latest_bundle))
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
            latest_local.update({
                "valid": False,
                "error": f"{type(exc).__name__}: {exc}",
                "checkedAt": now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
            })

    return {
        "latestLocalBackup": latest_local,
        "restoreEnvironment": build_restore_preflight_summary(latest_bundle, latest_local if latest_local.get("exists") else {}),
        "lastSuccess": last_success,
        "lastFailure": last_failure,
        "recentLogCount": len(logs[:50]),
    }


def serialize_backup_settings(record: models.BackupSetting) -> dict:
    """Return safe backup settings payload for the UI without exposing passwords."""
    return BACKUP_MGR.serialize_backup_settings(record)


def serialize_slack_settings(record: models.BackupSetting) -> dict:
    """Return safe Slack alert settings payload for the UI without exposing tokens."""
    return BACKUP_MGR.serialize_slack_settings(record, SECURITY_SLACK_DEFAULT_CHANNEL)


def normalize_backup_schedule(
    schedule_type: str,
    schedule_time: str | None,
    schedule_weekday: int | None,
    schedule_monthday: int | None,
) -> tuple[str, str, int | None, int | None]:
    """Validate schedule fields and normalize disabled values."""
    value = (schedule_type or "manual").strip().lower()
    if value not in {"manual", "daily", "weekly", "monthly"}:
        raise HTTPException(status_code=400, detail="invalid schedule type")

    time_value = (schedule_time or "").strip()
    if value == "manual":
        return value, "", None, None

    if not re.fullmatch(r"^([01]\d|2[0-3]):[0-5]\d$", time_value):
        raise HTTPException(status_code=400, detail="invalid schedule time")

    if value == "daily":
        return value, time_value, None, None
    if value == "weekly":
        if schedule_weekday is None or int(schedule_weekday) not in range(7):
            raise HTTPException(status_code=400, detail="weekly schedule requires weekday 0-6")
        return value, time_value, int(schedule_weekday), None
    if schedule_monthday is None or int(schedule_monthday) not in range(1, 32):
        raise HTTPException(status_code=400, detail="monthly schedule requires day 1-31")
    return value, time_value, None, int(schedule_monthday)


def resolve_backup_password_payload(
    data: BackupSettingsPayload,
    record: models.BackupSetting,
    *,
    require_present: bool,
) -> str:
    """Resolve password changes while keeping stored secrets masked in the UI."""
    if data.passwordChanged:
        password = (data.ftpPassword or "").strip()
        if require_present and not password:
            raise HTTPException(status_code=400, detail="ftp password is required")
        return password
    if record.ftp_password_enc:
        return decrypt_backup_secret(record.ftp_password_enc)
    if require_present:
        raise HTTPException(status_code=400, detail="ftp password is required")
    return ""


def normalize_slack_threshold(value: int, field_name: str) -> int:
    """Validate Slack alert threshold percentages."""
    parsed = int(value)
    if parsed < 1 or parsed > 100:
        raise HTTPException(status_code=400, detail=f"{field_name} must be between 1 and 100")
    return parsed


def resolve_slack_token_payload(
    data: SlackSettingsPayload,
    record: models.BackupSetting,
    *,
    require_present: bool,
) -> str:
    """Resolve Slack bot token changes while keeping stored secrets masked in the UI."""
    if data.tokenChanged:
        token = (data.botToken or "").strip()
        if require_present and not token:
            raise HTTPException(status_code=400, detail="slack bot token is required")
        return token
    if record.slack_bot_token_enc:
        return decrypt_backup_secret(record.slack_bot_token_enc)
    if require_present:
        raise HTTPException(status_code=400, detail="slack bot token is required")
    return ""


def ensure_ftp_remote_dir(ftp: FTP, remote_dir: str) -> str:
    """Ensure nested remote directories exist before file upload."""
    return BACKUP_MGR.ensure_ftp_remote_dir(ftp, remote_dir)


def ftp_connect(host: str, username: str, password: str, timeout: int = 10) -> FTP:
    """Open and authenticate an FTP session."""
    return BACKUP_MGR.ftp_connect(host, username, password, timeout)


def upload_dir_via_ftp(ftp: FTP, local_dir: Path, remote_dir: str) -> list[str]:
    """Upload a local directory tree to an FTP directory."""
    return BACKUP_MGR.upload_dir_via_ftp(ftp, local_dir, remote_dir)


def build_backup_manifest(record: models.BackupSetting, backup_id: str, files: list[str]) -> dict:
    """Build manifest metadata for a backup set."""
    return BACKUP_MGR.build_backup_manifest(record, backup_id, files)


def build_pg_dump_command(database_url: str, output_path: Path) -> tuple[list[str], dict[str, str]]:
    """Convert SQLAlchemy DATABASE_URL into pg_dump CLI arguments."""
    return BACKUP_MGR.build_pg_dump_command(database_url, output_path)


def build_pg_restore_command(database_url: str, input_path: Path) -> tuple[list[str], dict[str, str]]:
    """Convert SQLAlchemy DATABASE_URL into pg_restore CLI arguments."""
    return BACKUP_MGR.build_pg_restore_command(database_url, input_path)


def list_ftp_backup_directories(ftp: FTP, remote_root: str) -> list[str]:
    """Return backup bundle directory names from an FTP remote path."""
    return BACKUP_MGR.list_ftp_backup_directories(ftp, remote_root)


def download_ftp_backup_bundle(
    ftp: FTP,
    remote_root: str,
    backup_id: str,
    local_root: Path,
) -> tuple[Path, list[str]]:
    """Download one FTP backup bundle directory into local staging."""
    return BACKUP_MGR.download_ftp_backup_bundle(ftp, remote_root, backup_id, local_root)


def validate_tar_archive(path: Path) -> None:
    """Open a tar archive to verify it is readable."""
    BACKUP_MGR.validate_tar_archive(path)


def archive_path_to_bundle(bundle_dir: Path, archive_name: str, source_path: Path, *, exclude_names: set[str] | None = None) -> Path:
    """Create a gzipped tar archive for a file or directory with optional exclusions."""
    return BACKUP_MGR.archive_path_to_bundle(bundle_dir, archive_name, source_path, exclude_names=exclude_names)


def validate_downloaded_backup_bundle(bundle_dir: Path, downloaded: list[str]) -> dict:
    """Validate manifest, dump, and archive files after download."""
    return BACKUP_MGR.validate_downloaded_backup_bundle(bundle_dir, downloaded)


def create_restore_point_bundle() -> Path:
    """Create a local pre-restore snapshot before applying a bundle."""
    return BACKUP_MGR.create_restore_point_bundle(
        pki_dir=PKI,
        ccd_dir=CCD,
        ccd_legacy_dir=CCD_LEGACY,
        ccd_sfos_dir=CCD_SFOS,
        openvpn_conf_path=OPENVPN_SERVER_CONF,
        app_env_file=APP_ENV_FILE,
        app_ui_dir=str(APP_UI_DIR),
        restore_point_dir=RESTORE_POINT_DIR,
        database_url=os.environ["DATABASE_URL"],
    )


def safe_extract_archive(archive_path: Path, target_dir: Path) -> None:
    """Extract a tar.gz archive after checking for path traversal."""
    BACKUP_MGR.safe_extract_archive(archive_path, target_dir)


def append_restore_stage(stages: list[dict], name: str, status: str, detail: str) -> dict:
    """Append a restore stage entry in a UI-friendly shape."""
    return BACKUP_MGR.append_restore_stage(stages, name, status, detail)


def run_systemctl_action(service_name: str, action: str) -> tuple[bool, str]:
    """Run a systemctl action and return success + readable detail."""
    return BACKUP_MGR.run_systemctl_action(service_name, action)


def fetch_local_json(path: str, timeout: int = 3) -> dict | list:
    """Call a local API endpoint, using the internal token when available."""
    req = urllib.request.Request(f"http://127.0.0.1:8443{path}")
    if INTERNAL_API_TOKEN:
        req.add_header("X-Internal-Token", INTERNAL_API_TOKEN)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def build_restore_service_checklist() -> list[dict]:
    """Check core services and API endpoints after restore."""
    def active(service_name: str) -> bool:
        result = subprocess.run(["systemctl", "is-active", service_name], check=False, capture_output=True, text=True)
        return result.returncode == 0 and result.stdout.strip() == "active"

    checklist = [
        {"name": "postgresql.service", "ok": active("postgresql.service"), "detail": "PostgreSQL 데이터베이스 서비스 상태", "critical": True},
        {"name": "nginx.service", "ok": active("nginx.service"), "detail": "HTTPS 프록시 및 정적 UI 서비스 상태", "critical": True},
        {"name": "certsvc.service", "ok": active("certsvc.service"), "detail": "FastAPI 백엔드 서비스 상태", "critical": True},
        {"name": "openvpn-server@server.service", "ok": active("openvpn-server@server.service"), "detail": "일반 SG OpenVPN 서비스 상태", "critical": True},
        {"name": "openvpn-server@server-legacy.service", "ok": active("openvpn-server@server-legacy.service"), "detail": "레거시 SG OpenVPN 서비스 상태", "critical": True},
        {"name": "openvpn-server@server-sfos.service", "ok": active("openvpn-server@server-sfos.service"), "detail": "SFOS OpenVPN 서비스 상태", "critical": True},
        {"name": "PKI 디렉터리", "ok": Path(PKI).exists(), "detail": PKI, "critical": True},
        {"name": "CCD 디렉터리", "ok": Path(CCD).exists(), "detail": CCD, "critical": True},
        {"name": "CCD Legacy 디렉터리", "ok": Path(CCD_LEGACY).exists(), "detail": CCD_LEGACY, "critical": True},
        {"name": "CCD SFOS 디렉터리", "ok": Path(CCD_SFOS).exists(), "detail": CCD_SFOS, "critical": True},
        {"name": ".env 파일", "ok": Path(APP_ENV_FILE).exists(), "detail": APP_ENV_FILE, "critical": True},
    ]

    health_ok = False
    health_detail = "certsvc health check unavailable"
    for _ in range(5):
        try:
            body = fetch_local_json("/health")
            if isinstance(body, dict):
                health_ok = bool(body.get("database"))
                health_detail = json.dumps(body, ensure_ascii=False)
                break
        except Exception as exc:
            health_detail = str(exc)
            threading.Event().wait(1)
    checklist.append({"name": "certsvc /health", "ok": health_ok, "detail": health_detail, "critical": True})

    for path, label in [
        ("/clients", "클라이언트 목록 API"),
        ("/leases", "IP 임대 목록 API"),
    ]:
        ok = False
        detail = "endpoint unavailable"
        for _ in range(5):
            try:
                body = fetch_local_json(path)
                count = len(body) if isinstance(body, list) else (len(body.get("items", [])) if isinstance(body, dict) else 0)
                ok = True
                detail = f"{path} 응답 정상, 항목 수={count}"
                break
            except Exception as exc:
                detail = str(exc)
                threading.Event().wait(1)
        checklist.append({"name": label, "ok": ok, "detail": detail, "critical": False})
    return checklist


def build_restore_preflight_summary(bundle_dir: Path | None, validation: dict | None = None) -> dict:
    """Summarize whether the current host is ready for a restore operation."""
    validation = validation or {}
    stage_root = Path(RESTORE_STAGE_DIR)
    stage_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(stage_root)
    bundle_size_bytes = 0
    if bundle_dir and bundle_dir.exists():
        try:
            bundle_size_bytes = sum(item.stat().st_size for item in bundle_dir.rglob("*") if item.is_file())
        except OSError:
            bundle_size_bytes = 0
    required_free_bytes = max(bundle_size_bytes * 2, 512 * 1024 * 1024)
    service_checklist = build_restore_service_checklist()
    failed_checks = [
        item.get("name")
        for item in service_checklist
        if not item.get("ok") and item.get("critical", True)
    ]
    ready = bool(validation.get("valid", True)) and usage.free >= required_free_bytes and not failed_checks
    return {
        "ready": ready,
        "checkedAt": now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
        "stageDir": str(stage_root),
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


def apply_restored_backup_bundle(bundle_dir: Path) -> dict:
    """Apply a downloaded backup bundle using BackupManager orchestration."""
    return BACKUP_MGR.apply_restored_backup_bundle(
        bundle_dir=bundle_dir,
        pki_dir=PKI,
        ccd_dir=CCD,
        ccd_legacy_dir=CCD_LEGACY,
        ccd_sfos_dir=CCD_SFOS,
        openvpn_conf_path=OPENVPN_SERVER_CONF,
        app_root_dir=str(APP_ROOT_DIR),
        app_env_file=APP_ENV_FILE,
        database_url=os.environ["DATABASE_URL"],
        service_checklist_fn=build_restore_service_checklist,
    )


def run_restore_job(db: Session, data: RestoreRunPayload) -> dict:
    """Application-level restore orchestration: download, validate, and optionally apply."""
    host = (data.ftpHost or "").strip()
    username = (data.ftpUsername or "").strip()
    password = (data.ftpPassword or "").strip()
    remote_path = (data.ftpRemotePath or "").strip() or "/"
    backup_id = (data.backupId or "").strip()
    mode = (data.mode or "validate").strip().lower()
    if mode not in {"validate", "restore"}:
        raise HTTPException(status_code=400, detail="mode must be validate or restore")
    if not host or not username or not password or not backup_id:
        raise HTTPException(status_code=400, detail="ftp host, username, password, and backupId are required")

    record = get_backup_settings_record(db)
    staging_root = Path(RESTORE_STAGE_DIR)
    staging_root.mkdir(parents=True, exist_ok=True)

    ftp = ftp_connect(host, username, password)
    try:
        bundle_dir, downloaded = download_ftp_backup_bundle(ftp, remote_path, backup_id, staging_root)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()

    validation = validate_downloaded_backup_bundle(bundle_dir, downloaded)
    preflight = build_restore_preflight_summary(bundle_dir, validation)
    append_backup_log(
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
            raise HTTPException(
                status_code=400,
                detail=f"백업 검증 실패: 누락 파일 {missing} — 불완전한 백업은 복구할 수 없습니다.",
            )
        if not preflight.get("ready"):
            raise HTTPException(
                status_code=400,
                detail=f"복구 사전 점검 실패: {', '.join(preflight.get('failedChecks', [])) or 'free space or validation issue'}",
            )
        try:
            result["restore"] = apply_restored_backup_bundle(bundle_dir)
        except RestoreExecutionError as exc:
            failed_result = {
                "mode": mode,
                "validation": validation,
                "restore": exc.payload,
            }
            append_backup_log(
                db,
                record,
                job_type="restore",
                status="failed",
                trigger="manual",
                message="백업 복구 실패",
                detail=json.dumps(failed_result, ensure_ascii=False, indent=2),
            )
            if record.slack_notify_backup_completed:
                try_send_configured_slack_message(
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

    append_backup_log(
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
            try_send_configured_slack_message(
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
            try_send_configured_slack_message(
                record,
                "restore_completed",
                {
                    "backupId": validation.get("backupId") or backup_id,
                    "restorePoint": restore_result.get("restorePoint", "-"),
                    "stageCount": len(restore_result.get("stages", [])),
                    "serviceChecklistOk": bool(restore_result.get("serviceChecklistOk")),
                },
            )

    # 스테이징 디렉터리는 검증/복구 완료 후 정리
    try:
        shutil.rmtree(bundle_dir, ignore_errors=True)
    except Exception:
        pass

    return result


def create_backup_bundle(record: models.BackupSetting) -> tuple[Path, dict]:
    """Create a local backup bundle directory with DB/config artifacts."""
    return BACKUP_MGR.create_backup_bundle(
        record,
        pki_dir=PKI,
        ccd_dir=CCD,
        ccd_legacy_dir=CCD_LEGACY,
        ccd_sfos_dir=CCD_SFOS,
        openvpn_conf_path=OPENVPN_SERVER_CONF,
        app_env_file=APP_ENV_FILE,
        app_ui_dir=str(APP_UI_DIR),
        database_url=os.environ["DATABASE_URL"],
    )


def run_backup_job(db: Session, record: models.BackupSetting, trigger: str = "manual") -> dict:
    """Application-level backup orchestration: create bundle, upload, log, and notify."""
    host = (record.ftp_host or "").strip()
    username = (record.ftp_username or "").strip()
    if not host or not username or not record.ftp_password_enc:
        raise HTTPException(status_code=400, detail="backup settings are incomplete")
    return BACKUP_MGR.run_backup_job(
        db,
        record,
        create_bundle_fn=create_backup_bundle,
        validation_fn=build_local_backup_validation,
        append_log_fn=append_backup_log,
        notify_backup_completed_fn=lambda target_record: try_send_configured_slack_message(target_record, "backup_completed"),
        trigger=trigger,
        io_error_types=BACKUP_IO_ERROR_TYPES,
    )


def run_backup_test_connection(settings: BackupSettingsPayload) -> dict:
    """Validate FTP connectivity with the current form values before saving."""
    host = (settings.ftpHost or "").strip()
    username = (settings.ftpUsername or "").strip()
    password = (settings.ftpPassword or "").strip()
    remote_path = (settings.ftpRemotePath or "").strip() or "/"
    if not host or not username or not password:
        raise HTTPException(status_code=400, detail="ftp host, username, and password are required")
    return BACKUP_MGR.test_ftp_connection(host, username, password, remote_path)


def build_schedule_run_key(record: models.BackupSetting, now_local: datetime) -> str:
    """Return the dedupe key for the current schedule window."""
    return BACKUP_MGR.build_schedule_run_key(record, now_local)


def should_run_scheduled_backup(record: models.BackupSetting, now_local: datetime) -> bool:
    """Check whether a scheduled backup is due at the current local time."""
    return BACKUP_MGR.should_run_scheduled_backup(record, now_local)


def backup_scheduler_loop() -> None:
    """Application-level scheduler loop coordinating DB state and backup execution."""
    while True:
        scheduled_run_key = "-"
        try:
            should_run = False
            with Session(DB_ENGINE) as db:
                record = get_backup_settings_record(db)
                now_local = datetime.now()  # server local time for schedule matching
                if should_run_scheduled_backup(record, now_local):
                    scheduled_run_key = build_schedule_run_key(record, now_local)
                    record.last_run_key = scheduled_run_key
                    db.add(record)
                    db.commit()
                    should_run = True
            if should_run:
                # 별도 세션으로 실행: pg_dump+FTP 업로드 중 세션을 점유하지 않도록 분리
                with Session(DB_ENGINE) as db:
                    record = get_backup_settings_record(db)
                    with BACKUP_LOCK:
                        try:
                            run_backup_job(db, record, trigger="scheduled")
                            LOGGER.info("scheduled backup completed run_key=%s", scheduled_run_key)
                        except HTTPException as exc:
                            LOGGER.warning(
                                "scheduled backup skipped run_key=%s detail=%s",
                                scheduled_run_key,
                                getattr(exc, "detail", str(exc)),
                            )
                        except (OSError, subprocess.SubprocessError, RuntimeError, ConnectionError) as exc:
                            LOGGER.exception("scheduled backup failed run_key=%s error=%s", scheduled_run_key, exc)
        except BACKUP_IO_ERROR_TYPES as exc:
            LOGGER.exception("backup scheduler loop failed run_key=%s error=%s", scheduled_run_key, exc)
            append_feature_log("backup", {
                "event": "backup-scheduler-failed",
                "runKey": scheduled_run_key,
                "error": f"{type(exc).__name__}: {exc}",
            })
        except Exception:
            LOGGER.exception("backup scheduler loop unexpected failure run_key=%s", scheduled_run_key)
        finally:
            threading.Event().wait(30)


def ensure_backup_scheduler_started() -> None:
    """Start the background backup scheduler only once per process."""
    global BACKUP_SCHEDULER_THREAD
    if BACKUP_SCHEDULER_THREAD and BACKUP_SCHEDULER_THREAD.is_alive():
        return
    BACKUP_SCHEDULER_THREAD = threading.Thread(target=backup_scheduler_loop, name="backup-scheduler", daemon=True)
    BACKUP_SCHEDULER_THREAD.start()


def resolve_request_ip(request: Request) -> str:
    """Resolve client IP and trust X-Forwarded-For only from known reverse proxies."""
    direct_host = request.client.host if request.client else ""
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for and client_ip_allowed(direct_host, TRUST_PROXY_CIDRS):
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    return direct_host


def default_security_state() -> dict:
    """Build default persistent security monitoring state."""
    return ALERT_MGR.default_security_state()


def security_state_path() -> Path:
    """Return the JSON state file path for security monitoring."""
    return ALERT_MGR.state_file


def load_security_state() -> dict:
    """Load security monitoring state from disk, falling back to defaults."""
    return ALERT_MGR.load_security_state()


def prune_daily_overflow(state: dict, keep_days: int = 30) -> None:
    """30일 이상된 dailyOverflow 날짜 키를 제거하여 상태 파일 무한 증가를 방지한다."""
    ALERT_MGR.prune_daily_overflow(state, keep_days=keep_days)


def save_security_state(state: dict) -> None:
    """Persist security monitoring state to disk."""
    ALERT_MGR.save_security_state(state)


def append_security_event(state: dict, event_type: str, **payload) -> None:
    """Append a bounded security event history entry."""
    ALERT_MGR.append_security_event(state, event_type, **payload)


def build_daily_overflow_rows(daily_overflow: dict) -> list[dict]:
    """Flatten daily overflow counters into table-friendly rows."""
    return ALERT_MGR.build_daily_overflow_rows(daily_overflow)


def build_daily_summary_candidates(daily_overflow: dict, min_overflow_failures: int) -> list[dict]:
    """Return rows eligible for future daily summary delivery."""
    return ALERT_MGR.build_daily_summary_candidates(daily_overflow, min_overflow_failures)


def is_daily_summary_enabled(record: models.BackupSetting) -> bool:
    """Return whether daily Slack summary delivery is operationally enabled."""
    return bool(record.slack_enabled and record.slack_bot_token_enc)


def try_send_security_alert_slack(payload: dict) -> None:
    """Best-effort Slack send for enroll intrusion/security alerts."""
    try:
        with Session(DB_ENGINE) as db:
            record = get_backup_settings_record(db)
            if not record.slack_enabled or not record.slack_bot_token_enc or not bool(getattr(record, "slack_notify_security_alert", True)):
                return
            channel = (record.slack_channel or SECURITY_SLACK_DEFAULT_CHANNEL).strip() or SECURITY_SLACK_DEFAULT_CHANNEL
            bot_token = decrypt_backup_secret(record.slack_bot_token_enc)
        text, blocks = build_slack_test_payload("security_alert", payload=payload)
        send_slack_message(bot_token, channel, text, blocks)
    except Exception:
        traceback.print_exc()


def build_slack_test_payload(template_type: str, payload: dict | None = None) -> tuple[str, list[dict]]:
    """Build sample Slack message text/blocks for supported alert templates."""
    now_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alert_payload = payload or {}
    security_payload = alert_payload
    templates = {
        "certificate_expiry": {
            "title": "인증서 만료 예정",
            "summary": f"{alert_payload.get('hostname', 'suwon-hyo-yoyang')} 인증서가 {alert_payload.get('daysLeft', 7)}일 후 만료됩니다.",
            "fields": [
                ("클라이언트", str(alert_payload.get("hostname", "suwon-hyo-yoyang"))),
                ("할당 IP", str(alert_payload.get("assignedIp", "172.23.212.1"))),
                ("만료일", str(alert_payload.get("expireAt", "2026-04-14"))),
                ("잔여일", f"{alert_payload.get('daysLeft', 7)}일"),
                ("조치", "재발급 일정 확인 필요"),
            ],
        },
        "backup_completed": {
            "title": "백업 완료",
            "summary": "정기 백업이 정상 완료되었습니다.",
            "fields": [
                ("백업 ID", f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
                ("대상", "SSL_VPN_Server_Backup"),
                ("상태", "성공"),
                ("파일 수", "8개"),
            ],
        },
        "restore_validated": {
            "title": "백업 검증 완료",
            "summary": f"{alert_payload.get('backupId', 'backup_20260410_000000')} 검증이 정상 완료되었습니다.",
            "fields": [
                ("백업 ID", str(alert_payload.get("backupId", "backup_20260410_000000"))),
                ("실행 모드", "검증모드"),
                ("DB 객체 힌트", f"{alert_payload.get('dbObjectCountHint', 0)}개"),
                ("검증 상태", "정상"),
                ("누락 파일", f"{alert_payload.get('missingFileCount', 0)}개"),
            ],
        },
        "restore_completed": {
            "title": "백업 복구 완료",
            "summary": f"{alert_payload.get('backupId', 'backup_20260410_000000')} 복구가 완료되었습니다.",
            "fields": [
                ("백업 ID", str(alert_payload.get("backupId", "backup_20260410_000000"))),
                ("실행 모드", "복구모드"),
                ("복구 전 자동 백업", str(alert_payload.get("restorePoint", "-"))),
                ("실행 단계", f"{alert_payload.get('stageCount', 0)}건"),
                ("서비스 체크", "정상" if alert_payload.get("serviceChecklistOk", False) else "확인 필요"),
            ],
        },
        "restore_failed": {
            "title": "백업 복구 실패",
            "summary": str(alert_payload.get("error", "복구 작업 중 오류가 발생했습니다.")),
            "fields": [
                ("백업 ID", str(alert_payload.get("backupId", "-"))),
                ("실행 모드", str(alert_payload.get("modeLabel", "복구모드"))),
                ("실패 단계", str(alert_payload.get("failedStage", "-"))),
                ("복구 전 자동 백업", str(alert_payload.get("restorePoint", "-"))),
                ("조치", "복구 로그와 서비스 상태를 확인하세요."),
            ],
        },
        "security_alert": {
            "title": "침입 시도 감지",
            "summary": f"{security_payload.get('ip', '203.0.113.10')}에서 반복 인증 실패가 감지되었습니다.",
            "fields": [
                ("출발지 IP", str(security_payload.get("ip", "203.0.113.10"))),
                ("실패 횟수", str(security_payload.get("failureCount", 6))),
                ("사유", str(security_payload.get("reason", "bad signature"))),
                ("엔드포인트", str(security_payload.get("endpoint", "/enroll"))),
                ("호스트", str(security_payload.get("hostname", "unknown")) or "-"),
                ("조치", "수동 검토 및 차단 여부 확인"),
            ],
        },
        "service_down": {
            "title": "서비스 다운 감지",
            "summary": f"{alert_payload.get('serviceName', 'openvpn-server@server-legacy.service')} 상태가 비정상입니다.",
            "fields": [
                ("서비스", str(alert_payload.get("serviceName", "openvpn-server@server-legacy.service"))),
                ("현재 상태", str(alert_payload.get("currentStatus", "stopped"))),
                ("포트", str(alert_payload.get("port", "-"))),
                ("감지 시각", str(alert_payload.get("detectedAt", now_label))),
                ("권장 조치", "runtime guard 로그 및 systemctl status 확인"),
            ],
        },
        "service_recovered": {
            "title": "서비스 복구 감지",
            "summary": f"{alert_payload.get('serviceName', 'openvpn-server@server-legacy.service')} 상태가 정상으로 복구되었습니다.",
            "fields": [
                ("서비스", str(alert_payload.get("serviceName", "openvpn-server@server-legacy.service"))),
                ("이전 상태", str(alert_payload.get("previousStatus", "disconnected"))),
                ("현재 상태", str(alert_payload.get("currentStatus", "active"))),
                ("포트", str(alert_payload.get("port", "-"))),
                ("복구 시각", str(alert_payload.get("detectedAt", now_label))),
                ("확인 항목", "서비스 상태 및 실제 연결 수 재확인"),
            ],
        },
        "resource_threshold": {
            "title": "자원 사용률 임계치 초과",
            "summary": "서버 자원 사용률이 임계치를 넘었습니다.",
            "fields": [
                ("CPU", f"{alert_payload.get('cpuPercent', 92.4)}%"),
                ("Memory", f"{alert_payload.get('memoryPercent', 88.1)}%"),
                ("Disk /", f"{alert_payload.get('diskPercent', 91.3)}%"),
                ("권장 조치", "로그/백업 용량 및 프로세스 상태 확인"),
            ],
        },
        "daily_summary": {
            "title": "전일 요약",
            "summary": f"{alert_payload.get('candidateCount', 0)}건의 후보가 감지되었습니다.",
            "fields": [
                ("기준 일자", str(alert_payload.get("targetDate", datetime.now().strftime("%Y-%m-%d")))),
                ("후보 수", str(alert_payload.get("candidateCount", 0))),
                ("기준", f"추가 시도 {alert_payload.get('minOverflowFailures', SECURITY_DAILY_SUMMARY_MIN_OVERFLOW)}회 이상"),
                ("채널", str(alert_payload.get("channel", SECURITY_SLACK_DEFAULT_CHANNEL))),
                ("요약", str(alert_payload.get("summaryLine", "후보가 없습니다."))),
            ],
        },
    }
    selected = templates.get(template_type)
    if not selected:
        raise HTTPException(status_code=400, detail="unsupported slack template type")

    text = f"[{SLACK_ALERT_BRAND}] {selected['title']} - {selected['summary']}"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{SLACK_ALERT_BRAND} | {selected['title']}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": selected["summary"]},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{label}*\n{value}"}
                for label, value in selected["fields"]
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"생성 시각: {now_label}"}],
        },
    ]
    return text, blocks

def send_slack_message(bot_token: str, channel: str, text: str, blocks: list[dict] | None = None) -> dict:
    """Send a message to Slack chat.postMessage using a bot token."""
    payload = {
        "channel": channel,
        "text": text,
    }
    if blocks:
        payload["blocks"] = blocks
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {bot_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(body.get("error") or "slack api error")
    return body


def try_send_configured_slack_message(
    record: models.BackupSetting,
    template_type: str,
    payload: dict | None = None,
) -> None:
    """Best-effort Slack send for configured operational alerts."""
    if not record.slack_enabled or not record.slack_bot_token_enc:
        return
    channel = (record.slack_channel or SECURITY_SLACK_DEFAULT_CHANNEL).strip() or SECURITY_SLACK_DEFAULT_CHANNEL
    try:
        bot_token = decrypt_backup_secret(record.slack_bot_token_enc)
        text, blocks = build_slack_test_payload(template_type, payload=payload)
        send_slack_message(bot_token, channel, text, blocks)
        try:
            with Session(DB_ENGINE) as db:
                current = get_backup_settings_record(db)
                append_backup_log(
                    db,
                    current,
                    job_type=f"alert_{template_type}",
                    status="success",
                    trigger="auto",
                    message=f"슬랙 알림 전송 완료 ({template_type})",
                    detail=json.dumps({"channel": channel, "payload": payload or {}}, ensure_ascii=False),
                )
        except (SQLAlchemyError, OSError, RuntimeError, ValueError):
            LOGGER.exception("failed to persist slack success log template_type=%s", template_type)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
        LOGGER.exception("failed to send slack message template_type=%s error=%s", template_type, exc)
        try:
            with Session(DB_ENGINE) as db:
                current = get_backup_settings_record(db)
                append_backup_log(
                    db,
                    current,
                    job_type=f"alert_{template_type}",
                    status="failed",
                    trigger="auto",
                    message=f"슬랙 알림 전송 실패 ({template_type})",
                    detail=traceback.format_exc(),
                )
        except (SQLAlchemyError, OSError, RuntimeError, ValueError):
            LOGGER.exception("failed to persist slack failure log template_type=%s", template_type)


def prune_failure_timestamps(timestamps: list[int], window_seconds: int, now_ts: int) -> list[int]:
    """Keep only timestamps within the configured monitoring window."""
    return [ts for ts in timestamps if now_ts - int(ts) <= window_seconds]


def is_monitored_auth_failure(detail: object) -> bool:
    """Return True when an enroll error should count toward abuse monitoring.

    detail이 dict인 경우(409 timestamp 에러 등)를 별도 처리해야 str 변환 후 토큰 불일치를 방지한다.
    """
    if isinstance(detail, dict):
        text_detail = " ".join(str(v) for v in detail.values()).lower()
    else:
        text_detail = str(detail or "").lower()
    monitored_tokens = [
        "bad signature",
        "timestamp_out_of_range",
        "exceeded allowed range",
        "invalid hostname format",
        "invalid mac format",
    ]
    return any(token in text_detail for token in monitored_tokens)


def register_security_success(source_ip: str, endpoint: str, hostname: str = "") -> None:
    """Reset short-term failure counters after a successful monitored enroll."""
    ALERT_MGR.register_security_success(source_ip, endpoint, hostname)


def register_security_failure(source_ip: str, endpoint: str, reason: str, hostname: str = "") -> dict:
    """Record a monitored enroll failure, alert, and ban when thresholds are exceeded."""
    result = ALERT_MGR.register_security_failure(source_ip, endpoint, reason, hostname)
    if result.get("shouldAlert"):
        try_send_security_alert_slack(
            {
                "ip": source_ip,
                "endpoint": endpoint,
                "reason": reason,
                "hostname": hostname,
                "failureCount": result.get("failureCount", 0),
            }
        )
    return {
        "failureCount": int(result.get("failureCount", 0)),
        "banned": bool(result.get("banned", False)),
    }


def is_source_ip_banned(source_ip: str) -> bool:
    """Return True when an IP is currently marked as manually reviewed ban target."""
    return ALERT_MGR.is_source_ip_banned(source_ip)


@app.middleware("http")
async def block_banned_enroll_ips(request: Request, call_next):
    """Block requests from banned IPs on externally exposed enroll paths."""
    source_ip = resolve_request_ip(request)
    if request.url.path in SECURITY_MONITOR_MONITORED_PATHS and is_source_ip_banned(source_ip):
        return JSONResponse(status_code=403, content={"detail": "source ip blocked"})
    return await call_next(request)


@app.get("/auth/me")
def auth_me(request: Request):
    """Return current web login session state."""
    claims = parse_session_cookie(request.cookies.get(WEB_SESSION_COOKIE_NAME))
    if not claims:
        return JSONResponse(status_code=401, content={"authenticated": False})
    return {"authenticated": True, "username": claims.get("sub", WEB_LOGIN_USERNAME)}


@app.post("/auth/login")
def auth_login(request: Request, data: WebLoginPayload):
    """Authenticate the web console and issue a signed session cookie."""
    if not WEB_SESSION_SECRET:
        raise HTTPException(status_code=503, detail="web session secret not configured")
    if not (WEB_LOGIN_PASSWORD_HASH or WEB_LOGIN_PASSWORD):
        raise HTTPException(status_code=503, detail="web login password not configured")

    source_ip = resolve_request_ip(request)

    try:
        SECURITY.check_login_rate_limit(source_ip, WEB_LOGIN_MAX_ATTEMPTS, WEB_LOGIN_WINDOW_SECONDS)
    except RateLimitExceeded:
        LOGGER.warning("login rate limit exceeded source_ip=%s", source_ip or "-")
        append_feature_log("auth", {
            "event": "login-rate-limit",
            "sourceIp": source_ip or "-",
        })
        raise HTTPException(status_code=429, detail="too many login attempts")

    username = normalize_login_username(data.username)
    password = normalize_login_password(data.password)
    if not (
        hmac.compare_digest(username, WEB_LOGIN_USERNAME)
        and verify_web_login_password(password)
    ):
        SECURITY.register_login_failure(source_ip)
        failure_count = SECURITY.get_login_failure_count(source_ip, WEB_LOGIN_WINDOW_SECONDS)
        LOGGER.warning(
            "invalid login source_ip=%s username=%s failures=%s",
            source_ip or "-",
            username or "-",
            failure_count,
        )
        append_feature_log("auth", {
            "event": "login-failure",
            "sourceIp": source_ip or "-",
            "username": username or "-",
            "failures": failure_count,
        })
        raise HTTPException(status_code=401, detail="invalid credentials")

    SECURITY.clear_login_attempts(source_ip)
    LOGGER.info("web login success source_ip=%s username=%s", source_ip or "-", username)
    append_feature_log("auth", {
        "event": "login-success",
        "sourceIp": source_ip or "-",
        "username": username,
    })
    response = JSONResponse({"ok": True, "username": WEB_LOGIN_USERNAME})
    response.set_cookie(
        key=WEB_SESSION_COOKIE_NAME,
        value=build_session_cookie(WEB_LOGIN_USERNAME),
        max_age=WEB_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=session_cookie_secure(request),
        path="/",
    )
    return response


@app.post("/auth/logout")
def auth_logout(request: Request):
    """Clear the current web login session cookie."""
    source_ip = resolve_request_ip(request) or "-"
    LOGGER.info("web logout source_ip=%s", source_ip)
    append_feature_log("auth", {
        "event": "logout",
        "sourceIp": source_ip,
    })
    response = JSONResponse({"ok": True})
    response.delete_cookie(WEB_SESSION_COOKIE_NAME, path="/")
    return response


def build_client_cert_if_missing(hostname: str):
    """Create client cert via EasyRSA when missing and return paths."""
    ca_path = Path(PKI) / "pki" / "ca.crt"
    crt_path = Path(PKI) / "pki" / "issued" / f"{hostname}.crt"
    key_path = Path(PKI) / "pki" / "private" / f"{hostname}.key"
    if ca_path.exists() and crt_path.exists() and key_path.exists():
        return ca_path, crt_path, key_path

    env = os.environ.copy()
    env["EASYRSA_BATCH"] = "1"
    env["EASYRSA_CERT_EXPIRE"] = "3650"
    env["EASYRSA_CA_EXPIRE"] = "3650"

    # EasyRSA 실행 전, 기존 파일을 임시 경로에 백업해둔다.
    # 생성 실패 시 복구하여 인증서 영구 소실을 방지한다.
    backups: dict[str, str] = {}
    for dirname in ["reqs", "issued", "private"]:
        pattern = str(Path(PKI) / "pki" / dirname / f"{hostname}*")
        for filepath in glob.glob(pattern):
            src = Path(filepath)
            if src.exists():
                bak = src.with_suffix(src.suffix + ".bak")
                shutil.copy2(src, bak)
                backups[str(src)] = str(bak)
                src.unlink()

    result = subprocess.run(
        ["./easyrsa", "build-client-full", hostname, "nopass"],
        cwd=PKI,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # EasyRSA 실패 → 백업 파일 원위치 복구
        for orig, bak in backups.items():
            try:
                shutil.move(bak, orig)
            except Exception:
                pass
        raise RuntimeError(f"EasyRSA failed ({result.returncode}): {result.stderr}")

    # 성공 후 백업 파일 정리
    for bak in backups.values():
        try:
            Path(bak).unlink(missing_ok=True)
        except Exception:
            pass

    return ca_path, crt_path, key_path


def ensure_client_material(hostname: str):
    """Load client CA/CRT/KEY PEM contents."""
    ca_path, crt_path, key_path = build_client_cert_if_missing(hostname)
    with open(ca_path, "r", encoding="utf-8") as f:
        ca_pem = f.read()
    with open(crt_path, "r", encoding="utf-8") as f:
        crt_pem = f.read()
    with open(key_path, "r", encoding="utf-8") as f:
        key_pem = f.read()
    return ca_pem, crt_pem, key_pem


def write_openvpn_bundle(
    hostname: str,
    ca_cert: str,
    certificate: str,
    key: str,
    vpn_port: int | None = None,
    api_port: int | None = None,
) -> str:
    """Build and archive deployable OpenVPN client bundle."""
    return VPN_CFG_MGR.write_openvpn_bundle(
        hostname,
        ca_cert,
        certificate,
        key,
        server_ip=OPENVPN_SERVER_IP,
        default_port=OPENVPN_PORT,
        vpn_port=vpn_port,
        pki_dir=PKI,
        templates_dir=str(TEMPLATES_DIR),
        api_port=api_port,
    )


def collect_runtime_service_statuses() -> list[dict]:
    """Collect runtime service status snapshot based on service liveness."""
    db_ok = True
    try:
        with DB_ENGINE.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db_ok = False

    return VPN_CFG_MGR.build_runtime_service_statuses(
        db_ok=db_ok,
        openvpn_port=OPENVPN_PORT,
        openvpn_legacy_port=OPENVPN_LEGACY_PORT,
        sfos_vpn_port=SFOS_VPN_PORT,
    )


def alert_monitor_loop() -> None:
    """Application-level monitor loop coordinating runtime checks and alert delivery."""
    while True:
        try:
            with Session(DB_ENGINE) as db:
                record = get_backup_settings_record(db)
                services = collect_runtime_service_statuses()
                resources = MONITOR.get_resource_usage()
                active_clients = db.query(models.Client).filter(models.Client.status == "active").all()
                assigned_ip_map = build_assigned_ip_map(db)
                ALERT_MGR.process_operational_alerts(
                    record=record,
                    services=services,
                    resources=resources,
                    active_clients=active_clients,
                    assigned_ip_map=assigned_ip_map,
                    get_certificate_expire_at=lambda client: get_certificate_expire_at_util(client.cert_cn or client.hostname, client.created_at, pki_dir=PKI),
                    send_slack=lambda template_type, payload: try_send_configured_slack_message(record, template_type, payload=payload),
                )
        except (SQLAlchemyError, OSError, RuntimeError, ValueError) as exc:
            LOGGER.exception("alert monitor loop failed error=%s", exc)
            append_feature_log("security", {
                "event": "alert-monitor-loop-failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
        except Exception:
            LOGGER.exception("alert monitor loop unexpected failure")
        finally:
            threading.Event().wait(ALERT_MONITOR_INTERVAL_SECONDS)


def ensure_alert_monitor_started() -> None:
    """Start the background operational alert monitor only once per process."""
    global ALERT_MONITOR_THREAD
    if ALERT_MONITOR_THREAD and ALERT_MONITOR_THREAD.is_alive():
        return
    ALERT_MONITOR_THREAD = threading.Thread(target=alert_monitor_loop, name="alert-monitor", daemon=True)
    ALERT_MONITOR_THREAD.start()


def upsert_client_and_credential(
    db: Session,
    hostname: str,
    mac: str,
    ip: str,
    ts: int,
    admin_password: str | None = None,
    auto_commit: bool = True,
):
    """Create or update SFOS client and credential records.

    기존 클라이언트가 재등록하더라도 패스워드를 교체하지 않는다.
    VPN/APC 패스워드는 encrypt_backup_secret으로 가역 암호화해 저장하며,
    SFOS 관리자 비밀번호는 credentials.sfos_admin_password_enc 에 별도 암호화 저장한다.
    기존 sha256$ 형식 레코드는 복호화 불가이므로 최초 1회 rotate 처리한다.
    """
    ip = IP_LEASE_MGR.canonical_ip(ip)
    username = make_sfos_username(hostname)
    client = db.query(models.Client).filter_by(hostname=hostname, vpn_type="sfos").first()
    if not client:
        client = models.Client(
            hostname=hostname,
            mac=mac,
            vpn_type="sfos",
            cert_cn=hostname,
            status="active",
        )
        db.add(client)
        db.flush()
        db.refresh(client)
    else:
        client.mac = mac
        client.cert_cn = hostname
        client.status = "active"
        db.flush()

    sync_ip_lease_binding(db, client=client, assigned_ip=ip)

    manual_password = (admin_password or "").strip()
    cred = db.query(models.Credential).filter_by(client_id=client.id).first()
    if not cred:
        # 최초 등록: 관리자 지정 패스워드 우선, 미입력 시 새 패스워드 생성
        password = manual_password or make_sfos_password()
        cred = models.Credential(
            client_id=client.id,
            username=username,
            password=encrypt_backup_secret(password),
            sfos_admin_password_enc=encrypt_backup_secret(manual_password) if manual_password else "",
        )
        db.add(cred)
        db.flush()
    elif cred.password.startswith("enc1$"):
        # 기존 암호화 패스워드 재사용, 관리자 지정 패스워드가 있으면 교체
        password = decrypt_backup_secret(cred.password)
        if manual_password and password != manual_password:
            password = manual_password
            cred.password = encrypt_backup_secret(password)
        if manual_password:
            cred.sfos_admin_password_enc = encrypt_backup_secret(manual_password)
        if cred.username != username:
            cred.username = username
        db.flush()
    else:
        # 구 sha256$ 해시 포맷은 복호화 불가이므로 관리자 지정값 또는 신규값으로 rotate
        password = manual_password or make_sfos_password()
        cred.username = username
        cred.password = encrypt_backup_secret(password)
        cred.sfos_admin_password_enc = encrypt_backup_secret(manual_password) if manual_password else ""
        db.flush()

    if auto_commit:
        db.commit()

    return username, password


@app.on_event("startup")
def startup_runtime() -> None:
    """Initialize runtime-only tables and background workers."""
    ensure_client_schema()
    ensure_credential_schema()
    ensure_ip_lease_schema()
    ensure_equipment_schema()
    sync_legacy_client_flags()
    ensure_backup_schema()
    ensure_backup_scheduler_started()
    ensure_alert_monitor_started()

    # Process offline queue if any files exist
    db_gen = None
    try:
        db_gen = get_db()
        db = next(db_gen)
        results = OFFLINE_QUEUE_MGR.process_offline_queue(db)
        if results["processed"] > 0:
            LOGGER.info(f"Offline queue processing: {results['succeeded']} succeeded, {results['failed']} failed")
            if results["errors"]:
                LOGGER.warning(f"Offline queue errors: {results['errors']}")
    except Exception as e:
        LOGGER.warning(f"Failed to process offline queue on startup: {e}", exc_info=True)
    finally:
        if db_gen is not None:
            try:
                db_gen.close()
            except Exception:
                pass


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Return service health based on DB connectivity and runtime readiness."""
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db_ok = False

    ui_index_exists = (Path("/opt/certsvc/ui/dist") / "index.html").exists()
    dir_checks = {
        "pki": Path(PKI).exists(),
        "ccd": Path(CCD).exists(),
        "ccdSfos": Path(CCD_SFOS).exists(),
        "uiDist": ui_index_exists,
        "backupDir": Path(BACKUP_BASE_DIR).exists(),
    }
    status = "ok" if db_ok and all(dir_checks.values()) else "degraded"
    return {
        "status": status,
        "database": db_ok,
        "checks": dir_checks,
        "time": now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
    }


@app.get("/equipment-assets")
def list_equipment_assets(
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Return equipment asset list with recent per-asset history."""
    assets = db.query(models.EquipmentAsset).order_by(models.EquipmentAsset.updated_at.desc()).all()
    asset_ids = [asset.id for asset in assets if asset.id]
    client_ids = [asset.client_id for asset in assets if asset.client_id]
    history_map: dict[int, list[models.EquipmentAssetHistory]] = {}
    client_map: dict[int, models.Client] = {}
    if client_ids:
        client_rows = db.query(models.Client).filter(models.Client.id.in_(client_ids)).all()
        client_map = {row.id: row for row in client_rows if row.id is not None}
    if asset_ids:
        history_rows = (
            db.query(models.EquipmentAssetHistory)
            .filter(models.EquipmentAssetHistory.asset_id.in_(asset_ids))
            .order_by(models.EquipmentAssetHistory.created_at.desc())
            .all()
        )
        for row in history_rows:
            bucket = history_map.setdefault(row.asset_id, [])
            if len(bucket) < 12:
                bucket.append(row)
    return [
        serialize_equipment_asset(
            asset,
            history_map.get(asset.id, []),
            hostname=(client_map.get(asset.client_id).hostname if asset.client_id in client_map else ""),
        )
        for asset in assets
    ]


@app.post("/equipment-assets/manual")
def create_equipment_asset_manual(
    data: EquipmentAssetManualCreatePayload,
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Create a single equipment asset from the admin UI."""
    serial_number = normalize_serial_number(data.serialNumber)
    device_model = normalize_device_model(data.deviceModel)

    existing = db.query(models.EquipmentAsset).filter_by(serial_number=serial_number).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="이미 등록된 시리얼입니다.")

    asset = upsert_equipment_asset(
        db,
        serial_number=serial_number,
        device_model=device_model,
        event_type=ASSET_HISTORY_MANUAL,
        event_summary="관리자 수동 등록으로 장비 정보가 반영되었습니다.",
        event_detail=f"수동 등록: serial={serial_number}, model={device_model}",
        created_by="security-console",
    )
    db.commit()
    db.refresh(asset)

    return {
        "ok": True,
        "mode": "created",
        "asset": serialize_equipment_asset(asset),
    }


@app.get("/equipment-assets/import/template")
def download_equipment_assets_template(
    _: dict | None = Depends(require_web_or_internal_access),
):
    """Download an Excel template for bulk equipment serial/model registration."""
    if not EQUIPMENT_ASSETS_TEMPLATE_PATH.is_file():
        raise HTTPException(status_code=500, detail="equipment asset template file is missing")

    return FileResponse(
        EQUIPMENT_ASSETS_TEMPLATE_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="equipment_assets_template.xlsx",
    )


@app.post("/equipment-assets/import")
async def import_equipment_assets_excel(
    file: UploadFile = File(...),
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Bulk import equipment assets from an Excel file."""
    filename = (file.filename or "").strip().lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="only .xlsx files are supported")

    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="openpyxl is required for excel import") from exc

    payload = await file.read()
    await file.close()
    if not payload:
        raise HTTPException(status_code=400, detail="empty excel file")

    try:
        wb = load_workbook(filename=io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid excel format: {exc}") from exc

    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        raise HTTPException(status_code=400, detail="excel header row is missing")

    def normalize_header(value: object) -> str:
        return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").strip().lower())

    normalized_headers = [normalize_header(value) for value in header]
    serial_candidates = {"serialnumber", "serial", "시리얼", "시리얼넘버"}
    model_candidates = {"devicemodel", "model", "장비명모델", "장비모델"}

    serial_idx = next((idx for idx, col in enumerate(normalized_headers) if col in serial_candidates), None)
    model_idx = next((idx for idx, col in enumerate(normalized_headers) if col in model_candidates), None)
    if serial_idx is None or model_idx is None:
        raise HTTPException(status_code=400, detail="header must include 시리얼 and 장비명/모델 columns")

    created = 0
    duplicates = 0
    skipped = 0
    row_errors: list[str] = []

    for row_number, row in enumerate(rows, start=2):
        raw_serial = str((row[serial_idx] if serial_idx < len(row) else "") or "").strip()
        raw_model = str((row[model_idx] if model_idx < len(row) else "") or "").strip()
        if not raw_serial and not raw_model:
            continue
        if not raw_serial or not raw_model:
            skipped += 1
            row_errors.append(f"row {row_number}: serialNumber/deviceModel are required")
            continue

        try:
            serial_number = normalize_serial_number(raw_serial)
            device_model = normalize_device_model(raw_model)
            existing = db.query(models.EquipmentAsset).filter_by(serial_number=serial_number).first()
            if existing is not None:
                duplicates += 1
                skipped += 1
                row_errors.append(f"row {row_number}: 이미 등록된 시리얼입니다. ({serial_number})")
                continue

            upsert_equipment_asset(
                db,
                serial_number=serial_number,
                device_model=device_model,
                event_type=ASSET_HISTORY_IMPORT,
                event_summary="엑셀 일괄 등록으로 장비 정보가 반영되었습니다.",
                event_detail=f"엑셀 등록: row={row_number}, serial={serial_number}, model={device_model}",
                created_by="security-console",
            )
            created += 1
        except HTTPException as exc:
            skipped += 1
            row_errors.append(f"row {row_number}: {exc.detail}")

    db.commit()
    return {
        "ok": True,
        "created": created,
        "duplicates": duplicates,
        "skipped": skipped,
        "errors": row_errors,
    }


@app.post("/equipment-assets/sync/callback")
def receive_equipment_sync_callback(
    data: InventorySyncPayload,
    _: None = Depends(require_inventory_sync_token),
    db: Session = Depends(get_db),
):
    """Receive external inventory/customer sync data and apply it to the asset DB."""
    try:
        asset = EQUIPMENT_MGR.apply_sync_payload(
            db,
            data.model_dump(exclude_none=True),
            created_by="inventory-callback",
        )
        db.commit()
        return {"ok": True, "asset": serialize_equipment_asset(asset)}
    except ValueError as exc:
        db.rollback()
        message = str(exc)
        if "required" in message.lower():
            raise HTTPException(status_code=400, detail=message) from exc
        raise HTTPException(status_code=404, detail=message) from exc
    except (IntegrityError, RuntimeError, TypeError, KeyError, OSError) as exc:
        db.rollback()
        LOGGER.exception("inventory sync callback failed")
        raise HTTPException(status_code=500, detail=f"inventory sync callback failed: {exc}") from exc


def resolve_inventory_sync_target(
    db: Session,
    *,
    serial_number: str = "",
) -> tuple[models.EquipmentAsset, models.Client | None, str]:
    """Resolve an equipment asset and its linked client/IP by serial number only."""
    client = None

    normalized_serial = normalize_serial_number(serial_number) if (serial_number or "").strip() else ""
    if not normalized_serial:
        raise HTTPException(status_code=400, detail="serialNumber is required")

    asset = db.query(models.EquipmentAsset).filter_by(serial_number=normalized_serial).first()
    if asset is None:
        raise HTTPException(status_code=404, detail=f"equipment asset not found for serial: {normalized_serial}")

    if client is None and asset.client_id:
        client = db.query(models.Client).filter_by(id=asset.client_id).first()

    assigned_ip = ""
    if client is not None:
        lease = db.query(models.IPLease).filter_by(client_id=client.id).first()
        assigned_ip = canonical_ip_util(lease.assigned_ip) if lease and lease.assigned_ip else ""
    return asset, client, assigned_ip


@app.post("/equipment-assets/sync/listener")
def listen_equipment_sync_request(
    data: InventorySyncPayload,
    _: None = Depends(require_inventory_sync_token),
    db: Session = Depends(get_db),
):
    """Mock listener for future external inventory sync integration.

    It accepts the current outbound payload shape and returns a normalized
    response payload without mutating the DB. This allows end-to-end testing
    before the real vendor/customer API is available.
    """
    request_payload = data.model_dump(exclude_none=True)
    asset, client, assigned_ip = resolve_inventory_sync_target(
        db,
        serial_number=data.serialNumber or "",
    )

    response_payload = {
        "serialNumber": asset.serial_number,
        "hostname": (client.hostname if client else data.hostname) or "",
        "assignedIp": assigned_ip,
        "vpnType": (client.vpn_type if client else data.vpnType) or "",
        "customerName": asset.customer_name or data.customerName or "연동 대기 고객사",
        "customerSyncStatus": int(asset.customer_sync_status or 0),
        "assetStatus": int(asset.asset_status or 0),
        "saleType": int(asset.sale_type or 0),
        "deviceModel": asset.device_model or data.deviceModel or "",
        "licenseInfo": build_inventory_license_info(client),
        "expireAt": get_certificate_expire_at_util(
            (client.cert_cn if client and client.cert_cn else (client.hostname if client else "")) or "",
            client.created_at if client else asset.created_at,
            pki_dir=PKI,
        ) if client else "",
        "registeredAt": format_display_datetime_util(
            client.created_at if client else asset.created_at,
            DISPLAY_TIMEZONE,
        ),
        "note": "inventory sync mock listener response",
    }

    append_inventory_sync_log(
        {
            "event": "inventory-sync-listener-request",
            "mode": "listener",
            "payload": request_payload,
            "response": response_payload,
        },
        feature="inventory-sync",
    )
    return {"ok": True, "mode": "listener", "response": response_payload}


@app.post("/equipment-assets/sync/trigger")
def trigger_equipment_asset_sync(
    data: InventorySyncTriggerPayload,
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Manually trigger outbound inventory sync for a specific asset by serial."""
    asset, client, assigned_ip = resolve_inventory_sync_target(
        db,
        serial_number=data.serialNumber or "",
    )

    result = try_send_inventory_sync(
        db,
        asset=asset,
        client=client,
        assigned_ip=assigned_ip,
        trigger="manual",
        force=True,
    )
    return {"ok": bool(result.get("ok")), "result": result}


@app.get("/clients")
def list_clients(
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Return client list enriched with live connection status."""
    clients = db.query(models.Client).order_by(models.Client.created_at.desc()).all()
    conn_sets = VPN_CFG_MGR.collect_connection_sets(
        openvpn_server_conf=OPENVPN_SERVER_CONF,
        openvpn_status_file=OPENVPN_STATUS_FILE,
        openvpn_legacy_status_file=OPENVPN_LEGACY_STATUS_FILE,
        sfos_server_conf=SFOS_SERVER_CONF,
        sfos_status_file=SFOS_STATUS_FILE,
    )
    openvpn_connected = conn_sets["openvpnConnected"]
    openvpn_legacy_connected = conn_sets["openvpnLegacyConnected"]
    sfos_connected = conn_sets["sfosConnected"]
    assigned_ip_map = build_assigned_ip_map(db)
    equipment_rows = db.query(models.EquipmentAsset.client_id, models.EquipmentAsset.serial_number).all()
    serial_by_client_id = {
        int(client_id): serial_number
        for client_id, serial_number in equipment_rows
        if client_id is not None and serial_number
    }
    result = []
    changed = False
    for client in clients:
        cn = (client.cert_cn or client.hostname or "").strip()
        if client.vpn_type == "openvpn":
            before_flag = bool(getattr(client, "is_legacy", False))
            is_legacy_client = sync_client_legacy_flag(client)
            changed = changed or (before_flag != is_legacy_client)
        else:
            is_legacy_client = False
        display_vpn_type = VPN_CFG_MGR.normalize_display_vpn_type(
            client.vpn_type,
            is_legacy_openvpn=is_legacy_client,
        )
        computed = VPN_CFG_MGR.compute_client_connection_status(
            display_vpn_type=display_vpn_type,
            is_active=(client.status or "").strip().lower() != "inactive",
            identity=cn,
            openvpn_connected=openvpn_connected,
            openvpn_legacy_connected=openvpn_legacy_connected,
            sfos_connected=sfos_connected,
        )
        is_connected = computed == "active"

        current_status = (client.status or "").strip().lower()
        if current_status == "inactive":
            display_status = "inactive"
        else:
            display_status = "active" if is_connected else "disconnected"

        result.append(
            {
                "id": client.id,
                "hostname": client.hostname,
                "vpnType": client.vpn_type,
                "httpsPortStatus": 0,
                "certCn": client.cert_cn,
                "assignedIp": assigned_ip_map.get(client.id, ""),
                "isLegacy": is_legacy_client,
                "status": display_status,
                "expireAt": get_certificate_expire_at_util(client.cert_cn or client.hostname, client.created_at, pki_dir=PKI),
                "registeredAt": format_display_datetime_util(client.created_at, DISPLAY_TIMEZONE),
                "lastSeenAt": format_display_datetime_util(client.updated_at, DISPLAY_TIMEZONE),
                "mac": client.mac or "",
                "serialNumber": serial_by_client_id.get(client.id, ""),
                "tenant": "",
                "issuer": SERVER_DN,
                "remoteIp": "",
                "username": "",
            }
        )
    if changed:
        db.commit()
    return result


def get_client_serial_number(db: Session, client_id: int) -> str:
    """Return the linked equipment serial number for a client when available."""
    serial = db.query(models.EquipmentAsset.serial_number).filter_by(client_id=client_id).scalar()
    return (serial or "").strip()


def resolve_sfos_backup_dir(path_exists: callable, candidates: list[str] | None = None) -> str | None:
    """Return first existing SFOS backup directory among version-specific candidate paths.

    `path_exists` should be a callable that returns True when the provided path exists.
    This allows the same fallback logic to be reused for local filesystem checks or
    remote checks over SSH/SFTP.
    """
    paths = candidates or SFOS_BACKUP_DIR_CANDIDATES
    for path in paths:
        try:
            if path_exists(path):
                return path
        except Exception:
            continue
    return None


def find_latest_client_backup_file(hostname: str, serial_number: str) -> Path | None:
    """Find latest client backup file by serial/hostname under the client backup base directory."""
    base_dir = Path(CLIENT_BACKUP_BASE_DIR)
    if not base_dir.exists() or not base_dir.is_dir():
        return None

    keys = [
        (serial_number or "").strip().lower(),
        (hostname or "").strip().lower(),
    ]
    keys = [key for key in keys if key]
    if not keys:
        return None

    candidates: list[Path] = []
    seen: set[str] = set()

    def add_candidate(path: Path) -> None:
        if not path.is_file():
            return
        resolved = str(path.resolve())
        if resolved in seen:
            return
        seen.add(resolved)
        candidates.append(path)

    for key in keys:
        key_dir = base_dir / key
        if key_dir.is_dir():
            for path in key_dir.rglob("*"):
                add_candidate(path)

    if not candidates:
        for path in base_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(base_dir)).lower()
            if any(key in rel for key in keys):
                add_candidate(path)

    if not candidates:
        return None

    return max(candidates, key=lambda path: path.stat().st_mtime)


@app.get("/clients/{client_id}/backup")
def get_client_backup_info(
    client_id: int,
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Return latest backup file metadata for a client detail page."""
    client = db.query(models.Client).filter_by(id=client_id).first()
    if client is None:
        raise HTTPException(status_code=404, detail="client not found")

    serial_number = get_client_serial_number(db, int(client.id))
    latest_file = find_latest_client_backup_file(client.hostname, serial_number)
    if latest_file is None:
        return {
            "available": False,
            "clientId": client_id,
            "hostname": client.hostname,
            "serialNumber": serial_number,
            "backup": None,
        }

    stat = latest_file.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return {
        "available": True,
        "clientId": client_id,
        "hostname": client.hostname,
        "serialNumber": serial_number,
        "backup": {
            "filename": latest_file.name,
            "sizeBytes": int(stat.st_size),
            "updatedAt": modified_at.isoformat(),
            "updatedAtDisplay": format_display_datetime_util(modified_at, DISPLAY_TIMEZONE),
        },
    }


@app.get("/clients/{client_id}/backup/download")
def download_client_backup(
    client_id: int,
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Download latest backup file for a client."""
    client = db.query(models.Client).filter_by(id=client_id).first()
    if client is None:
        raise HTTPException(status_code=404, detail="client not found")

    serial_number = get_client_serial_number(db, int(client.id))
    latest_file = find_latest_client_backup_file(client.hostname, serial_number)
    if latest_file is None:
        raise HTTPException(status_code=404, detail="client backup not found")

    return FileResponse(
        path=str(latest_file),
        media_type="application/octet-stream",
        filename=latest_file.name,
    )


@app.get("/leases")
def list_leases(
    vpn_type: str | None = None,
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Return lease list with computed connection status."""
    conn_sets = VPN_CFG_MGR.collect_connection_sets(
        openvpn_server_conf=OPENVPN_SERVER_CONF,
        openvpn_status_file=OPENVPN_STATUS_FILE,
        openvpn_legacy_status_file=OPENVPN_LEGACY_STATUS_FILE,
        sfos_server_conf=SFOS_SERVER_CONF,
        sfos_status_file=SFOS_STATUS_FILE,
    )
    openvpn_connected = conn_sets["openvpnConnected"]
    openvpn_legacy_connected = conn_sets["openvpnLegacyConnected"]
    sfos_connected = conn_sets["sfosConnected"]

    clients = db.query(models.Client).order_by(models.Client.updated_at.desc()).all()
    assigned_ip_map = build_assigned_ip_map(db)
    lease_activity_map = {
        int(row.client_id): bool(row.is_active)
        for row in db.query(models.IPLease.client_id, models.IPLease.is_active).all()
        if row.client_id is not None
    }

    result = []
    for client in clients:
        display_vpn_type = VPN_CFG_MGR.normalize_display_vpn_type(
            client.vpn_type,
            is_legacy_openvpn=(client.vpn_type == "openvpn" and is_legacy_openvpn_client(client)),
        )

        if vpn_type and display_vpn_type != vpn_type:
            continue

        identity = (client.hostname or "").strip()
        assigned_ip = assigned_ip_map.get(client.id, "")
        is_active = lease_activity_map.get(client.id, True)

        status = VPN_CFG_MGR.compute_client_connection_status(
            display_vpn_type=display_vpn_type,
            is_active=is_active,
            identity=identity,
            openvpn_connected=openvpn_connected,
            openvpn_legacy_connected=openvpn_legacy_connected,
            sfos_connected=sfos_connected,
        )

        result.append(
            {
                "vpnType": display_vpn_type,
                "identity": identity,
                "assignedIp": assigned_ip,
                "isActive": is_active,
                "status": status,
                "updatedAt": format_display_datetime_util(client.updated_at, DISPLAY_TIMEZONE),
            }
        )
    return result


def lease_pool_stats(db: Session, vpn_type: str, cidr: str, gateway_ip: str | None = None) -> dict:
    """Compute total/used/remaining IP counts for a CIDR."""
    return IP_LEASE_MGR.lease_pool_stats(db, vpn_type, cidr, gateway_ip)


@app.get("/leases/stats")
def get_lease_stats(
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Return lease pool statistics for SG and XGS."""
    return {
        "openvpn": lease_pool_stats(
            db=db,
            vpn_type="openvpn",
            cidr=OPENVPN_CIDR,
            gateway_ip=OPENVPN_GATEWAY_IP,
        ),
        "openvpn-legacy": lease_pool_stats(
            db=db,
            vpn_type="openvpn-legacy",
            cidr=OPENVPN_LEGACY_CIDR,
            gateway_ip=OPENVPN_LEGACY_GATEWAY_IP,
        ),
        "sfos": lease_pool_stats(
            db=db,
            vpn_type="sfos",
            cidr=SFOS_CIDR,
            gateway_ip=SFOS_GATEWAY_IP,
        ),
    }


@app.get("/system/status")
def system_status(
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Return service statuses and host resource metrics."""
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    services = VPN_CFG_MGR.build_runtime_service_statuses(
        db_ok=db_ok,
        openvpn_port=OPENVPN_PORT,
        openvpn_legacy_port=OPENVPN_LEGACY_PORT,
        sfos_vpn_port=SFOS_VPN_PORT,
    )
    return {
        "services": services,
        "resources": MONITOR.get_resource_usage(),
        "dirs": {
            "pki": Path(PKI).exists(),
            "ccd": Path(CCD).exists(),
            "ccd_sfos": Path(CCD_SFOS).exists(),
        },
    }


@app.get("/runtime-guard/logs")
def get_runtime_guard_logs(
    _: None = Depends(require_security_console_token),
):
    """Return recent runtime guard log lines for the security console."""
    entries = parse_runtime_guard_entries()
    return {"path": RUNTIME_GUARD_LOG_FILE, "entries": entries}


@app.get("/feature-logs/{feature}")
def get_feature_logs(
    feature: str,
    limit: int = 100,
    _: None = Depends(require_internal_access),
):
    """Return recent entries from a feature-specific log file."""
    feature_key = normalize_feature_log_name(feature)
    if feature_key not in FEATURE_LOG_FILES:
        raise HTTPException(status_code=404, detail=f"unknown feature log: {feature}")
    path, entries = read_feature_log_entries(feature_key, limit=limit)
    return {"feature": feature_key, "path": path, "entries": entries}


@app.get("/alerts/history")
def get_alert_history(
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Return recent alert/notification history."""
    record = get_backup_settings_record(db)
    logs = [entry for entry in reversed(parse_job_logs(record)) if is_alert_log_entry(entry)]
    return {
        "logs": logs[:200],
        "serverTs": int(datetime.now(tz=timezone.utc).timestamp()),
    }


@app.get("/backup/settings")
def get_backup_settings(
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Return masked backup settings and recent job logs for the settings page."""
    record = get_backup_settings_record(db)
    logs = list(reversed(parse_job_logs(record)[-100:]))
    return {"settings": serialize_backup_settings(record), "slack": serialize_slack_settings(record), "logs": logs}


@app.get("/backup/logs")
def get_backup_logs(
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Return backup job log history."""
    record = get_backup_settings_record(db)
    return {"logs": list(reversed(parse_job_logs(record)[-200:]))}


@app.get("/backup/status")
def get_backup_status(
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Return latest backup integrity summary without changing the UI."""
    record = get_backup_settings_record(db)
    return summarize_backup_status(record)


@app.post("/backup/settings/test")
def test_backup_settings(
    data: BackupSettingsPayload,
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Test FTP connectivity using current form values or the stored masked password."""
    record = get_backup_settings_record(db)
    password = resolve_backup_password_payload(data, record, require_present=True)
    payload = BackupSettingsPayload(
        ftpHost=data.ftpHost,
        ftpUsername=data.ftpUsername,
        ftpPassword=password,
        ftpRemotePath=data.ftpRemotePath,
        scheduleType=data.scheduleType,
        scheduleTime=data.scheduleTime,
        scheduleWeekday=data.scheduleWeekday,
        scheduleMonthday=data.scheduleMonthday,
        passwordChanged=True,
    )
    try:
        result = run_backup_test_connection(payload)
        append_backup_log(
            db,
            record,
            job_type="connection_test",
            status="success",
            message="FTP 연결 테스트 성공",
            detail=json.dumps(result, ensure_ascii=False, indent=2),
        )
        return result
    except HTTPException as exc:
        append_backup_log(
            db,
            record,
            job_type="connection_test",
            status="failed",
            message="FTP 연결 테스트 실패",
            detail=str(exc.detail),
        )
        raise
    except Exception as exc:
        append_backup_log(
            db,
            record,
            job_type="connection_test",
            status="failed",
            message="FTP 연결 테스트 실패",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise HTTPException(status_code=500, detail=f"FTP test failed: {exc}")


@app.post("/backup/settings")
def save_backup_settings(
    data: BackupSettingsPayload,
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Persist backup configuration with masked-password update semantics."""
    record = get_backup_settings_record(db)
    schedule_type, schedule_time, schedule_weekday, schedule_monthday = normalize_backup_schedule(
        data.scheduleType,
        data.scheduleTime,
        data.scheduleWeekday,
        data.scheduleMonthday,
    )
    password = resolve_backup_password_payload(data, record, require_present=False)
    record.ftp_host = (data.ftpHost or "").strip()
    record.ftp_username = (data.ftpUsername or "").strip()
    record.ftp_remote_path = (data.ftpRemotePath or "").strip()
    record.schedule_type = schedule_type
    record.schedule_time = schedule_time
    record.schedule_weekday = schedule_weekday
    record.schedule_monthday = schedule_monthday
    record.updated_by = "settings-ui"
    if data.passwordChanged:
        record.ftp_password_enc = encrypt_backup_secret(password)
    db.add(record)
    db.commit()
    db.refresh(record)
    append_backup_log(
        db,
        record,
        job_type="settings_update",
        status="success",
        message="백업 설정 저장 완료",
        detail=json.dumps(serialize_backup_settings(record), ensure_ascii=False, indent=2),
    )
    return {"ok": True, "settings": serialize_backup_settings(record)}


@app.post("/backup/run")
def run_backup_now(
    data: BackupRunPayload,
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Run a manual FTP backup immediately using the stored settings."""
    record = get_backup_settings_record(db)
    with BACKUP_LOCK:
        try:
            result = run_backup_job(db, record, trigger="manual")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"backup failed: {exc}")
    return {"ok": True, "result": result, "note": (data.note or "").strip()}


@app.post("/backup/restore/list")
def list_restore_backups(
    data: RestoreBrowsePayload,
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """List available backup bundle directories from the configured FTP path."""
    record = get_backup_settings_record(db)
    host = (data.ftpHost or "").strip()
    username = (data.ftpUsername or "").strip()
    password = (data.ftpPassword or "").strip()
    remote_path = (data.ftpRemotePath or "").strip() or "/"
    if not host or not username or not password:
        raise HTTPException(status_code=400, detail="ftp host, username, and password are required")
    try:
        ftp = ftp_connect(host, username, password)
        try:
            resolved = ensure_ftp_remote_dir(ftp, remote_path)
            backups = list_ftp_backup_directories(ftp, resolved)
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()
        append_backup_log(
            db,
            record,
            job_type="restore_list",
            status="success",
            trigger="manual",
            message="복구 백업 목록 조회 완료",
            detail=json.dumps({"remotePath": resolved, "backups": backups}, ensure_ascii=False, indent=2),
        )
        return {"ok": True, "remotePath": resolved, "backups": backups}
    except HTTPException:
        raise
    except Exception as exc:
        append_backup_log(
            db,
            record,
            job_type="restore_list",
            status="failed",
            trigger="manual",
            message="복구 백업 목록 조회 실패",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise HTTPException(status_code=500, detail=f"restore list failed: {exc}")


@app.post("/backup/restore/run")
def run_restore_now(
    data: RestoreRunPayload,
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Download a selected backup bundle and validate or restore it."""
    with BACKUP_LOCK:
        try:
            result = run_restore_job(db, data)
        except HTTPException:
            raise
        except RestoreExecutionError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        except Exception as exc:
            record = get_backup_settings_record(db)
            append_backup_log(
                db,
                record,
                job_type="restore_validate" if (data.mode or "validate").lower() == "validate" else "restore",
                status="failed",
                trigger="manual",
                message="백업 검증 실패" if (data.mode or "validate").lower() == "validate" else "백업 복구 실패",
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise HTTPException(status_code=500, detail=f"restore failed: {exc}")
    return {"ok": True, "result": result}


@app.post("/backup/slack/settings")
def save_slack_settings(
    data: SlackSettingsPayload,
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Persist Slack alert settings for backup and operational notifications."""
    record = get_backup_settings_record(db)
    channel = (data.channel or "").strip() or SECURITY_SLACK_DEFAULT_CHANNEL
    bot_token = resolve_slack_token_payload(data, record, require_present=bool(data.enabled))
    record.slack_enabled = bool(data.enabled)
    record.slack_channel = channel
    record.slack_notify_certificate_expiry = bool(data.notifyCertificateExpiry)
    record.slack_notify_backup_completed = bool(data.notifyBackupCompleted)
    record.slack_notify_security_alert = bool(data.notifySecurityAlert)
    record.slack_notify_service_down = bool(data.notifyServiceDown)
    record.slack_notify_resource_threshold = bool(data.notifyResourceThreshold)
    record.slack_cpu_threshold = normalize_slack_threshold(data.cpuThreshold, "cpu threshold")
    record.slack_memory_threshold = normalize_slack_threshold(data.memoryThreshold, "memory threshold")
    record.slack_disk_threshold = normalize_slack_threshold(data.diskThreshold, "disk threshold")
    record.updated_by = "security-console"
    if data.tokenChanged:
        record.slack_bot_token_enc = encrypt_backup_secret(bot_token) if bot_token else ""
    db.add(record)
    db.commit()
    db.refresh(record)
    append_backup_log(
        db,
        record,
        job_type="slack_settings",
        status="success",
        message="슬랙 알림 설정 저장",
        detail=json.dumps(serialize_slack_settings(record), ensure_ascii=False, indent=2),
    )
    return {"ok": True, "slack": serialize_slack_settings(record)}


@app.post("/backup/client/upload")
async def upload_client_backup(
    request: Request,
    hostname: str = Form(...),
    timestamp: int = Form(...),
    signature: str = Form(...),
    keyId: str = Form(default=""),
    file: UploadFile = File(...),
):
    """Receive a backup file uploaded from an enrolled SG client over VPN."""

    def sanitize_backup_path_segment(value: str, default: str) -> str:
        normalized = re.sub(r"[^\w.-]+", "_", (value or "").strip(), flags=re.UNICODE)
        normalized = normalized.strip("._-")
        return normalized or default

    verify_fresh_timestamp(timestamp)
    msg = f"{hostname}:{timestamp}"
    if not hmac.compare_digest(sign(msg), signature):
        raise HTTPException(status_code=400, detail="bad signature")
    normalized_hostname = hostname.strip()
    if not HOSTNAME_PATTERN.fullmatch(normalized_hostname):
        raise HTTPException(status_code=400, detail="invalid hostname")
    guard_backup_upload_replay(normalized_hostname, timestamp, signature)

    provided_key_id = (keyId or "").strip()
    if provided_key_id and provided_key_id != BACKUP_UPLOAD_HMAC_KEY_ID:
        LOGGER.warning(
            "backup upload keyId mismatch hostname=%s provided=%s expected=%s",
            normalized_hostname,
            provided_key_id,
            BACKUP_UPLOAD_HMAC_KEY_ID,
        )

    raw_filename = file.filename or f"{normalized_hostname}_{datetime.now().strftime('%Y.%m.%d')}.abf"
    safe_filename = re.sub(r"[^a-zA-Z0-9._\-]", "_", raw_filename)
    if not safe_filename:
        safe_filename = f"{normalized_hostname}_{datetime.now().strftime('%Y.%m.%d')}.abf"

    backup_type = "sg"
    hostname_segment = sanitize_backup_path_segment(normalized_hostname, "_unknown_host")
    with Session(DB_ENGINE) as db:
        client = db.query(models.Client).filter_by(hostname=normalized_hostname).order_by(models.Client.updated_at.desc()).first()
        if client:
            if client.vpn_type == "sfos":
                backup_type = "xgs"
            elif bool(client.is_legacy):
                backup_type = "sg_legacy"

    dest_dir = Path(CLIENT_BACKUP_BASE_DIR) / backup_type / hostname_segment
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / safe_filename
    chunk_size = 1024 * 1024
    total_bytes = 0
    try:
        with dest_path.open("wb") as fh:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > BACKUP_UPLOAD_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="backup file too large")
                fh.write(chunk)
    except HTTPException:
        try:
            dest_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except Exception:
        try:
            dest_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        await file.close()

    # Keep the latest upload and prune older backups only after a 2-day grace period.
    retention_seconds = 2 * 24 * 60 * 60
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    pruned_files: list[str] = []
    for candidate in dest_dir.glob("*"):
        if not candidate.is_file() or candidate == dest_path:
            continue
        try:
            age_seconds = now_ts - candidate.stat().st_mtime
        except OSError:
            continue
        if age_seconds >= retention_seconds:
            try:
                candidate.unlink()
                pruned_files.append(candidate.name)
            except OSError:
                LOGGER.warning("failed to prune old client backup path=%s", candidate)

    LOGGER.info(
        "client backup received hostname=%s type=%s file=%s size=%d pruned=%d",
        normalized_hostname,
        backup_type,
        safe_filename,
        total_bytes,
        len(pruned_files),
    )
    append_feature_log("backup", {
        "event": "client-backup-received",
        "hostname": normalized_hostname,
        "backupType": backup_type,
        "keyId": provided_key_id or BACKUP_UPLOAD_HMAC_KEY_ID,
        "file": safe_filename,
        "size": total_bytes,
        "pruned": pruned_files,
        "dest": str(dest_path.relative_to(CLIENT_BACKUP_BASE_DIR)),
    })
    return {
        "ok": True,
        "file": safe_filename,
        "path": str(dest_path.relative_to(CLIENT_BACKUP_BASE_DIR)),
        "pruned": pruned_files,
    }


@app.post("/backup/slack/test")
def send_slack_test_message(
    data: SlackTestPayload,
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Send a sample Slack message using the saved bot token and channel."""
    record = get_backup_settings_record(db)
    if not record.slack_bot_token_enc:
        raise HTTPException(status_code=400, detail="slack bot token is not configured")
    channel = (record.slack_channel or SECURITY_SLACK_DEFAULT_CHANNEL).strip() or SECURITY_SLACK_DEFAULT_CHANNEL
    bot_token = decrypt_backup_secret(record.slack_bot_token_enc)
    try:
        text, blocks = build_slack_test_payload((data.templateType or "").strip())
        response = send_slack_message(bot_token, channel, text, blocks)
        append_backup_log(
            db,
            record,
            job_type="slack_test",
            status="success",
            message="슬랙 테스트 메시지 전송 완료",
            detail=json.dumps({"templateType": data.templateType, "channel": channel, "response": response}, ensure_ascii=False, indent=2),
        )
        return {"ok": True, "channel": channel, "templateType": data.templateType, "response": response}
    except HTTPException:
        raise
    except Exception as exc:
        append_backup_log(
            db,
            record,
            job_type="slack_test",
            status="failed",
            message="슬랙 테스트 메시지 전송 실패",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise HTTPException(status_code=500, detail=f"slack test failed: {exc}")


@app.get("/security/monitor")
def get_security_monitor(_: None = Depends(require_security_console_token)):
    """Return hidden security monitor state for manual review and release."""
    with Session(DB_ENGINE) as db:
        record = get_backup_settings_record(db)
    return ALERT_MGR.get_security_monitor_payload(
        record=record,
        webhook_enabled=bool(SECURITY_WEBHOOK_URL),
        slack_enabled=bool(record.slack_enabled and record.slack_bot_token_enc),
        slack_channel=record.slack_channel or SECURITY_SLACK_DEFAULT_CHANNEL,
    )


@app.post("/security/console/access")
def open_security_console(
    request: Request,
    data: DebugAccessPayload,
    _: dict | None = Depends(require_web_or_internal_access),
):
    """Open the hidden security console after internal password verification."""
    source_ip = resolve_request_ip(request) or "-"
    if not DEBUG_MODE_PASSWORD:
        raise HTTPException(status_code=503, detail="debug mode password not configured")
    if not hmac.compare_digest((data.password or "").strip(), DEBUG_MODE_PASSWORD):
        append_feature_log("security", {
            "event": "security-console-password-failure",
            "sourceIp": source_ip,
        })
        raise HTTPException(status_code=403, detail="invalid admin password")
    LOGGER.warning("security console unlocked from source_ip=%s", source_ip)
    append_feature_log("security", {
        "event": "security-console-opened",
        "sourceIp": source_ip,
    })
    return {"ok": True, "token": ""}


@app.post("/security/monitor/settings")
def update_security_monitor_settings(
    request: Request,
    data: SecuritySettingsPayload,
    _: None = Depends(require_security_console_token),
):
    """Update security monitor thresholds without restarting the service."""
    if data.alertThreshold <= 0 or data.banThreshold <= 0 or data.windowSeconds <= 0:
        raise HTTPException(status_code=400, detail="all values must be positive")
    if data.alertThreshold > data.banThreshold:
        raise HTTPException(status_code=400, detail="alert threshold must be <= ban threshold")
    ALERT_MGR.update_monitor_settings(
        alert_threshold=int(data.alertThreshold),
        ban_threshold=int(data.banThreshold),
        window_seconds=int(data.windowSeconds),
    )
    append_feature_log("security", {
        "event": "security-monitor-settings-updated",
        "sourceIp": resolve_request_ip(request) or "-",
        "alertThreshold": int(data.alertThreshold),
        "banThreshold": int(data.banThreshold),
        "windowSeconds": int(data.windowSeconds),
    })
    return {"ok": True}


@app.post("/security/monitor/unban")
def unban_security_ip(
    request: Request,
    data: SecurityUnbanPayload,
    _: None = Depends(require_security_console_token),
):
    """Release a banned IP after manual review."""
    try:
        target_ip = canonical_ip_util(data.ip)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid IP address: {data.ip!r}")
    ALERT_MGR.unban_ip(target_ip, note=(data.note or "").strip())
    append_feature_log("security", {
        "event": "security-ip-unbanned",
        "sourceIp": resolve_request_ip(request) or "-",
        "targetIp": target_ip,
        "note": (data.note or "").strip(),
    })
    return {"ok": True}


@app.post("/security/monitor/unenroll")
def unenroll_security_client(
    request: Request,
    data: SecurityUnenrollPayload,
    _: None = Depends(require_security_console_token),
    db: Session = Depends(get_db),
):
    """Delete enrolled client records from security monitor review flow."""
    hostname = normalize_hostname(data.hostname)
    vpn_type = (data.vpnType or "").strip().lower()
    if vpn_type not in {"openvpn", "sfos"}:
        raise HTTPException(status_code=400, detail="vpnType must be one of: openvpn, sfos")

    client = db.query(models.Client).filter_by(hostname=hostname, vpn_type=vpn_type).first()
    if client is None:
        raise HTTPException(status_code=404, detail="client not found")

    released_ip_count = db.query(models.IPLease).filter(models.IPLease.client_id == client.id).delete(synchronize_session=False)
    credential_deleted = db.query(models.Credential).filter(models.Credential.client_id == client.id).delete(synchronize_session=False)

    serial_unlinked = 0
    if vpn_type == "sfos":
        # Keep asset records, but detach serial ownership from this SFOS client.
        serial_unlinked = db.query(models.EquipmentAsset).filter(models.EquipmentAsset.client_id == client.id).update(
            {models.EquipmentAsset.client_id: None},
            synchronize_session=False,
        )

    deleted_client_id = int(client.id)
    db.delete(client)
    db.commit()

    append_feature_log("security", {
        "event": "security-client-unenrolled",
        "sourceIp": resolve_request_ip(request) or "-",
        "hostname": hostname,
        "vpnType": vpn_type,
        "releasedIpCount": int(released_ip_count),
        "credentialDeleted": int(credential_deleted),
        "serialUnlinked": int(serial_unlinked),
        "note": (data.note or "").strip(),
    })

    return {
        "ok": True,
        "hostname": hostname,
        "vpnType": vpn_type,
        "deletedClientId": deleted_client_id,
        "releasedIpCount": int(released_ip_count),
        "serialUnlinked": int(serial_unlinked),
    }


@app.get("/enroll/vpn_enroll.sh")
def get_vpn_enroll_script(
    request: Request,
    hostname: str | None = None,
    token: str | None = None,
    x_enroll_token: str | None = Header(default=None, alias="X-Enroll-Token"),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Serve generated vpn_enroll.sh for internal requests or callers with a valid enroll token."""
    require_internal_or_enroll_token(
        request=request,
        token=token,
        x_enroll_token=x_enroll_token,
        x_internal_token=x_internal_token,
    )
    default_hostname = normalize_hostname(hostname) if hostname else ""
    content = render_vpn_enroll_script(default_hostname=default_hostname, request=request)
    return Response(
        content=content,
        media_type="text/x-shellscript; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="vpn_enroll.sh"'},
    )


@app.post("/enroll")
def enroll(request: Request, data: EnrollPayload, db: Session = Depends(get_db)):
    """Handle SG enroll and return client bundle archive."""
    source_ip = resolve_request_ip(request)
    hostname = (data.hostname or "").strip()
    serial_number = normalize_serial_number(data.serialNumber or "") if getattr(data, "serialNumber", None) else ""
    device_model = normalize_device_model(data.deviceModel or "") if getattr(data, "deviceModel", None) else ""
    try:
        hostname = normalize_hostname(data.hostname)
        mac = normalize_mac(data.mac or "")
        serial_number = normalize_serial_number(data.serialNumber or "")
        device_model = normalize_device_model(data.deviceModel or "")
        data = data.model_copy(
            update={
                "hostname": hostname,
                "mac": mac or None,
                "serialNumber": serial_number or None,
                "deviceModel": device_model or None,
            }
        )
        verify_openvpn_enroll(data)
        is_legacy_client = int(data.selectedVpnPort or OPENVPN_PORT) == int(OPENVPN_LEGACY_PORT)
        runtime = resolve_openvpn_runtime(is_legacy_client)
        client = db.query(models.Client).filter_by(hostname=hostname, vpn_type="openvpn").first()
        if not client:
            client = models.Client(
                hostname=hostname,
                mac=mac,
                vpn_type="openvpn",
                cert_cn=hostname,
                is_legacy=is_legacy_client,
                status="active",
            )
            db.add(client)
            db.commit()
            db.refresh(client)
        else:
            client.mac = mac
            client.cert_cn = hostname
            client.is_legacy = is_legacy_client
            client.status = "active"
            db.commit()

        ip = alloc_ip_openvpn(db, is_legacy=is_legacy_client, client_id=client.id)

        ca_cert, certificate, key = ensure_client_material(hostname)
        _, _, script_server_port = resolve_enroll_script_target(request)
        archive_path, tmp_root_dir = write_openvpn_bundle(
            hostname,
            ca_cert,
            certificate,
            key,
            vpn_port=int(runtime["vpn_port"]),
            api_port=int(script_server_port),
        )
        sync_ip_lease_binding(db, client=client, assigned_ip=ip)
        if serial_number:
            asset = upsert_equipment_asset(
                db,
                serial_number=serial_number,
                device_model=device_model,
                client_id=client.id,
                event_type=ASSET_HISTORY_ENROLL,
                event_summary="SSL enroll로 장비 정보가 반영되었습니다.",
                event_detail=f"호스트명 {hostname} 장비에서 수집한 시리얼 {serial_number} 정보가 자동 반영되었습니다.",
                created_by="enroll",
            )
            if asset is not None:
                if asset.asset_status != ASSET_STATUS_LEASED:
                    asset.asset_status = ASSET_STATUS_LEASED
                if asset.sale_type != ASSET_SALE_TYPE_LEASE:
                    asset.sale_type = ASSET_SALE_TYPE_LEASE
            db.commit()
            if asset is not None:
                try_send_inventory_sync(
                    db,
                    asset=asset,
                    client=client,
                    assigned_ip=ip,
                    trigger="enroll",
                )

        VPN_CFG_MGR.write_ccd_entry_and_route(
            ccd_dir=str(runtime["ccd_dir"]),
            hostname=hostname,
            assigned_ip=ip,
            route_gateway_ip=str(runtime["route_gateway_ip"]),
            push_remote_network=OPENVPN_PUSH_REMOTE_NETWORK_1,
        )

        register_security_success(source_ip, "/enroll", hostname)
        append_feature_log("enroll", {
            "event": "enroll-success",
            "endpoint": "/enroll",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "assignedIp": ip,
            "legacy": is_legacy_client,
            "vpnPort": int(runtime["vpn_port"]),
        })
        # BackgroundTask로 응답 전송 후 임시 디렉터리(개인키 포함) 정리
        return FileResponse(
            archive_path,
            media_type="application/gzip",
            filename=f"{hostname}.tar.gz",
            background=BackgroundTask(shutil.rmtree, str(tmp_root_dir), True),
        )
    except HTTPException as exc:
        db.rollback()
        append_feature_log("enroll", {
            "event": "enroll-http-error",
            "endpoint": "/enroll",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "statusCode": exc.status_code,
            "detail": str(exc.detail),
        })
        if is_monitored_auth_failure(exc.detail):
            register_security_failure(
                source_ip=source_ip,
                endpoint="/enroll",
                reason=str(exc.detail),
                hostname=(data.hostname or "").strip(),
            )
        raise
    except Exception as e:
        db.rollback()
        LOGGER.exception("enroll failed hostname=%s source_ip=%s", hostname, source_ip)
        append_feature_log("enroll", {
            "event": "enroll-exception",
            "endpoint": "/enroll",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "error": f"{type(e).__name__}: {e}",
        })
        raise HTTPException(status_code=500, detail=f"Enroll failed: {e}")


@app.post("/enroll/apc")
def apc(
    request: Request,
    data: EnrollApcPayload,
    _: None = Depends(require_internal_access),
    db: Session = Depends(get_db),
):
    """Handle XGS enroll and return APC payload file."""
    source_ip = resolve_request_ip(request)
    hostname = (data.hostname or "").strip()
    serial_number = normalize_serial_number(data.serialNumber or "") if getattr(data, "serialNumber", None) else ""
    device_model = normalize_device_model(data.deviceModel or "") if getattr(data, "deviceModel", None) else ""
    try:
        hostname = normalize_hostname(data.hostname)
        mac = normalize_mac(data.mac or "")
        serial_number = normalize_serial_number(data.serialNumber or "")
        device_model = normalize_device_model(data.deviceModel or "")
        data = data.model_copy(
            update={
                "hostname": hostname,
                "mac": mac or None,
                "serialNumber": serial_number or None,
                "deviceModel": device_model or None,
            }
        )
        verify_sfos_enroll(data)
        ts = int(data.timestamp)
        existing_client = db.query(models.Client).filter_by(hostname=hostname, vpn_type="sfos").first()
        if not existing_client:
            existing_client = models.Client(
                hostname=hostname,
                mac=mac,
                vpn_type="sfos",
                cert_cn=hostname,
                status="active",
            )
            db.add(existing_client)
            db.flush()
            db.refresh(existing_client)
        else:
            existing_client.mac = mac
            existing_client.cert_cn = hostname
            existing_client.status = "active"
            db.flush()
        ip = alloc_ip_sfos(db, client_id=existing_client.id)
        VPN_CFG_MGR.write_ccd_entry_and_route(
            ccd_dir=CCD_SFOS,
            hostname=hostname,
            assigned_ip=ip,
            route_gateway_ip=SFOS_TUN_SERIAL_IP,
            push_remote_network=OPENVPN_PUSH_REMOTE_NETWORK_1,
        )

        username, password = upsert_client_and_credential(db, hostname, mac, ip, ts, auto_commit=False)
        ca_cert, certificate, key = ensure_client_material(hostname)
        if serial_number:
            asset = upsert_equipment_asset(
                db,
                serial_number=serial_number,
                device_model=device_model,
                client_id=existing_client.id,
                event_type=ASSET_HISTORY_APC,
                event_summary="APC enroll로 장비 정보가 반영되었습니다.",
                event_detail=f"호스트명 {hostname} 장비의 APC 등록 시 시리얼 {serial_number} / 모델 {device_model or '-'} 정보가 반영되었습니다.",
                created_by="apc-enroll",
            )
            db.flush()
            if asset is not None:
                try_send_inventory_sync(
                    db,
                    asset=asset,
                    client=existing_client,
                    assigned_ip=ip,
                    trigger="apc-enroll",
                )

        db.commit()

        apc_obj = {
            "username": username,
            "password": password,
            "server_dn": SFOS_SERVER_DN,
            "protocol": "tcp",
            "encryption_algorithm": "AES-128-CBC",
            "server_port": str(SFOS_VPN_PORT),
            "authentication_algorithm": "SHA256",
            "ca_cert": ca_cert,
            "server_address": [SFOS_SERVER_IP],
            "certificate": certificate,
            "key": key,
        }
        append_feature_log("apc", {
            "event": "apc-success",
            "endpoint": "/enroll/apc",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "assignedIp": ip,
        })
        return Response(
            content=json.dumps(apc_obj, ensure_ascii=False),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{hostname}.apc"'},
        )
    except HTTPException as exc:
        db.rollback()
        append_feature_log("apc", {
            "event": "apc-http-error",
            "endpoint": "/enroll/apc",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "statusCode": exc.status_code,
            "detail": str(exc.detail),
        })
        raise
    except Exception as e:
        db.rollback()
        LOGGER.exception("apc generation failed hostname=%s source_ip=%s", hostname, source_ip)
        append_feature_log("apc", {
            "event": "apc-exception",
            "endpoint": "/enroll/apc",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "error": f"{type(e).__name__}: {e}",
        })
        raise HTTPException(status_code=500, detail=f"failed to generate apc: {e}")


@app.post("/admin/apc/request")
def admin_apc_request(
    request: Request,
    data: AdminApcPayload,
    _: dict | None = Depends(require_web_or_internal_access),
    db: Session = Depends(get_db),
):
    """Handle manual APC creation request from admin UI."""
    source_ip = resolve_request_ip(request)
    hostname = (data.hostname or "").strip()
    serial_number = normalize_serial_number(data.serialNumber or "") if getattr(data, "serialNumber", None) else ""
    device_model = normalize_device_model(data.deviceModel or "") if getattr(data, "deviceModel", None) else ""
    admin_password = (data.adminPassword or "").strip()
    try:
        hostname = normalize_hostname(data.hostname)
        mac = normalize_mac(data.mac)
        serial_number = normalize_serial_number(data.serialNumber or "")
        device_model = normalize_device_model(data.deviceModel or "")
        if not admin_password:
            raise HTTPException(status_code=400, detail="admin password is required")

        ts = int(datetime.now(tz=timezone.utc).timestamp())
        existing_client = db.query(models.Client).filter_by(hostname=hostname, vpn_type="sfos").first()
        if not existing_client:
            existing_client = models.Client(
                hostname=hostname,
                mac=mac,
                vpn_type="sfos",
                cert_cn=hostname,
                status="active",
            )
            db.add(existing_client)
            db.flush()
            db.refresh(existing_client)
        else:
            existing_client.mac = mac
            existing_client.cert_cn = hostname
            existing_client.status = "active"
            db.flush()
        ip = alloc_ip_sfos(db, client_id=existing_client.id)
        VPN_CFG_MGR.write_ccd_entry_and_route(
            ccd_dir=CCD_SFOS,
            hostname=hostname,
            assigned_ip=ip,
            route_gateway_ip=SFOS_TUN_SERIAL_IP,
            push_remote_network=OPENVPN_PUSH_REMOTE_NETWORK_1,
        )

        username, password = upsert_client_and_credential(
            db,
            hostname,
            mac,
            ip,
            ts,
            admin_password=admin_password,
            auto_commit=False,
        )
        ca_cert, certificate, key = ensure_client_material(hostname)
        if serial_number:
            asset = upsert_equipment_asset(
                db,
                serial_number=serial_number,
                device_model=device_model,
                client_id=existing_client.id,
                event_type=ASSET_HISTORY_APC,
                event_summary="관리자 APC 요청으로 장비 정보가 반영되었습니다.",
                event_detail=f"호스트명 {hostname} 관리자 APC 생성 요청에서 시리얼 {serial_number} / 모델 {device_model or '-'} 정보가 반영되었습니다.",
                created_by="admin-apc",
            )
            db.flush()
            if asset is not None:
                try_send_inventory_sync(
                    db,
                    asset=asset,
                    client=existing_client,
                    assigned_ip=ip,
                    trigger="admin-apc",
                )

        db.commit()
        apc_obj = {
            "username": username,
            "password": password,
            "server_dn": SFOS_SERVER_DN,
            "protocol": "tcp",
            "encryption_algorithm": "AES-128-CBC",
            "server_port": str(SFOS_VPN_PORT),
            "authentication_algorithm": "SHA256",
            "ca_cert": ca_cert,
            "server_address": [SFOS_SERVER_IP],
            "certificate": certificate,
            "key": key,
        }
        append_feature_log("admin-apc", {
            "event": "admin-apc-success",
            "endpoint": "/admin/apc/request",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "assignedIp": ip,
        })
        return Response(
            content=json.dumps(apc_obj, ensure_ascii=False),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{hostname}.apc"'},
        )
    except HTTPException as exc:
        db.rollback()
        append_feature_log("admin-apc", {
            "event": "admin-apc-http-error",
            "endpoint": "/admin/apc/request",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "statusCode": exc.status_code,
            "detail": str(exc.detail),
        })
        raise
    except Exception as e:
        db.rollback()
        LOGGER.exception("admin apc generation failed hostname=%s source_ip=%s", hostname, source_ip)
        append_feature_log("admin-apc", {
            "event": "admin-apc-exception",
            "endpoint": "/admin/apc/request",
            "sourceIp": source_ip,
            "hostname": hostname,
            "serialNumber": serial_number,
            "deviceModel": device_model,
            "error": f"{type(e).__name__}: {e}",
        })
        raise HTTPException(status_code=500, detail=f"failed to generate admin apc: {e}")