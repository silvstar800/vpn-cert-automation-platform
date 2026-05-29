from __future__ import annotations

import sys
from pathlib import Path

from db import SessionLocal
import models
from app import (
    CCD,
    CCD_LEGACY,
    OPENVPN_LEGACY_GATEWAY_IP,
    OPENVPN_LEGACY_SERVER_CONF,
    alloc_ip_openvpn,
    canonical_ip,
    write_ccd_entry_and_route,
)


def main() -> int:
    hostname = "miso-1004-clinic"
    db = SessionLocal()
    try:
        client = db.query(models.Client).filter_by(hostname=hostname, vpn_type="openvpn").first()
        if client is None:
            print(f"client not found: {hostname}", file=sys.stderr)
            return 1

        client.is_legacy = True

        lease = db.query(models.IPLease).filter_by(client_id=client.id).first()
        current_ip = canonical_ip(lease.assigned_ip) if lease and lease.assigned_ip else "-"

        # Remove incorrect SG lease so the legacy allocator can assign from the 212 pool.
        if lease is not None:
            db.delete(lease)
            db.flush()

        desired_ip = alloc_ip_openvpn(db, is_legacy=True, client_id=client.id)

        standard_ccd = Path(CCD) / hostname
        if standard_ccd.exists():
            standard_ccd.unlink()

        write_ccd_entry_and_route(
            ccd_dir=CCD_LEGACY,
            hostname=hostname,
            assigned_ip=desired_ip,
            route_gateway_ip=OPENVPN_LEGACY_GATEWAY_IP,
            server_conf_path=OPENVPN_LEGACY_SERVER_CONF,
        )
        db.commit()

        print(f"{hostname}: {current_ip} -> {desired_ip}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
