import StatusBadge from "../components/StatusBadge";
import { vpnLabel } from "../utils/vpnLabel";
import { formatDateSeoul } from "../utils/time";

function expireStatusMeta(daysLeft) {
  if (daysLeft < 0) return { tone: "critical", label: "만료" };
  if (daysLeft <= 30) return { tone: "warning", label: "만료 예정" };
  return { tone: "safe", label: "여유" };
}

function toRows(clients) {
  const today = new Date();
  return clients
    .filter((client) => client.expireAt)
    .map((client) => {
      const diffMs = new Date(client.expireAt).getTime() - today.getTime();
      const daysLeft = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
      return {
        hostname: client.hostname,
        vpnType: client.vpnType,
        assignedIp: client.assignedIp,
        expireAt: client.expireAt,
        daysLeft,
        status: expireStatusMeta(daysLeft),
      };
    })
    .sort((a, b) => a.daysLeft - b.daysLeft);
}

export default function ExpirePage({ clients = [] }) {
  const rows = toRows(clients);
  const expired = rows.filter((row) => row.daysLeft < 0).length;
  const within7 = rows.filter((row) => row.daysLeft <= 7).length;
  const within30 = rows.filter((row) => row.daysLeft <= 30).length;

  return (
    <div className="page-grid">
      <div className="stats-grid compact-stats">
        <div className="stat-card">
          <div className="stat-label">이미 만료</div>
          <div className="stat-value">{expired}</div>
          <div className="stat-sub">즉시 조치 필요</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">7일 이내</div>
          <div className="stat-value">{within7}</div>
          <div className="stat-sub">긴급 갱신 대상</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">30일 이내</div>
          <div className="stat-value">{within30}</div>
          <div className="stat-sub">사전 점검 대상</div>
        </div>
      </div>

      <div className="panel">
        <h3 className="panel-title">만료 목록</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>호스트명</th>
              <th>VPN 유형</th>
              <th>만료일</th>
              <th>남은 일수</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.hostname}-${row.expireAt}`}>
                <td>{row.hostname}</td>
                <td>{vpnLabel(row.vpnType, row.assignedIp)}</td>
                <td>{formatDateSeoul(row.expireAt)}</td>
                <td>{row.daysLeft}</td>
                <td><StatusBadge tone={row.status.tone} label={row.status.label} /></td>
              </tr>
            ))}
            {!rows.length ? <tr className="placeholder-row"><td colSpan={5}>표시할 인증서가 없습니다.</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
