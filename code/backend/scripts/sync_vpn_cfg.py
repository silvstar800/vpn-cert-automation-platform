#!/usr/bin/env python3
import ipaddress
import os
import re
import subprocess
from pathlib import Path

ENV_PATH = "/opt/certsvc/.env"


def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def q(sql: str) -> list[str]:
    cmd = [
        "sudo",
        "-u",
        "postgres",
        "psql",
        "-d",
        "certsvc",
        "-At",
        "-c",
        sql,
    ]
    out = subprocess.check_output(cmd, text=True)
    return [line.strip() for line in out.splitlines() if line.strip()]


def strip_mask(ip: str) -> str:
    return ip.split("/", 1)[0].strip()


def ensure_remote(remote: str) -> str:
    r = remote.strip()
    return r if "/" in r else f"{r}/32"


def write_ccd(ccd_dir: str, rows: list[tuple[str, str]], netmask: str, remote_net: str) -> None:
    p = Path(ccd_dir)
    p.mkdir(parents=True, exist_ok=True)
    desired = set()
    remote_value = ensure_remote(remote_net)

    for identity, ip in rows:
        ip_only = strip_mask(ip)
        desired.add(identity)
        content = (
            f"ifconfig-push {ip_only} {netmask}\n"
            f"push 'setenv-safe remote_network_1 {remote_value}'\n"
            f"push 'setenv-safe local_network_1 {ip_only}/32'\n"
            f"iroute {ip_only} 255.255.255.255\n"
        )
        (p / identity).write_text(content, encoding="utf-8")

    for f in p.iterdir():
        if f.is_file() and f.name not in desired:
            f.unlink()


def sync_routes(conf_path: str, ips: list[str], *, static_route_cidr: str | None = None) -> None:
    p = Path(conf_path)
    if not p.exists():
        return

    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()

    managed_prefixes = (
        "10.8.",
        "10.10.",
        "172.23.210.",
        "172.23.212.",
        "172.23.220.",
    )

    out: list[str] = []
    for line in lines:
        s = line.strip()
        m = re.match(r"^route\s+(\S+)\s+(\S+)$", s)
        if m:
            rip = m.group(1)
            if rip.startswith(managed_prefixes):
                continue
        out.append(line)

    if static_route_cidr:
        network = ipaddress.ip_network(static_route_cidr, strict=False)
        desired_routes = [f"route {network.network_address} {network.netmask}"]
    else:
        desired_routes = [f"route {strip_mask(ip)} 255.255.255.255" for ip in ips]
    if out and out[-1].strip() != "":
        out.append("")
    out.extend(desired_routes)

    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    env = load_env(ENV_PATH)

    ccd = env.get("CCD_DIR", "/etc/openvpn/ccd")
    ccd_legacy = env.get("CCD_LEGACY_DIR", "/etc/openvpn/ccd-legacy")
    ccd_sfos = env.get("CCD_SFOS_DIR", "/etc/openvpn/ccd-sfos")
    openvpn_nm = env.get("OPENVPN_NETMASK", "255.255.252.0")
    openvpn_legacy_nm = env.get("OPENVPN_LEGACY_TUN_SERIAL_IP", "10.242.253.1")
    openvpn_legacy_cidr = env.get("OPENVPN_LEGACY_CIDR", "172.23.212.0/22")
    sfos_nm = env.get("SFOS_NETMASK", "255.255.252.0")
    remote_net = env.get("OPENVPN_PUSH_REMOTE_NETWORK_1", "10.0.200.4")

    openvpn_conf = env.get("OPENVPN_SERVER_CONF", "/etc/openvpn/server/server.conf")
    openvpn_legacy_conf = env.get("OPENVPN_LEGACY_SERVER_CONF", "/etc/openvpn/server/server-legacy.conf")
    sfos_conf = env.get("SFOS_SERVER_CONF", "/etc/openvpn/server/server-sfos.conf")

    openvpn_rows = []
    for r in q("""
        select c.hostname, l.assigned_ip
        from ip_leases l
        join clients c on c.id = l.client_id
        where c.vpn_type='openvpn'
          and coalesce(c.is_legacy,false)=false
        order by l.created_at, l.id
    """):
        ident, ip = r.split("|", 1)
        openvpn_rows.append((ident, ip))

    openvpn_legacy_rows = []
    for r in q("""
        select c.hostname, l.assigned_ip
        from ip_leases l
        join clients c on c.id = l.client_id
        where c.vpn_type='openvpn'
          and coalesce(c.is_legacy,false)=true
        order by l.created_at, l.id
    """):
        ident, ip = r.split("|", 1)
        openvpn_legacy_rows.append((ident, ip))

    sfos_rows = []
    for r in q("""
        select c.hostname, l.assigned_ip
        from ip_leases l
        join clients c on c.id = l.client_id
        where c.vpn_type='sfos'
        order by l.created_at, l.id
    """):
        ident, ip = r.split("|", 1)
        sfos_rows.append((ident, ip))

    write_ccd(ccd, openvpn_rows, openvpn_nm, remote_net)
    write_ccd(ccd_legacy, openvpn_legacy_rows, openvpn_legacy_nm, remote_net)
    write_ccd(ccd_sfos, sfos_rows, sfos_nm, remote_net)

    sync_routes(openvpn_conf, [ip for _, ip in openvpn_rows])
    sync_routes(openvpn_legacy_conf, [ip for _, ip in openvpn_legacy_rows], static_route_cidr=openvpn_legacy_cidr)
    sync_routes(sfos_conf, [ip for _, ip in sfos_rows])

    print(f"synced ccd openvpn={len(openvpn_rows)} openvpn_legacy={len(openvpn_legacy_rows)} sfos={len(sfos_rows)}")
    print(f"openvpn_conf={openvpn_conf}")
    print(f"openvpn_legacy_conf={openvpn_legacy_conf}")
    print(f"sfos_conf={sfos_conf}")


if __name__ == "__main__":
    main()
