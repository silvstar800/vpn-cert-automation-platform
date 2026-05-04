"""VPN configuration management."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class VPNConfigManager:
    """Manages OpenVPN and SFOS VPN configuration artifacts."""

    def __init__(
        self,
        *,
        ccd_dir: str | None = None,
        ccd_legacy_dir: str | None = None,
        ccd_sfos_dir: str | None = None,
        openvpn_conf_path: str | None = None,
        pki_dir: str | None = None,
        templates_dir: str | None = None,
        openvpn_server_ip: str = "127.0.0.1",
        openvpn_port: int = 1194,
    ) -> None:
        self.ccd_dir = Path(ccd_dir) if ccd_dir else None
        self.ccd_legacy_dir = Path(ccd_legacy_dir) if ccd_legacy_dir else None
        self.ccd_sfos_dir = Path(ccd_sfos_dir) if ccd_sfos_dir else None
        self.openvpn_conf = Path(openvpn_conf_path) if openvpn_conf_path else None
        self.pki_dir = Path(pki_dir) if pki_dir else None
        self.templates_dir = Path(templates_dir) if templates_dir else None
        self.openvpn_server_ip = openvpn_server_ip
        self.openvpn_port = int(openvpn_port)

        for path in [self.ccd_dir, self.ccd_legacy_dir, self.ccd_sfos_dir]:
            if path:
                path.mkdir(parents=True, exist_ok=True)

    def write_ccd_entry(
        self,
        *,
        hostname: str,
        assigned_ip: str,
        gateway_ip: str,
        ccd_dir: str | None = None,
        push_remote_network: str | None = None,
    ) -> bool:
        """Write one CCD entry for a client."""
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
            lines.extend(
                [
                    f"push 'setenv-safe local_network_1 {assigned_ip}/32'",
                    f"iroute {assigned_ip} 255.255.255.255",
                ]
            )
            ccd_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

    def delete_ccd_entry(self, *, hostname: str, ccd_dir: str | None = None) -> bool:
        """Delete one CCD entry when it exists."""
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

    def list_ccd_entries(self, ccd_dir: str | None = None) -> list[str]:
        """List available CCD entries."""
        target_dir = Path(ccd_dir) if ccd_dir else self.ccd_dir
        if not target_dir or not target_dir.exists():
            return []
        return [item.name for item in target_dir.glob("*") if item.is_file()]

    def get_ccd_entry_content(self, *, hostname: str, ccd_dir: str | None = None) -> str | None:
        """Read one CCD entry content."""
        target_dir = Path(ccd_dir) if ccd_dir else self.ccd_dir
        if not target_dir:
            return None
        ccd_file = target_dir / hostname
        if not ccd_file.exists():
            return None
        return ccd_file.read_text(encoding="utf-8")

    def read_openvpn_status(self, *, status_file: str) -> dict[str, dict[str, str]]:
        """Parse OpenVPN status file into a connected client mapping."""
        clients: dict[str, dict[str, str]] = {}
        try:
            with open(status_file, "r", encoding="utf-8", errors="ignore") as handle:
                in_client_section = False
                for raw_line in handle:
                    line = raw_line.strip()
                    if line.startswith("Updated,"):
                        continue
                    if line.startswith("Common Name"):
                        in_client_section = True
                        continue
                    if in_client_section and line.startswith("ROUTING TABLE"):
                        in_client_section = False
                        continue
                    if not in_client_section or not line:
                        continue

                    parts = line.split(",")
                    if len(parts) < 5:
                        continue

                    cn = parts[0].strip()
                    clients[cn] = {
                        "real_address": parts[1].strip(),
                        "virtual_address": parts[2].strip(),
                        "bytes_received": parts[3].strip(),
                        "bytes_sent": parts[4].strip(),
                    }
        except OSError:
            return {}
        return clients

    def read_connected_common_names(self, *, status_file: str) -> set[str]:
        """Return the connected client CN set from an OpenVPN status file."""
        connected: set[str] = set()
        path = Path(status_file)
        if not path.exists():
            return connected
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for raw_line in handle:
                    if not raw_line.startswith("CLIENT_LIST,"):
                        continue
                    parts = raw_line.strip().split(",")
                    if len(parts) >= 2 and parts[1]:
                        connected.add(parts[1].strip())
        except OSError:
            return set()
        return connected

    def resolve_status_file(self, *, config_path: str, fallback: str, override_path: str = "") -> str:
        """Resolve the status file declared in an OpenVPN config file."""
        if override_path:
            return override_path

        path = Path(config_path)
        if not path.exists():
            return fallback

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
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

    def ensure_server_conf_route(self, *, target_ip: str) -> bool:
        """Ensure one per-host route exists in the configured server conf."""
        if not self.openvpn_conf or not self.openvpn_conf.exists():
            return False
        try:
            route_line = f"route {target_ip} 255.255.255.255"
            current = self.openvpn_conf.read_text(encoding="utf-8", errors="ignore").splitlines()
            if any(line.strip() == route_line for line in current):
                return True
            if current and current[-1].strip():
                current.append("")
            current.append(route_line)
            self.openvpn_conf.write_text("\n".join(current) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

    def write_openvpn_bundle(
        self,
        *,
        hostname: str,
        ca_cert: str,
        certificate: str,
        key: str,
        vpn_port: int | None = None,
    ) -> tuple[str, Path]:
        """Build a deployable OpenVPN bundle and return archive + temp root path."""
        if not self.pki_dir:
            raise RuntimeError("PKI directory is not configured")

        client_conf = (
            "client\n"
            "dev tun-utm9\n"
            "proto udp\n"
            f"remote {self.openvpn_server_ip} {int(vpn_port or self.openvpn_port)}\n\n"
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
        shutil.copy(str(self.pki_dir / "ta.key"), str(cert_dir / "ta.key"))

        if self.templates_dir and self.templates_dir.is_dir():
            for src in self.templates_dir.iterdir():
                if src.name == "vpn_enroll.sh":
                    continue
                dest = base_dir / src.name
                if src.is_file():
                    shutil.copy2(src, dest)
                elif src.is_dir():
                    shutil.copytree(src, dest, dirs_exist_ok=True)

        archive_path = shutil.make_archive(str(root_dir / hostname), "gztar", str(root_dir), hostname)
        return archive_path, root_dir
