"""
Pydantic request/response schemas for certsvc API endpoints.

All BaseModel classes used by FastAPI endpoint handlers are defined here
and imported into app.py, keeping the route module free of schema clutter.
"""

from pydantic import BaseModel, Field


class EnrollPayload(BaseModel):
    hostname: str = Field(min_length=1, max_length=128)
    timestamp: int
    signature: str = Field(min_length=16, max_length=256)
    mac: str | None = Field(default="", max_length=32)
    serialNumber: str | None = Field(default="", max_length=64)
    deviceModel: str | None = Field(default="", max_length=128)
    selectedVpnPort: int | None = None
    opensslVersion: str | None = Field(default="", max_length=64)


class EnrollApcPayload(BaseModel):
    hostname: str = Field(min_length=1, max_length=128)
    mac: str | None = Field(default="", max_length=32)
    serialNumber: str | None = Field(default="", max_length=64)
    deviceModel: str | None = Field(default="", max_length=128)
    timestamp: int
    signature: str = Field(min_length=16, max_length=256)


class AdminApcPayload(BaseModel):
    hostname: str = Field(min_length=1, max_length=128)
    mac: str | None = Field(default="", max_length=32)
    serialNumber: str | None = Field(default="", max_length=64)
    deviceModel: str | None = Field(default="", max_length=128)
    adminPassword: str = Field(min_length=1, max_length=256)


class SecuritySettingsPayload(BaseModel):
    alertThreshold: int
    banThreshold: int
    windowSeconds: int


class SecurityUnbanPayload(BaseModel):
    ip: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default="", max_length=500)


class SecurityUnenrollPayload(BaseModel):
    hostname: str = Field(min_length=1, max_length=128)
    vpnType: str = Field(min_length=1, max_length=32)
    note: str | None = Field(default="", max_length=500)


class DebugAccessPayload(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class WebLoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class BackupSettingsPayload(BaseModel):
    ftpHost: str = Field(min_length=1, max_length=256)
    ftpUsername: str = Field(min_length=1, max_length=256)
    ftpPassword: str | None = Field(default="", max_length=256)
    ftpRemotePath: str = Field(min_length=1, max_length=512)
    scheduleType: str = Field(min_length=1, max_length=16)
    scheduleTime: str | None = Field(default="", max_length=8)
    scheduleWeekday: int | None = None
    scheduleMonthday: int | None = None
    passwordChanged: bool = False


class BackupRunPayload(BaseModel):
    note: str | None = Field(default="", max_length=500)


class RestoreBrowsePayload(BaseModel):
    ftpHost: str = Field(min_length=1, max_length=256)
    ftpUsername: str = Field(min_length=1, max_length=256)
    ftpPassword: str = Field(min_length=1, max_length=256)
    ftpRemotePath: str | None = Field(default="/", max_length=512)


class RestoreRunPayload(BaseModel):
    ftpHost: str = Field(min_length=1, max_length=256)
    ftpUsername: str = Field(min_length=1, max_length=256)
    ftpPassword: str = Field(min_length=1, max_length=256)
    ftpRemotePath: str | None = Field(default="/", max_length=512)
    backupId: str = Field(min_length=1, max_length=128)
    mode: str = Field(default="validate", max_length=16)


class SlackSettingsPayload(BaseModel):
    enabled: bool = False
    botToken: str | None = Field(default="", max_length=256)
    channel: str = Field(default="#01-alert", max_length=128)
    notifyCertificateExpiry: bool = True
    notifyBackupCompleted: bool = True
    notifySecurityAlert: bool = True
    notifyServiceDown: bool = True
    notifyResourceThreshold: bool = True
    cpuThreshold: int = 90
    memoryThreshold: int = 90
    diskThreshold: int = 90
    tokenChanged: bool = False


class SlackTestPayload(BaseModel):
    templateType: str = Field(min_length=1, max_length=64)


class InventorySyncPayload(BaseModel):
    serialNumber: str | None = Field(default="", max_length=64)
    hostname: str | None = Field(default="", max_length=128)
    assignedIp: str | None = Field(default="", max_length=64)
    vpnType: str | None = Field(default="", max_length=32)
    customerName: str | None = Field(default="", max_length=256)
    customerSyncStatus: int | None = None
    assetStatus: int | str | None = None
    saleType: int | str | None = None
    deviceModel: str | None = Field(default="", max_length=128)
    licenseInfo: dict | None = None

    model_config = {"extra": "allow"}


class InventorySyncTriggerPayload(BaseModel):
    serialNumber: str | None = Field(default="", max_length=64)
    hostname: str | None = Field(default="", max_length=128)


class EquipmentAssetManualCreatePayload(BaseModel):
    serialNumber: str = Field(min_length=1, max_length=64)
    deviceModel: str = Field(min_length=1, max_length=128)
