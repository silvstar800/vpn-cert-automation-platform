"""VPN configuration management."""

import ipaddress
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class VPNConfigManager:
    """Manages OpenVPN and SFOS VPN configurations."""

    def __init__(self, ccd_dir: str = None, ccd_legacy_dir: str = None, 
                 ccd_sfos_dir: str = None, openvpn_conf_path: str = None):
        """Initialize with config directories.
        
        Args:
            ccd_dir: Client Config Directory for OpenVPN
            ccd_legacy_dir: CCD for OpenVPN Legacy
            ccd_sfos_dir: CCD for SFOS
            openvpn_conf_path: Path to OpenVPN server config
        """
        self.ccd_dir = Path(ccd_dir) if ccd_dir else None
        self.ccd_legacy_dir = Path(ccd_legacy_dir) if ccd_legacy_dir else None
        self.ccd_sfos_dir = Path(ccd_sfos_dir) if ccd_sfos_dir else None
        self.openvpn_conf = Path(openvpn_conf_path) if openvpn_conf_path else None
        
        # Create directories if they don't exist
        for d in [self.ccd_dir, self.ccd_legacy_dir, self.ccd_sfos_dir]:
            if d:
                d.mkdir(parents=True, exist_ok=True)

    def write_ccd_entry(self, hostname: str, assigned_ip: str, 
                       gateway_ip: str, ccd_dir: str = None,
                       push_remote_network: str = None) -> bool:
        """Write client config directory (CCD) entry.
        
        Args:
            hostname: Client hostname
            assigned_ip: Assigned VPN IP
            gateway_ip: Gateway IP for tunnel
            ccd_dir: Custom CCD directory (overrides instance)
            push_remote_network: Optional remote network to push
            
        Returns:
            True if successful, False otherwise
        """
        try:
            target_dir = Path(ccd_dir) if ccd_dir else self.ccd_dir
            if not target_dir:
                return False
            
            target_dir.mkdir(parents=True, exist_ok=True)
            ccd_file = target_dir / hostname
            
            lines = [
                "push-reset",
                "push 'topology net30'",
                f"ifconfig-push {assigned_ip} {gateway_ip}",
                f"push 'route-gateway {gateway_ip}'",
            ]
            
            if push_remote_network:
                lines.append(f"push 'setenv-safe remote_network_1 {push_remote_network}/32'")
            
            lines.extend([
                f"push 'setenv-safe local_network_1 {assigned_ip}/32'",
                f"iroute {assigned_ip} 255.255.255.255",
            ])
            
            ccd_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

    def delete_ccd_entry(self, hostname: str, ccd_dir: str = None) -> bool:
        """Delete CCD entry for hostname.
        
        Args:
            hostname: Client hostname
            ccd_dir: Custom CCD directory (overrides instance)
            
        Returns:
            True if successful or file didn't exist, False on error
        """
        try:
            target_dir = Path(ccd_dir) if ccd_dir else self.ccd_dir
            if not target_dir:
                return False
            
            ccd_file = target_dir / hostname
            if ccd_file.exists():
                ccd_file.unlink()
            return True
        except Exception:
            return False

    def list_ccd_entries(self, ccd_dir: str = None) -> list[str]:
        """List all CCD entries (client hostnames).
        
        Args:
            ccd_dir: Custom CCD directory (overrides instance)
            
        Returns:
            List of client hostnames
        """
        target_dir = Path(ccd_dir) if ccd_dir else self.ccd_dir
        if not target_dir or not target_dir.exists():
            return []
        
        return [f.name for f in target_dir.glob("*") if f.is_file()]

    def get_ccd_entry_content(self, hostname: str, ccd_dir: str = None) -> Optional[str]:
        """Read CCD entry content.
        
        Args:
            hostname: Client hostname
            ccd_dir: Custom CCD directory (overrides instance)
            
        Returns:
            CCD entry content or None if not found
        """
        target_dir = Path(ccd_dir) if ccd_dir else self.ccd_dir
        if not target_dir:
            return None
        
        ccd_file = target_dir / hostname
        if ccd_file.exists():
            return ccd_file.read_text(encoding="utf-8")
        return None

    def read_openvpn_status(self, status_file: str) -> dict:
        """Parse OpenVPN status file for connected clients.
        
        Args:
            status_file: Path to OpenVPN status file
            
        Returns:
            Dict of connected clients with their info
        """
        clients = {}
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                in_client_section = False
                for line in f:
                    line = line.strip()
                    if line.startswith("Updated,"):
                        continue
                    if line.startswith("Common Name"):
                        in_client_section = True
                        continue
                    if in_client_section and line.startswith("ROUTING TABLE"):
                        in_client_section = False
                        continue
                    
                    if in_client_section and line:
                        parts = line.split(",")
                        if len(parts) >= 5:
                            cn = parts[0].strip()
                            real_addr = parts[1].strip()
                            virtual_addr = parts[2].strip()
                            bytes_recv = parts[3].strip()
                            bytes_sent = parts[4].strip()
                            
                            clients[cn] = {
                                "real_address": real_addr,
                                "virtual_address": virtual_addr,
                                "bytes_received": bytes_recv,
                                "bytes_sent": bytes_sent,
                            }
        except OSError:
            pass
        
        return clients

    def ensure_server_conf_route(self, hostname: str, target_ip: str) -> bool:
        """Ensure per-host route exists in server conf.
        
        Args:
            hostname: Client identifier
            target_ip: Target IP for route
            
        Returns:
            True if successful or already exists
        """
        if not self.openvpn_conf or not self.openvpn_conf.exists():
            return False
        
        try:
            route_line = f"route {target_ip} 255.255.255.255"
            current = self.openvpn_conf.read_text(encoding="utf-8").splitlines()
            
            if any(line.strip() == route_line for line in current):
                return True
            
            if current and current[-1].strip():
                current.append("")
            current.append(route_line)
            self.openvpn_conf.write_text("\n".join(current) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

    @staticmethod
    def resolve_openvpn_runtime(
        is_legacy: bool,
        *,
        ccd_dir: str,
        ccd_legacy_dir: str,
        openvpn_tun_serial_ip: str,
        openvpn_legacy_tun_serial_ip: str,
        openvpn_server_conf: str,
        openvpn_legacy_server_conf: str,
        openvpn_port: int,
        openvpn_legacy_port: int,
        openvpn_status_file: str,
        openvpn_legacy_status_file: str,
    ) -> dict[str, object]:
        """Return per-runtime OpenVPN paths and gateway settings."""
        if is_legacy:
            return {
                "ccd_dir": ccd_legacy_dir,
                "route_gateway_ip": openvpn_legacy_tun_serial_ip,
                "server_conf_path": openvpn_legacy_server_conf,
                "vpn_port": openvpn_legacy_port,
                "status_file": openvpn_legacy_status_file,
            }
        return {
            "ccd_dir": ccd_dir,
            "route_gateway_ip": openvpn_tun_serial_ip,
            "server_conf_path": openvpn_server_conf,
            "vpn_port": openvpn_port,
            "status_file": openvpn_status_file,
        }

    @staticmethod
    def ensure_server_conf_route_path(server_conf_path: str, target_ip: str) -> None:
        """Ensure per-host /32 route exists in server conf."""
        try:
            normalized_ip = str(ipaddress.ip_address(str(target_ip).strip()))
        except ValueError as exc:
            raise RuntimeError(f"invalid route target ip: {target_ip}") from exc

        route_line = f"route {normalized_ip} 255.255.255.255"
        conf = Path(server_conf_path)
        if not conf.exists():
            raise RuntimeError(f"server conf not found: {server_conf_path}")

        try:
            current = conf.read_text(encoding="utf-8", errors="ignore").splitlines()
            if any(line.strip() == route_line for line in current):
                return
            if current and current[-1].strip():
                current.append("")
            current.append(route_line)
            conf.write_text("\n".join(current) + "\n", encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"failed to update route in {server_conf_path}: {exc}") from exc

    @staticmethod
    def ensure_server_conf_host_routes_path(server_conf_path: str, cidr: str) -> None:
        """Ensure every host in CIDR has route entry in server conf."""
        try:
            network = ipaddress.ip_network(str(cidr).strip(), strict=False)
        except ValueError as exc:
            raise RuntimeError(f"invalid host route cidr: {cidr}") from exc

        desired_routes = {f"route {host} 255.255.255.255" for host in network.hosts()}
        conf = Path(server_conf_path)
        if not conf.exists():
            raise RuntimeError(f"server conf not found: {server_conf_path}")

        try:
            current = conf.read_text(encoding="utf-8", errors="ignore").splitlines()
            existing = {line.strip() for line in current}
            missing = [
                line
                for line in sorted(
                    desired_routes,
                    key=lambda line: tuple(int(part) for part in line.split()[1].split(".")),
                )
                if line not in existing
            ]
            if not missing:
                return
            insert_at = 0
            for idx, line in enumerate(current):
                if line.strip().startswith("ca "):
                    insert_at = idx
                    break
            updated = current[:insert_at] + missing + ([""] if insert_at > 0 and (not current[:insert_at] or current[insert_at - 1].strip()) else []) + current[insert_at:]
            conf.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"failed to update host routes in {server_conf_path}: {exc}") from exc

    @staticmethod
    def ensure_server_conf_network_route_path(server_conf_path: str, cidr: str) -> None:
        """Ensure a CIDR route exists in server conf."""
        try:
            network = ipaddress.ip_network(str(cidr).strip(), strict=False)
        except ValueError as exc:
            raise RuntimeError(f"invalid route cidr: {cidr}") from exc

        route_line = f"route {network.network_address} {network.netmask}"
        conf = Path(server_conf_path)
        if not conf.exists():
            raise RuntimeError(f"server conf not found: {server_conf_path}")

        try:
            current = conf.read_text(encoding="utf-8", errors="ignore").splitlines()
            if any(line.strip() == route_line for line in current):
                return
            if current and current[-1].strip():
                current.append("")
            current.append(route_line)
            conf.write_text("\n".join(current) + "\n", encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"failed to update route in {server_conf_path}: {exc}") from exc

    def write_ccd_entry_and_route(
        self,
        *,
        ccd_dir: str,
        hostname: str,
        assigned_ip: str,
        route_gateway_ip: str,
        push_remote_network: str = "",
    ) -> None:
        """Write CCD entry for client runtime."""
        self.write_ccd_entry(
            hostname=hostname,
            assigned_ip=assigned_ip,
            gateway_ip=route_gateway_ip,
            ccd_dir=ccd_dir,
            push_remote_network=push_remote_network or None,
        )

    @staticmethod
    def write_openvpn_bundle(
        hostname: str,
        ca_cert: str,
        certificate: str,
        key: str,
        *,
        server_ip: str,
        default_port: int,
        vpn_port: int | None = None,
        pki_dir: str,
        templates_dir: str,
        api_port: int | None = None,
    ) -> tuple[str, Path]:
        """Build and archive deployable OpenVPN client bundle."""
        client_conf = (
            "client\n"
            "dev tun-utm9\n"
            "proto udp\n"
            f"remote {server_ip} {int(vpn_port or default_port)}\n\n"
            "persist-key\n"
            "persist-tun\n\n"
            "cipher AES-256-CBC\n"
            "auth SHA256\n\n"
            "remote-cert-tls server\n\n"
            "ca /etc/openvpn/certs/ca.crt\n"
            f"cert /etc/openvpn/certs/{hostname}.crt\n"
            f"key /etc/openvpn/certs/{hostname}.key\n"
            "tls-auth /etc/openvpn/ta.key 1\n\n"
            "key-direction 1\n"
            "verb 3\n"
        )

        root_dir = Path(tempfile.mkdtemp(prefix="certsvc-enroll-"))
        base_dir = root_dir / hostname
        cert_dir = base_dir / "certs"
        cert_dir.mkdir(parents=True, exist_ok=True)

        (base_dir / "client.conf").write_text(client_conf, encoding="utf-8")
        (cert_dir / "ca.crt").write_text(ca_cert, encoding="utf-8")
        (cert_dir / f"{hostname}.crt").write_text(certificate, encoding="utf-8")
        (cert_dir / f"{hostname}.key").write_text(key, encoding="utf-8")
        shutil.copy(str(Path(pki_dir) / "ta.key"), str(cert_dir / "ta.key"))

        template_root = Path(templates_dir)
        if template_root.is_dir():
            for src in template_root.iterdir():
                if src.name == "vpn_enroll.sh":
                    continue
                dest = base_dir / src.name
                if src.is_file():
                    # For shell scripts, render placeholders
                    if src.suffix == ".sh":
                        content = src.read_text(encoding="utf-8")
                        content = content.replace("__FW_GUARD__", "/usr/local/sbin/certsvc-ensure-fw.sh")
                        content = content.replace("__SERVER_IP__", server_ip)
                        content = content.replace("__VPN_PORT__", str(int(vpn_port or default_port)))
                        if api_port:
                            content = content.replace("__SERVER_API_PORT__", str(api_port))
                        content = content.replace("__IPT_FILE__", "/var/mdw/etc/iptables/iptable.filter")
                        dest.write_text(content, encoding="utf-8")
                        dest.chmod(0o755)
                    else:
                        shutil.copy2(src, dest)
                elif src.is_dir():
                    shutil.copytree(src, dest, dirs_exist_ok=True)

        archive_path = shutil.make_archive(str(root_dir / hostname), "gztar", str(root_dir), hostname)
        return archive_path, root_dir

    @staticmethod
    def read_openvpn_connected_cns(status_file: str) -> set[str]:
        """Parse connected CN set from OpenVPN status file."""
        connected: set[str] = set()
        path = Path(status_file)
        if not path.exists():
            return connected

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if not line.startswith("CLIENT_LIST,"):
                        continue
                    parts = line.strip().split(",")
                    if len(parts) >= 2 and parts[1]:
                        connected.add(parts[1].strip())
        except OSError:
            return set()

        return connected

    @staticmethod
    def resolve_openvpn_status_file(config_path: str, fallback: str, *, openvpn_server_conf: str, sfos_server_conf: str) -> str:
        """Resolve status file path from conf, or fallback if unavailable."""
        if os.environ.get("OPENVPN_STATUS_FILE") and config_path == openvpn_server_conf:
            return os.environ["OPENVPN_STATUS_FILE"]
        if os.environ.get("SFOS_STATUS_FILE") and config_path == sfos_server_conf:
            return os.environ["SFOS_STATUS_FILE"]

        path = Path(config_path)
        if not path.exists():
            return fallback

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for raw in handle:
                    line = raw.strip()
                    if not line or line.startswith("#") or line.startswith(";"):
                        continue
                    if not line.startswith("status "):
                        continue
                    parts = line.split(maxsplit=1)
                    if len(parts) == 2 and parts[1].strip():
                        return parts[1].strip()
        except OSError:
            return fallback

        return fallback

    @staticmethod
    def is_systemd_service_active(service_name: str) -> bool:
        """Check whether a systemd service is active."""
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "active"

    def collect_connection_sets(
        self,
        *,
        openvpn_server_conf: str,
        openvpn_status_file: str,
        openvpn_legacy_status_file: str,
        sfos_server_conf: str,
        sfos_status_file: str,
    ) -> dict[str, object]:
        """Resolve status files and return connected CN sets for all runtimes."""
        openvpn_status_path = self.resolve_openvpn_status_file(
            openvpn_server_conf,
            openvpn_status_file,
            openvpn_server_conf=openvpn_server_conf,
            sfos_server_conf=sfos_server_conf,
        )
        sfos_status_path = self.resolve_openvpn_status_file(
            sfos_server_conf,
            sfos_status_file,
            openvpn_server_conf=openvpn_server_conf,
            sfos_server_conf=sfos_server_conf,
        )
        openvpn_connected = self.read_openvpn_connected_cns(openvpn_status_path)
        openvpn_legacy_connected = self.read_openvpn_connected_cns(openvpn_legacy_status_file)
        sfos_connected = self.read_openvpn_connected_cns(sfos_status_path)
        return {
            "openvpnStatusPath": openvpn_status_path,
            "openvpnLegacyStatusPath": openvpn_legacy_status_file,
            "sfosStatusPath": sfos_status_path,
            "openvpnConnected": openvpn_connected,
            "openvpnLegacyConnected": openvpn_legacy_connected,
            "sfosConnected": sfos_connected,
        }

    def build_runtime_service_statuses(
        self,
        *,
        db_ok: bool,
        openvpn_connected_count: int,
        openvpn_legacy_connected_count: int,
        sfos_connected_count: int,
        openvpn_port: int,
        openvpn_legacy_port: int,
        sfos_vpn_port: int,
    ) -> list[dict]:
        """Build runtime service status rows for security monitoring."""
        openvpn_service_up = self.is_systemd_service_active("openvpn-server@server.service")
        openvpn_legacy_service_up = self.is_systemd_service_active("openvpn-server@server-legacy.service")
        sfos_service_up = self.is_systemd_service_active("openvpn-server@server-sfos.service")

        return [
            {
                "name": "openvpn-server@server",
                "status": "active" if openvpn_service_up and openvpn_connected_count > 0 else ("disconnected" if openvpn_service_up else "stopped"),
                "port": f"{openvpn_port}/udp",
                "nominal": "active",
            },
            {
                "name": "openvpn-server@server-legacy",
                "status": "active" if openvpn_legacy_service_up and openvpn_legacy_connected_count > 0 else ("disconnected" if openvpn_legacy_service_up else "stopped"),
                "port": f"{openvpn_legacy_port}/udp",
                "nominal": "active",
            },
            {
                "name": "openvpn-server@sfos",
                "status": "active" if sfos_service_up and sfos_connected_count > 0 else ("disconnected" if sfos_service_up else "stopped"),
                "port": f"{sfos_vpn_port}/tcp",
                "nominal": "active",
            },
            {
                "name": "certsvc-db",
                "status": "running" if db_ok else "stopped",
                "port": "5432/tcp",
                "nominal": "running",
            },
        ]

    @staticmethod
    def normalize_display_vpn_type(vpn_type: str, *, is_legacy_openvpn: bool) -> str:
        """Return display vpn type label used by list APIs."""
        if vpn_type == "openvpn" and is_legacy_openvpn:
            return "openvpn-legacy"
        return vpn_type

    @staticmethod
    def compute_client_connection_status(
        *,
        display_vpn_type: str,
        is_active: bool,
        identity: str,
        openvpn_connected: set[str],
        openvpn_legacy_connected: set[str],
        sfos_connected: set[str],
    ) -> str:
        """Compute unified client/lease connection status."""
        if not is_active:
            return "inactive"
        if display_vpn_type in {"openvpn", "openvpn-legacy"}:
            pool = openvpn_legacy_connected if display_vpn_type == "openvpn-legacy" else openvpn_connected
            return "active" if identity in pool else "disconnected"
        if display_vpn_type == "sfos":
            return "active" if identity in sfos_connected else "disconnected"
        return "inactive"

