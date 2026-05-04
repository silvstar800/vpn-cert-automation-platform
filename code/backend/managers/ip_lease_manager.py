"""IP lease and allocation management."""

import ipaddress
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException


class IPLeaseManager:
    """Manages IP address allocation and leasing."""

    def __init__(self, db: Session = None):
        """Initialize with optional database session."""
        self.db = db

    def canonical_ip(self, ip_str: str | None) -> str:
        """Normalize IP/INET text into bare IP string (strip /32 etc.)."""
        if not ip_str:
            return "-"
        text_value = str(ip_str).strip()
        try:
            return str(ipaddress.ip_interface(text_value).ip)
        except ValueError:
            try:
                return str(ipaddress.ip_address(text_value))
            except ValueError:
                raise ValueError(f"invalid IP address: {text_value!r}")

    def ensure_remote(self, remote: str) -> str:
        """Ensure remote network has CIDR notation."""
        r = remote.strip()
        return r if "/" in r else f"{r}/32"

    def parse_cidr_network(self, cidr: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
        """Parse CIDR string into network object."""
        return ipaddress.ip_network(str(cidr).strip(), strict=False)

    def host_routes_from_cidr(self, cidr: str) -> list[str]:
        """Generate list of host IPs from CIDR subnet."""
        try:
            network = self.parse_cidr_network(cidr)
            return [str(host) for host in network.hosts()]
        except Exception:
            return []

    def allocate_ip_from_db(
        self, db: Session, vpn_type: str, cidr: str,
        gateway_ip: str | None = None, client_id: int | None = None
    ) -> str:
        """Get or allocate an IP lease from DB with conflict-safe retries."""
        if client_id is None:
            raise HTTPException(status_code=500, detail="client binding is required for IP allocation")
        
        # Import models here to avoid circular imports
        from models import IPLease, Client
        
        for _ in range(3):
            lease = db.query(IPLease).filter_by(client_id=client_id).first()
            if lease:
                normalized = self.canonical_ip(lease.assigned_ip)
                changed = False
                if str(lease.assigned_ip) != normalized:
                    lease.assigned_ip = normalized
                    changed = True
                if client_id is not None and lease.client_id != client_id:
                    lease.client_id = client_id
                    changed = True
                if changed:
                    db.commit()
                return normalized

            net = self.parse_cidr_network(cidr)
            used_ips = {
                self.canonical_ip(row.assigned_ip)
                for row in db.query(IPLease.assigned_ip).all()
                if row.assigned_ip and ipaddress.ip_address(self.canonical_ip(row.assigned_ip)) in net
            }
            for host in net.hosts():
                ip = str(host)
                if gateway_ip and ip == gateway_ip:
                    continue
                if ip in used_ips:
                    continue
                lease = IPLease(
                    client_id=client_id,
                    assigned_ip=ip,
                    is_active=True,
                )
                db.add(lease)
                try:
                    db.commit()
                    db.refresh(lease)
                    return self.canonical_ip(lease.assigned_ip)
                except IntegrityError:
                    db.rollback()
                    break

        raise HTTPException(status_code=500, detail=f"{vpn_type} IP pool exhausted or conflicted")

    def alloc_ip_openvpn(
        self, db: Session, is_legacy: bool = False, client_id: int | None = None,
        cidr: str = None, gateway_ip: str = None
    ) -> str:
        """Allocate OpenVPN IP address."""
        return self.allocate_ip_from_db(
            db=db,
            vpn_type="openvpn-legacy" if is_legacy else "openvpn",
            cidr=cidr,
            gateway_ip=gateway_ip,
            client_id=client_id,
        )

    def alloc_ip_sfos(
        self, db: Session, client_id: int | None = None,
        cidr: str = None, gateway_ip: str = None
    ) -> str:
        """Allocate SFOS VPN IP address."""
        return self.allocate_ip_from_db(
            db=db,
            vpn_type="sfos",
            cidr=cidr,
            gateway_ip=gateway_ip,
            client_id=client_id,
        )

    def lease_pool_stats(
        self, db: Session, vpn_type: str, cidr: str,
        gateway_ip: str | None = None
    ) -> dict:
        """Get statistics about IP lease pool."""
        from models import IPLease, Client
        
        net = self.parse_cidr_network(cidr)
        total = sum(1 for _ in net.hosts())

        if gateway_ip:
            try:
                gw = ipaddress.ip_address(gateway_ip)
                if gw in net:
                    total = max(total - 1, 0)
            except ValueError:
                pass

        # Query used IPs based on VPN type
        if vpn_type == "openvpn-legacy":
            rows = db.query(IPLease.assigned_ip).join(
                Client, Client.id == IPLease.client_id
            ).filter(
                Client.vpn_type == "openvpn",
                Client.is_legacy.is_(True),
            ).all()
            used_ips = {self.canonical_ip(row.assigned_ip) for row in rows if row.assigned_ip}
        elif vpn_type == "openvpn":
            rows = db.query(IPLease.assigned_ip).join(
                Client, Client.id == IPLease.client_id
            ).filter(
                Client.vpn_type == "openvpn",
                Client.is_legacy.is_(False),
            ).all()
            used_ips = {self.canonical_ip(row.assigned_ip) for row in rows if row.assigned_ip}
        elif vpn_type == "sfos":
            rows = db.query(IPLease.assigned_ip).join(
                Client, Client.id == IPLease.client_id
            ).filter(
                Client.vpn_type == "sfos",
            ).all()
            used_ips = {self.canonical_ip(row.assigned_ip) for row in rows if row.assigned_ip}
        else:
            used_ips = set()

        used = len(used_ips)
        remaining = max(total - used, 0)

        return {"total": total, "used": used, "remaining": remaining}

    def get_lease_stats(self, db: Session, cidr_config: dict, gateway_config: dict) -> dict:
        """Get overall statistics for all VPN lease pools.
        
        Args:
            db: Database session
            cidr_config: Dict with keys 'openvpn', 'openvpn_legacy', 'sfos' containing CIDR ranges
            gateway_config: Dict with gateway IPs for each VPN type
        """
        return {
            "openvpn": self.lease_pool_stats(
                db, "openvpn", cidr_config.get("openvpn", ""), gateway_config.get("openvpn", "")
            ),
            "openvpn_legacy": self.lease_pool_stats(
                db, "openvpn-legacy", cidr_config.get("openvpn_legacy", ""), gateway_config.get("openvpn_legacy", "")
            ),
            "sfos": self.lease_pool_stats(
                db, "sfos", cidr_config.get("sfos", ""), gateway_config.get("sfos", "")
            ),
        }
