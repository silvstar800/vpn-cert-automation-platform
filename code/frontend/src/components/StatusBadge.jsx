const DEFAULT_LABELS = {
  running: "정상",
  active: "연결됨",
  disconnected: "연결 끊김",
  safe: "여유",
  normal: "정상",
  warning: "만료 예정",
  critical: "만료",
  inactive: "비활성",
  stopped: "중지",
  expired: "만료",
  expiring: "만료 예정",
  healthy: "여유",
};

const CLASS_NAMES = {
  임대: "active",
  미회수: "warning",
  "재고(New)": "safe",
  "재고(Old)": "inactive",
  판매: "normal",
  폐기: "stopped",
  RMA대상: "critical",
  healthy: "safe",
  expiring: "warning",
  expired: "critical",
};

export default function StatusBadge({ status, label, tone }) {
  const value = tone || CLASS_NAMES[status] || status || "inactive";
  const resolvedLabel = label || DEFAULT_LABELS[status] || status || "-";
  return <span className={`status-badge ${value}`}>{resolvedLabel}</span>;
}
