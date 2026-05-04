function normalizeIp(ip) {
  return String(ip || "").trim();
}

export function deriveVpnType(vpnType, assignedIp) {
  const ip = normalizeIp(assignedIp);

  if (ip.startsWith("172.23.212.")) return "openvpn-legacy";
  if (ip.startsWith("172.23.208.")) return "openvpn";

  if (vpnType === "openvpn-legacy") return "openvpn-legacy";
  if (vpnType === "openvpn") return "openvpn";
  if (vpnType === "sfos") return "sfos";

  return vpnType || "";
}

export function vpnLabel(vpnType, assignedIp) {
  const resolved = deriveVpnType(vpnType, assignedIp);
  if (resolved === "openvpn") return "SG";
  if (resolved === "openvpn-legacy") return "레거시 SG";
  if (resolved === "sfos") return "XGS";
  return resolved || "-";
}
