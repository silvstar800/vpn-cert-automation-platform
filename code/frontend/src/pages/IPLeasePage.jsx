import { useMemo, useState } from "react";
import StatusBadge from "../components/StatusBadge";
import { deriveVpnType, vpnLabel } from "../utils/vpnLabel";
import { formatDateTimeSeoul } from "../utils/time";

export default function IPLeasePage({ leases = [] }) {
  const [activeTab, setActiveTab] = useState("openvpn");
  const rows = useMemo(
    () => leases.filter((row) => deriveVpnType(row.vpnType, row.assignedIp) === activeTab),
    [leases, activeTab],
  );

  return (
    <div className="page-grid">
      <div className="panel toolbar-panel">
        <div className="tab-group">
          <button className={`tab-btn ${activeTab === "openvpn" ? "active" : ""}`} onClick={() => setActiveTab("openvpn")}>SG</button>
          <button className={`tab-btn ${activeTab === "openvpn-legacy" ? "active" : ""}`} onClick={() => setActiveTab("openvpn-legacy")}>레거시 SG</button>
          <button className={`tab-btn ${activeTab === "sfos" ? "active" : ""}`} onClick={() => setActiveTab("sfos")}>XGS</button>
        </div>
      </div>

      <div className="two-col-grid">
        <div className="stat-card">
          <div className="stat-label">현재 필터</div>
          <div className="stat-value">{vpnLabel(activeTab)}</div>
          <div className="stat-sub">선택한 VPN 유형 기준</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">사용 중</div>
          <div className="stat-value">{rows.length}</div>
          <div className="stat-sub">현재 IP 임대 수</div>
        </div>
      </div>

      <div className="panel">
        <h3 className="panel-title">IP 임대 목록</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>식별값</th>
              <th>할당 IP</th>
              <th>활성</th>
              <th>업데이트 시점</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.vpnType}:${row.identity}`}>
                <td>{row.identity}</td>
                <td>{row.assignedIp}</td>
                <td><StatusBadge status={row.isActive ? "active" : "inactive"} /></td>
                <td>{formatDateTimeSeoul(row.updatedAt)}</td>
              </tr>
            ))}
            {!rows.length ? <tr className="placeholder-row"><td colSpan={4}>표시할 IP 임대 내역이 없습니다.</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
