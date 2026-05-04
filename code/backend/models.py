from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    TIMESTAMP,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.sql import func
from db import Base


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("hostname", "vpn_type", name="uq_clients_hostname_vpn"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    hostname = Column(String(128), nullable=False)
    mac = Column(String(32))
    vpn_type = Column(String(16), nullable=False)
    cert_cn = Column(String(128), nullable=False)
    is_legacy = Column(Boolean, nullable=False, default=False)
    status = Column(String(16), default="active")
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class Credential(Base):
    __tablename__ = "credentials"

    id = Column(BigInteger, primary_key=True)
    client_id = Column(BigInteger, ForeignKey("clients.id"), unique=True)
    username = Column(String(256), nullable=False)
    password = Column(String(512), nullable=False)
    sfos_admin_password_enc = Column(String(512), nullable=False, default="")
    created_at = Column(TIMESTAMP, server_default=func.now())


class IPLease(Base):
    __tablename__ = "ip_leases"
    __table_args__ = (
        UniqueConstraint("client_id", name="uq_ip_leases_client"),
        UniqueConstraint("assigned_ip", name="uq_ip_leases_assigned_ip"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    client_id = Column(BigInteger, ForeignKey("clients.id"), nullable=False, index=True)
    assigned_ip = Column(INET, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class BackupSetting(Base):
    __tablename__ = "backup_settings"

    id = Column(BigInteger, primary_key=True)
    ftp_host = Column(String(256), nullable=False, default="")
    ftp_username = Column(String(256), nullable=False, default="")
    ftp_password_enc = Column(Text, nullable=False, default="")
    ftp_remote_path = Column(String(512), nullable=False, default="")
    schedule_type = Column(String(16), nullable=False, default="manual")
    schedule_time = Column(String(8), nullable=False, default="")
    schedule_weekday = Column(Integer, nullable=True)
    schedule_monthday = Column(Integer, nullable=True)
    slack_enabled = Column(Boolean, nullable=False, default=False)
    slack_bot_token_enc = Column(Text, nullable=False, default="")
    slack_channel = Column(String(128), nullable=False, default="#01-alert")
    slack_notify_certificate_expiry = Column(Boolean, nullable=False, default=True)
    slack_notify_backup_completed = Column(Boolean, nullable=False, default=True)
    slack_notify_security_alert = Column(Boolean, nullable=False, default=True)
    slack_notify_service_down = Column(Boolean, nullable=False, default=True)
    slack_notify_resource_threshold = Column(Boolean, nullable=False, default=True)
    slack_cpu_threshold = Column(Integer, nullable=False, default=90)
    slack_memory_threshold = Column(Integer, nullable=False, default=90)
    slack_disk_threshold = Column(Integer, nullable=False, default=90)
    job_logs_json = Column(Text, nullable=False, default="[]")
    last_run_key = Column(String(32), nullable=False, default="")
    updated_by = Column(String(128), nullable=False, default="system")
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class EquipmentAsset(Base):
    __tablename__ = "equipment_assets"
    __table_args__ = (
        UniqueConstraint("serial_number", name="uq_equipment_assets_serial_number"),
        UniqueConstraint("client_id", name="uq_equipment_assets_client_id"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    serial_number = Column(String(64), nullable=False, index=True)
    customer_name = Column(String(256), nullable=False, default="")
    customer_sync_status = Column(SmallInteger, nullable=False, default=0)
    device_model = Column(String(128), nullable=False, default="")
    license_flags = Column(Integer, nullable=False, default=0)
    license_start_date = Column(Date, nullable=True)
    license_end_date = Column(Date, nullable=True)
    sale_type = Column(SmallInteger, nullable=False, default=1)
    asset_status = Column(SmallInteger, nullable=False, default=3)
    client_id = Column(BigInteger, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class EquipmentAssetHistory(Base):
    __tablename__ = "equipment_asset_history"

    id = Column(BigInteger, primary_key=True, index=True)
    asset_id = Column(BigInteger, ForeignKey("equipment_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(SmallInteger, nullable=False, default=0)
    summary = Column(String(256), nullable=False, default="")
    detail = Column(Text, nullable=False, default="")
    created_by = Column(String(128), nullable=False, default="system")
    created_at = Column(TIMESTAMP, server_default=func.now())
