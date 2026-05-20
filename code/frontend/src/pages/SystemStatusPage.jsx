import StatusBadge from "../components/StatusBadge";

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(1)}%` : "-";
}

function formatGb(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(1)} GB` : "-";
}

function serviceBadgeMeta(status) {
  return status === "running"
    ? { tone: "normal", label: "정상" }
    : { tone: "stopped", label: "중지" };
}

export default function SystemStatusPage({ services = [], resources = null }) {
  const cpu = resources?.cpu || {};
  const memory = resources?.memory || {};
  const disk = resources?.disk || resources?.disk_root || {};

  return (
    <div className="page-grid">
      <div className="two-col-grid">
        <div className="panel">
          <h3 className="panel-title">서비스 상태</h3>
          <div className="list-wrap">
            {services.map((service) => {
              const badge = serviceBadgeMeta(service.status);
              return (
                <div key={service.name} className="list-row">
                  <div>
                    <div className="row-title">{service.name}</div>
                    <div className="row-sub">{service.port}</div>
                  </div>
                  <StatusBadge tone={badge.tone} label={badge.label} />
                </div>
              );
            })}
            {!services.length ? <div className="muted-text">표시할 서비스 상태가 없습니다.</div> : null}
          </div>
        </div>

        <div className="panel">
          <h3 className="panel-title">시스템 현재 자원</h3>
          <div className="detail-list">
            <div className="detail-row"><span>CPU 사용률</span><strong>{formatPercent(cpu.usage_percent)}</strong></div>
            <div className="detail-row"><span>메모리 사용률</span><strong>{formatPercent(memory.used_percent)}</strong></div>
            <div className="detail-row"><span>메모리 사용량</span><strong>{formatGb(memory.used_gb)} / {formatGb(memory.total_gb)}</strong></div>
            <div className="detail-row"><span>디스크 사용률</span><strong>{formatPercent(disk.used_percent)}</strong></div>
            <div className="detail-row"><span>디스크 사용량</span><strong>{formatGb(disk.used_gb)} / {formatGb(disk.total_gb)}</strong></div>
          </div>
        </div>
      </div>
    </div>
  );
}
