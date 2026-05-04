import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import { vpnLabel } from "../utils/vpnLabel";
import { formatDateSeoul } from "../utils/time";

function expireStatusMeta(daysLeft) {
  if (daysLeft < 0) return { tone: "critical", label: "만료" };
  if (daysLeft <= 30) return { tone: "warning", label: "만료 예정" };
  return { tone: "safe", label: "여유" };
}

function buildExpireRows(clients) {
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
    .sort((a, b) => a.daysLeft - b.daysLeft)
    .slice(0, 8);
}

export default function DashboardPage({ clients = [], services = [] }) {
  const expireRows = buildExpireRows(clients);
  const activeClients = clients.filter((client) => client.status === "active").length;
  const expire30 = expireRows.filter((row) => row.daysLeft <= 30).length;
  const serviceAlerts = services.filter((service) => service.status !== "running").length;

  return (
    <div className="page-grid">
      <div className="stats-grid">
        <StatCard label="전체 클라이언트" value={clients.length} subText="SG + XGS 운영 장비" />
        <StatCard label="연결된 클라이언트" value={activeClients} subText="실시간 세션 기준" />
        <StatCard label="30일 이내 만료" value={expire30} subText="사전 갱신 확인 필요" />
        <StatCard label="서비스 알림" value={serviceAlerts} subText="확인 필요한 항목" />
      </div>

      <div className="two-col-grid">
        <div className="panel">
          <h3 className="panel-title">서비스 상태</h3>
          <div className="list-wrap">
            {services.map((service) => (
              <div key={service.name} className="list-row">
                <div>
                  <div className="row-title">{service.name}</div>
                  <div className="row-sub">{service.port}</div>
                </div>
                <StatusBadge status={service.status} />
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <h3 className="panel-title">만료 요약</h3>
          <div className="list-wrap">
            <div className="list-row"><span>오늘 만료</span><strong>{expireRows.filter((row) => row.daysLeft === 0).length}</strong></div>
            <div className="list-row"><span>7일 이내</span><strong>{expireRows.filter((row) => row.daysLeft <= 7).length}</strong></div>
            <div className="list-row"><span>30일 이내</span><strong>{expireRows.filter((row) => row.daysLeft <= 30).length}</strong></div>
            <div className="list-row"><span>이미 만료</span><strong>{expireRows.filter((row) => row.daysLeft < 0).length}</strong></div>
          </div>
        </div>
      </div>

      <div className="panel">
        <h3 className="panel-title">최근 만료 대상</h3>
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
            {expireRows.map((row) => (
              <tr key={`${row.hostname}-${row.expireAt}`}>
                <td>{row.hostname}</td>
                <td>{vpnLabel(row.vpnType, row.assignedIp)}</td>
                <td>{formatDateSeoul(row.expireAt)}</td>
                <td>{row.daysLeft}</td>
                <td><StatusBadge tone={row.status.tone} label={row.status.label} /></td>
              </tr>
            ))}
            {!expireRows.length ? (
              <tr className="placeholder-row"><td colSpan={5}>표시할 만료 대상이 없습니다.</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
