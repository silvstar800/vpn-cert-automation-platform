export const mockClients = [
  {
    id: 1,
    hostname: "utm9-seoul-01",
    vpnType: "openvpn",
    certCn: "utm9-seoul-01",
    assignedIp: "10.8.0.10",
    status: "active",
    expireAt: "2026-06-28",
    lastSeenAt: "2026-03-23 09:12",
    mac: "00:11:22:33:44:55",
    tenant: "ATECH-HQ",
    issuer: "OpenVPN-CA",
    remoteIp: "211.174.186.245",
    username: "",
  },
  {
    id: 2,
    hostname: "sfos-busan-01",
    vpnType: "sfos",
    certCn: "sfos-busan-01",
    assignedIp: "10.10.0.21",
    status: "active",
    expireAt: "2026-04-07",
    lastSeenAt: "2026-03-23 08:55",
    mac: "AA:BB:CC:DD:EE:01",
    tenant: "ATECH-BUSAN",
    issuer: "OpenVPN-CA",
    remoteIp: "211.174.186.245",
    username: "SRV_FAKE_001",
  },
  {
    id: 3,
    hostname: "utm9-gwanggyo-01",
    vpnType: "openvpn",
    certCn: "utm9-gwanggyo-01",
    assignedIp: "10.8.0.18",
    status: "inactive",
    expireAt: "2026-03-29",
    lastSeenAt: "2026-03-21 17:42",
    mac: "10:20:30:40:50:60",
    tenant: "ATECH-GWANGGYO",
    issuer: "OpenVPN-CA",
    remoteIp: "211.174.186.245",
    username: "",
  },
];

export const expireRows = [
  { hostname: "utm9-gwanggyo-01", vpnType: "openvpn", expireAt: "2026-03-29", daysLeft: 6, status: "critical" },
  { hostname: "sfos-busan-01", vpnType: "sfos", expireAt: "2026-04-07", daysLeft: 15, status: "warning" },
  { hostname: "utm9-seoul-01", vpnType: "openvpn", expireAt: "2026-06-28", daysLeft: 97, status: "normal" },
];

export const ipLeaseData = {
  openvpn: [
    { identity: "utm9-seoul-01", assignedIp: "10.8.0.10", isActive: true, updatedAt: "2026-03-23 09:12" },
    { identity: "utm9-gwanggyo-01", assignedIp: "10.8.0.18", isActive: false, updatedAt: "2026-03-21 17:42" },
  ],
  sfos: [
    { identity: "sfos-busan-01", assignedIp: "10.10.0.21", isActive: true, updatedAt: "2026-03-23 08:55" },
  ],
};

export const systemServices = [
  { name: "openvpn-server@server", status: "running", port: "1194/udp" },
  { name: "openvpn-server@sfos", status: "running", port: "4443/tcp" },
  { name: "certsvc", status: "running", port: "8443/tcp" },
  { name: "postgresql", status: "running", port: "5432/tcp" },
];