import { useMemo, useState } from "react";
import StatusBadge from "../components/StatusBadge";
import { deriveVpnType, vpnLabel } from "../utils/vpnLabel";
import { formatDateTimeSeoul } from "../utils/time";

export default function ClientsPage({ clients = [], onSelectClient }) {
  const [search, setSearch] = useState("");
  const [vpnType, setVpnType] = useState("all");

  const filteredClients = useMemo(() => {
    return clients.filter((client) => {
      const text = [client.hostname, client.certCn, client.assignedIp, client.mac, client.tenant]
        .join(" ")
        .toLowerCase();
      const resolvedType = deriveVpnType(client.vpnType, client.assignedIp);
      const matchSearch = !search || text.includes(search.toLowerCase());
      const matchType = vpnType === "all" || resolvedType === vpnType;
      return matchSearch && matchType;
    });
  }, [clients, search, vpnType]);

  return (
    <div className="page-grid">
      <div className="panel toolbar-panel toolbar-panel-inline">
        <input
          className="input"
          placeholder="호스트명, CN, IP, MAC 검색"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />

        <select className="select" value={vpnType} onChange={(event) => setVpnType(event.target.value)}>
          <option value="all">전체</option>
          <option value="openvpn">SG</option>
          <option value="openvpn-legacy">레거시 SG</option>
          <option value="sfos">XGS</option>
        </select>
      </div>

      <div className="panel">
        <h3 className="panel-title">클라이언트 목록</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>호스트명</th>
              <th>VPN 유형</th>
              <th>할당 IP</th>
              <th>상태</th>
              <th>만료일</th>
              <th>등록 시점</th>
            </tr>
          </thead>
          <tbody>
            {filteredClients.map((client) => (
              <tr key={client.id} onClick={() => onSelectClient(client)}>
                <td>
                  <div className="row-title">{client.hostname}</div>
                  <div className="row-sub">{client.tenant || "-"}</div>
                </td>
                <td>{vpnLabel(client.vpnType, client.assignedIp)}</td>
                <td>{client.assignedIp || "-"}</td>
                <td><StatusBadge status={client.status} /></td>
                <td>{client.expireAt || "-"}</td>
                <td>{formatDateTimeSeoul(client.registeredAt || client.lastSeenAt)}</td>
              </tr>
            ))}
            {!filteredClients.length ? (
              <tr className="placeholder-row"><td colSpan={6}>조건에 맞는 클라이언트가 없습니다.</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
