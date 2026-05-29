#!/usr/bin/env python3
import ipaddress
import subprocess
from pathlib import Path

ENV_PATH = "/opt/certsvc/.env"


def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def q(sql: str) -> list[str]:
    cmd = ["sudo", "-u", "postgres", "psql", "-d", "certsvc", "-At", "-c", sql]
    out = subprocess.check_output(cmd, text=True)
    return [line.strip() for line in out.splitlines() if line.strip()]


def strip_mask(ip: str) -> str:
    return ip.split("/", 1)[0].strip()


def ensure_remote(remote: str) -> str:
    r = remote.strip()
    return r if "/" in r else f"{r}/32"


def host_routes_from_cidr(cidr: str) -> list[str]:
    network = ipaddress.ip_network(cidr, strict=False)
    return [str(host) for host in network.hosts()]


def write_ccd_dir(ccd_dir: str, rows: list[tuple[str, str]], gw_ip: str, remote_net: str) -> None:
    p = Path(ccd_dir)
    p.mkdir(parents=True, exist_ok=True)
    desired = set()
    rnet = ensure_remote(remote_net)

    for identity, ip in rows:
        ip_only = strip_mask(ip)
        desired.add(identity)
        body = (
            "push-reset\n"
            "push 'topology net30'\n"
            f"ifconfig-push {ip_only} {gw_ip}\n"
            f"push 'route-gateway {gw_ip}'\n"
            f"push 'setenv-safe remote_network_1 {rnet}'\n"
            f"push 'setenv-safe local_network_1 {ip_only}/32'\n"
            f"iroute {ip_only} 255.255.255.255\n"
        )
        (p / identity).write_text(body, encoding="utf-8")

    for f in p.iterdir():
        if f.is_file() and f.name not in desired:
            f.unlink()


def write_server_conf(path: str, *, port: int, proto: str, dev: str, server_net: str, netmask: str,
                      gw_ip: str, ca: str, cert: str, key: str, dh: str, ta: str,
                      cipher: str, auth: str, status: str, log_append: str, ccd_dir: str,
                      data_ciphers: str = "AES-256-CBC", data_ciphers_fallback: str = "AES-256-CBC",
                      extra_lines: list[str], routes: list[str]) -> None:
    lines: list[str] = []
    lines.extend([
        f"port {port}",
        f"proto {proto}",
        f"dev {dev}",
        "",
        f"server {server_net} {netmask}",
        "topology net30",
        "",
        "ccd-exclusive",
        "duplicate-cn",
        "",
        f"route-gateway {gw_ip}",
        f"push 'route-gateway {gw_ip}'",
        "",
    ])
    for ip in routes:
        lines.append(f"route {strip_mask(ip)} 255.255.255.255")
    lines.extend([
        "",
        f"ca {ca}",
        f"cert {cert}",
        f"key {key}",
        f"dh {dh}",
        "",
    ])
    if ta:
        lines.append(f"tls-auth {ta} 0")
        lines.append("")
    lines.extend([
        f"cipher {cipher}",
        f"auth {auth}",
        f"data-ciphers {data_ciphers}",
        f"data-ciphers-fallback {data_ciphers_fallback}",
        "",
        f"client-config-dir {ccd_dir}",
        "",
        "keepalive 10 120",
        "persist-key",
        "persist-tun",
    ])
    if extra_lines:
        lines.append("")
        lines.extend(extra_lines)
    lines.extend([
        "",
        f"status {status}",
        f"log-append {log_append}",
        "",
        "verb 6",
        "",
    ])

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    env = load_env(ENV_PATH)

    pki = env.get("PKI_DIR", "/etc/openvpn/pki")
    ccd = env.get("CCD_DIR", "/etc/openvpn/ccd")
    ccd_legacy = env.get("CCD_LEGACY_DIR", "/etc/openvpn/ccd-legacy")
    ccd_sfos = env.get("CCD_SFOS_DIR", "/etc/openvpn/ccd-sfos")

    openvpn_conf = env.get("OPENVPN_SERVER_CONF", "/etc/openvpn/server/server.conf")
    openvpn_legacy_conf = env.get("OPENVPN_LEGACY_SERVER_CONF", "/etc/openvpn/server/server-legacy.conf")
    sfos_conf = env.get("SFOS_SERVER_CONF", "/etc/openvpn/server/server-sfos.conf")

    remote_net = env.get("OPENVPN_PUSH_REMOTE_NETWORK_1", "10.0.200.4")

    sg_gw = env.get("OPENVPN_TUN_SERIAL_IP", "10.242.254.1")
    sg_cidr = env.get("OPENVPN_CIDR", "172.23.208.0/22")
    sg_legacy_gw = env.get("OPENVPN_LEGACY_TUN_SERIAL_IP", "10.242.253.1")
    sg_legacy_cidr = env.get("OPENVPN_LEGACY_CIDR", "172.23.212.0/22")
    sg_legacy_network = ipaddress.ip_network(sg_legacy_cidr, strict=False)
    xgs_gw = env.get("SFOS_TUN_SERIAL_IP", "10.242.255.1")
    sfos_cidr = env.get("SFOS_CIDR", "172.23.220.0/22")

    sg_rows = []
    for row in q("""
        select c.hostname, l.assigned_ip
        from clients c
        join ip_leases l on l.client_id = c.id
        where c.vpn_type='openvpn'
          and coalesce(c.is_legacy,false)=false
        order by l.assigned_ip, c.id
    """):
        ident, ip = row.split("|", 1)
        sg_rows.append((ident, ip))

    sg_legacy_rows = []
    for row in q("""
        select c.hostname, l.assigned_ip
        from clients c
        join ip_leases l on l.client_id = c.id
        where c.vpn_type='openvpn'
          and coalesce(c.is_legacy,false)=true
        order by l.assigned_ip, c.id
    """):
        ident, ip = row.split("|", 1)
        sg_legacy_rows.append((ident, ip))

    xgs_rows = []
    for row in q("""
        select c.hostname, l.assigned_ip
        from clients c
        join ip_leases l on l.client_id = c.id
        where c.vpn_type='sfos'
        order by l.assigned_ip, c.id
    """):
        ident, ip = row.split("|", 1)
        xgs_rows.append((ident, ip))

    write_ccd_dir(ccd, sg_rows, sg_gw, remote_net)
    write_ccd_dir(ccd_legacy, sg_legacy_rows, sg_legacy_gw, remote_net)
    write_ccd_dir(ccd_sfos, xgs_rows, xgs_gw, remote_net)

    ca = f"{pki}/pki/ca.crt"
    cert = f"{pki}/pki/issued/server.crt"
    key = f"{pki}/pki/private/server.key"
    dh = f"{pki}/pki/dh.pem"
    ta = f"{pki}/ta.key"

    write_server_conf(
        openvpn_conf,
        port=1194,
        proto="udp",
        dev="tun-utm9",
        server_net="10.242.254.0",
        netmask="255.255.255.0",
        gw_ip=sg_gw,
        ca=ca,
        cert=cert,
        key=key,
        dh=dh,
        ta=ta,
        cipher="AES-256-CBC",
        auth="SHA256",
        status="/var/log/openvpn/status.log",
        log_append="/var/log/openvpn/openvpn.log",
        ccd_dir=ccd,
        data_ciphers="AES-256-CBC",
        data_ciphers_fallback="AES-256-CBC",
        extra_lines=[],
        routes=host_routes_from_cidr(sg_cidr),
    )

    write_server_conf(
        openvpn_legacy_conf,
        port=1195,
        proto="udp",
        dev="tun-legacy",
        server_net="10.242.253.0",
        netmask="255.255.255.0",
        gw_ip=sg_legacy_gw,
        ca=ca,
        cert=cert,
        key=key,
        dh=dh,
        ta=ta,
        cipher="AES-256-CBC",
        auth="SHA256",
        status="/var/log/openvpn/status-legacy.log",
        log_append="/var/log/openvpn/openvpn-legacy.log",
        ccd_dir=ccd_legacy,
        data_ciphers="AES-256-CBC",
        data_ciphers_fallback="AES-256-CBC",
        extra_lines=[
            f"route {sg_legacy_network.network_address} {sg_legacy_network.netmask}",
            "compat-mode 2.3.0",
            "tls-cert-profile legacy",
            "tls-cipher DEFAULT:@SECLEVEL=0",
        ],
        routes=host_routes_from_cidr(sg_legacy_cidr),
    )

    write_server_conf(
        sfos_conf,
        port=4443,
        proto="tcp",
        dev="tun-sfos",
        server_net="10.242.255.0",
        netmask="255.255.255.0",
        gw_ip=xgs_gw,
        ca=ca,
        cert=cert,
        key=key,
        dh=dh,
        ta="",
        cipher="AES-256-GCM",
        auth="SHA256",
        status="/run/openvpn-server/status-server-sfos.log",
        log_append="/var/log/openvpn/openvpn-sfos.log",
        ccd_dir=ccd_sfos,
        data_ciphers="AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305:AES-256-CBC",
        data_ciphers_fallback="AES-256-CBC",
        extra_lines=[],
        routes=host_routes_from_cidr(sfos_cidr),
    )

    print(f"synced SG={len(sg_rows)} SG_LEGACY={len(sg_legacy_rows)} XGS={len(xgs_rows)}")
    print(f"openvpn_conf={openvpn_conf}")
    print(f"openvpn_legacy_conf={openvpn_legacy_conf}")
    print(f"sfos_conf={sfos_conf}")


if __name__ == "__main__":
    main()


