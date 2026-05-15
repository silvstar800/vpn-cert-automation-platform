import { useEffect, useMemo, useRef, useState } from "react";
import {
  getAlertHistory,
  getBackupLogs,
  getBackupSettings,
  getRuntimeGuardLogs,
  getSecurityMonitor,
  listRestoreBackups,
  runBackupNow,
  runRestoreNow,
  saveBackupSettings,
  saveSlackSettings,
  sendSlackTestMessage,
  testBackupSettings,
  unbanSecurityIp,
  unenrollSecurityClient,
  updateSecurityMonitorSettings,
} from "../api/client";
import { formatDateTimeSeoul } from "../utils/time";
import { deriveVpnType, vpnLabel } from "../utils/vpnLabel";

const SECURITY_MONITOR_TAB_KEY = "certsvc.securityMonitor.activeTab";
const ALERT_REFRESH_COOLDOWN_SEC = 3;

const TAB_ITEMS = [
  { id: "overview", label: "개요" },
  { id: "review", label: "검토 / 해제" },
  { id: "unenroll", label: "연결 해제" },
  { id: "analysis", label: "분석" },
  { id: "settings", label: "설정" },
  { id: "backup", label: "백업" },
  { id: "alarm", label: "알람" },
];

const BACKUP_LOG_JOB_TYPES = new Set(["backup", "connection_test", "settings_update"]);

const CHART_TYPES = [
  { id: "kpi", label: "KPI 카드" },
  { id: "bar", label: "막대 차트" },
  { id: "line", label: "선 차트" },
  { id: "donut", label: "도넛 차트" },
  { id: "stacked", label: "누적 막대" },
  { id: "heatmap", label: "히트맵" },
];

const DEFAULT_BACKUP_FORM = {
  ftpHost: "",
  ftpUsername: "",
  ftpPassword: "",
  ftpRemotePath: "",
  scheduleType: "manual",
  scheduleTime: "",
  scheduleWeekday: 0,
  scheduleMonthday: 1,
  passwordChanged: false,
  passwordConfigured: false,
  passwordMasked: "",
};

const DEFAULT_RESTORE_FORM = {
  ftpHost: "",
  ftpUsername: "",
  ftpPassword: "",
  ftpRemotePath: "",
  backupId: "",
  mode: "validate",
};

const DEFAULT_SLACK_FORM = {
  enabled: false,
  botToken: "",
  channel: "#01-alert",
  notifyCertificateExpiry: true,
  notifyBackupCompleted: true,
  notifySecurityAlert: true,
  notifyServiceDown: true,
  notifyResourceThreshold: true,
  cpuThreshold: 90,
  memoryThreshold: 90,
  diskThreshold: 90,
  tokenChanged: false,
  tokenConfigured: false,
  tokenMasked: "",
};

const DEFAULT_UNENROLL_FORM = {
  hostname: "",
  vpnType: "openvpn",
  note: "",
};

const SLACK_TEMPLATE_OPTIONS = [
  { value: "certificate_expiry", label: "만료 알림" },
  { value: "backup_completed", label: "백업 완료" },
  { value: "restore_validated", label: "백업 검증 완료" },
  { value: "restore_completed", label: "백업 복구 완료" },
  { value: "restore_failed", label: "백업 복구 실패" },
  { value: "security_alert", label: "침입 시도 감지" },
  { value: "service_down", label: "서비스 다운" },
  { value: "service_recovered", label: "서비스 복구" },
  { value: "resource_threshold", label: "자원 임계치 초과" },
];

const RESTORE_STATUS_LABEL = {
  success: "성공",
  failed: "실패",
  skipped: "건너뜀",
};

const WEEKDAY_OPTIONS = [
  { value: 0, label: "월요일" },
  { value: 1, label: "화요일" },
  { value: 2, label: "수요일" },
  { value: 3, label: "목요일" },
  { value: 4, label: "금요일" },
  { value: 5, label: "토요일" },
  { value: 6, label: "일요일" },
];

function formatLogTime(ts) {
  if (!ts) return "-";
  const date = new Date(ts * 1000);
  return Number.isNaN(date.getTime()) ? "-" : formatDateTimeSeoul(date);
}

function formatStatusLabel(status) {
  return RESTORE_STATUS_LABEL[status] || status || "-";
}

function statusClassName(status, fallback = "success") {
  if (status === "failed") return "failed";
  if (status === "skipped") return "skipped";
  if (status === "success") return "success";
  return fallback;
}

function buildBackupPayload(form) {
  return {
    ftpHost: form.ftpHost.trim(),
    ftpUsername: form.ftpUsername.trim(),
    ftpPassword: form.ftpPassword,
    ftpRemotePath: form.ftpRemotePath.trim(),
    scheduleType: form.scheduleType,
    scheduleTime: form.scheduleType === "manual" ? "" : form.scheduleTime,
    scheduleWeekday: form.scheduleType === "weekly" ? Number(form.scheduleWeekday) : null,
    scheduleMonthday: form.scheduleType === "monthly" ? Number(form.scheduleMonthday) : null,
    passwordChanged: Boolean(form.passwordChanged),
  };
}

function buildRestoreBrowsePayload(form) {
  return {
    ftpHost: form.ftpHost.trim(),
    ftpUsername: form.ftpUsername.trim(),
    ftpPassword: form.ftpPassword,
    ftpRemotePath: form.ftpRemotePath.trim(),
  };
}

function buildRestoreRunPayload(form) {
  return {
    ...buildRestoreBrowsePayload(form),
    backupId: form.backupId,
    mode: form.mode,
  };
}

function formatTs(ts) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}

function formatDateKey(date) {
  return date.toISOString().slice(0, 10);
}

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function buildDailySeriesBetween(startDate, endDate) {
  const series = [];
  if (!startDate || !endDate) return series;
  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return series;
  const from = start <= end ? start : end;
  const to = start <= end ? end : start;
  const cursor = new Date(from);
  while (cursor <= to) {
    series.push(formatDateKey(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return series;
}

function getXAxisLabelStep(length) {
  if (length > 24) return 3;
  if (length > 14) return 2;
  return 1;
}

function SvgBarChart({ items, color = "#44cb98", height = 220, labelArea = 48, labelBottomPadding = 14 }) {
  const max = Math.max(...items.map((item) => item.value), 1);
  const width = Math.max(680, 72 + items.length * 34);
  const padding = 36;
  const baselineY = height - labelArea;
  const labelY = height - labelBottomPadding;
  const plotHeight = baselineY - 44;
  const slotWidth = (width - padding * 2) / Math.max(items.length, 1);
  const barWidth = Math.max(10, Math.min(18, slotWidth * 0.42));
  const labelStep = getXAxisLabelStep(items.length);

  return (
    <div className="chart-scroll">
      <div className="chart-scroll-inner">
      <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg chart-svg-wide" role="img" aria-label="막대 차트" style={{ width: `${width}px` }}>
          <line x1={padding} y1={baselineY} x2={width - padding} y2={baselineY} stroke="rgba(255,255,255,0.12)" />
          {items.map((item, index) => {
            const barHeight = (plotHeight * item.value) / max;
            const x = padding + slotWidth * index + (slotWidth - barWidth) / 2;
            const y = baselineY - barHeight;
            return (
              <g key={item.label}>
                <rect x={x} y={y} width={barWidth} height={barHeight} rx="8" fill={color} opacity="0.88" />
                <text
                  x={x + barWidth / 2}
                  y={labelY}
                  textAnchor="end"
                  transform={`rotate(-28 ${x + barWidth / 2} ${labelY})`}
                  className="chart-axis-label chart-axis-label-tilted"
                  opacity={index % labelStep === 0 ? 1 : 0}
                >
                  {item.label}
                </text>
                {item.value > 0 ? <text x={x + barWidth / 2} y={y - 8} textAnchor="middle" className="chart-value-label chart-value-label-raised">{item.value}</text> : null}
              </g>
            );
          })}
          </svg>
      </div>
      </div>
  );
}

function SvgLineChart({ items, color = "#f7c66a", height = 220 }) {
  const max = Math.max(...items.map((item) => item.value), 1);
  const width = Math.max(680, 88 + items.length * 34);
  const padding = 40;
  const baselineY = height - 48;
  const labelY = height - 14;
  const plotWidth = width - padding * 2;
  const plotHeight = height - 92;
  const labelStep = getXAxisLabelStep(items.length);
  const points = items.map((item, index) => {
    const x = padding + (plotWidth * index) / Math.max(items.length - 1, 1);
    const y = baselineY - (plotHeight * item.value) / max;
    return { ...item, x, y };
  });
  const polyline = points.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <div className="chart-scroll">
      <div className="chart-scroll-inner">
      <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg chart-svg-wide" role="img" aria-label="선 차트" style={{ width: `${width}px` }}>
        <line x1={padding} y1={baselineY} x2={width - padding} y2={baselineY} stroke="rgba(255,255,255,0.12)" />
        <polyline fill="none" stroke={color} strokeWidth="4" points={polyline} />
            {points.map((point, index) => (
              <g key={point.label}>
                <circle cx={point.x} cy={point.y} r="5" fill={color} />
                <text
                  x={point.x}
                  y={labelY}
                  textAnchor="end"
                  transform={`rotate(-28 ${point.x} ${labelY})`}
                  className="chart-axis-label chart-axis-label-tilted"
                  opacity={index % labelStep === 0 ? 1 : 0}
                >
                  {point.label}
                </text>
              {point.value > 0 ? <text x={point.x} y={point.y - 18} textAnchor="middle" className="chart-value-label chart-value-label-raised">{point.value}</text> : null}
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}

function SvgDonutChart({ items }) {
  const total = Math.max(items.reduce((sum, item) => sum + item.value, 0), 1);
  const radius = 70;
  const strokeWidth = 24;
  const circumference = 2 * Math.PI * radius;
  let offsetCursor = 0;

  return (
    <div className="chart-donut-shell">
      <svg viewBox="0 0 220 220" className="chart-donut" role="img" aria-label="도넛 차트">
        <g transform="translate(110 110) rotate(-90)">
          <circle cx="0" cy="0" r={radius} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={strokeWidth} />
          {items.map((item) => {
            const slice = (item.value / total) * circumference;
            const dashoffset = circumference - offsetCursor;
            offsetCursor += slice;
            return (
              <circle
                key={item.label}
                cx="0"
                cy="0"
                r={radius}
                fill="none"
                stroke={item.color}
                strokeWidth={strokeWidth}
                strokeDasharray={`${slice} ${circumference - slice}`}
                strokeDashoffset={dashoffset}
              />
            );
          })}
        </g>
        <text x="110" y="102" textAnchor="middle" className="chart-donut-total-label">총계</text>
        <text x="110" y="126" textAnchor="middle" className="chart-donut-total-value">{total}</text>
      </svg>
      <div className="chart-legend">
        {items.map((item) => (
          <div key={item.label} className="chart-legend-item">
            <span className="chart-legend-dot" style={{ background: item.color }} />
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function SvgStackedChart({ items, keys }) {
  const width = Math.max(680, 76 + items.length * 36);
  const height = 248;
  const padding = 36;
  const baselineY = height - 56;
  const labelY = height - 16;
  const plotHeight = height - 108;
  const slotWidth = (width - padding * 2) / Math.max(items.length, 1);
  const barWidth = Math.max(14, Math.min(22, slotWidth * 0.56));
  const max = Math.max(...items.map((item) => keys.reduce((sum, key) => sum + toNumber(item[key.id]), 0)), 1);
  const labelStep = getXAxisLabelStep(items.length);

  return (
        <div className="chart-stacked-shell">
          <div className="chart-scroll">
            <div className="chart-scroll-inner">
            <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg chart-svg-wide" role="img" aria-label="누적 막대 차트" style={{ width: `${width}px` }}>
            <line x1={padding} y1={baselineY} x2={width - padding} y2={baselineY} stroke="rgba(255,255,255,0.12)" />
            {items.map((item, index) => {
              const x = padding + slotWidth * index + (slotWidth - barWidth) / 2;
              let offset = 0;
              return (
                <g key={item.label}>
                {keys.map((key) => {
                  const segmentValue = toNumber(item[key.id]);
                  const segmentHeight = (plotHeight * segmentValue) / max;
                  const y = baselineY - offset - segmentHeight;
                  offset += segmentHeight;
                  return <rect key={key.id} x={x} y={y} width={barWidth} height={segmentHeight} fill={key.color} rx="6" />;
                })}
                  <text
                    x={x + barWidth / 2}
                    y={labelY}
                    textAnchor="end"
                    transform={`rotate(-28 ${x + barWidth / 2} ${labelY})`}
                    className="chart-axis-label chart-axis-label-tilted"
                    opacity={index % labelStep === 0 ? 1 : 0}
                  >
                    {item.label}
                  </text>
                </g>
            );
          })}
          </svg>
            </div>
        </div>
        <div className="chart-legend">
        {keys.map((key) => (
          <div key={key.id} className="chart-legend-item">
            <span className="chart-legend-dot" style={{ background: key.color }} />
            <span>{key.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function HeatmapChart({ rows }) {
  const hours = Array.from({ length: 24 }, (_, index) => index);
  const values = rows.flatMap((row) => hours.map((hour) => toNumber(row.hours?.[hour], 0)));
  const max = Math.max(...values, 1);

  return (
    <div className="heatmap-shell">
      <div className="heatmap-grid">
        <div className="heatmap-row heatmap-header">
          <div className="heatmap-label">일자</div>
          {hours.map((hour) => <div key={hour} className="heatmap-cell heatmap-hour">{hour}</div>)}
        </div>
        {rows.map((row) => (
          <div key={row.label} className="heatmap-row">
            <div className="heatmap-label">{row.label}</div>
            {hours.map((hour) => {
              const value = toNumber(row.hours?.[hour], 0);
              const opacity = value === 0 ? 0.08 : 0.16 + (value / max) * 0.84;
              return (
                <div
                  key={`${row.label}-${hour}`}
                  className="heatmap-cell"
                  style={{ background: `rgba(68, 203, 152, ${opacity})` }}
                  title={`${row.label} ${hour}시: ${value}`}
                >
                  {value > 0 ? value : ""}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SecurityMonitorPage({ clients = [] }) {
  const today = formatDateKey(new Date());
  const defaultStart = formatDateKey(new Date(Date.now() - 6 * 24 * 60 * 60 * 1000));
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedIp, setSelectedIp] = useState("");
  const [releaseNote, setReleaseNote] = useState("");
  const [activeTab, setActiveTab] = useState(() => {
    try {
      const saved = sessionStorage.getItem(SECURITY_MONITOR_TAB_KEY);
      if (saved && TAB_ITEMS.some((tab) => tab.id === saved)) return saved;
    } catch {
      // ignore storage failures
    }
    return "overview";
  });
  const [chartType, setChartType] = useState("kpi");
  const [filterStartDate, setFilterStartDate] = useState(defaultStart);
  const [filterEndDate, setFilterEndDate] = useState(today);
  const [filterIp, setFilterIp] = useState("all");
  const [filterStatus, setFilterStatus] = useState("all");
  const [filterReason, setFilterReason] = useState("all");
  const [filterEndpoint, setFilterEndpoint] = useState("all");
  const [filterHourFrom, setFilterHourFrom] = useState(0);
  const [filterHourTo, setFilterHourTo] = useState(23);
  const [draftSettings, setDraftSettings] = useState({ alertThreshold: 5, banThreshold: 10, windowSeconds: 600 });
  const [backupForm, setBackupForm] = useState(DEFAULT_BACKUP_FORM);
  const [backupSnapshot, setBackupSnapshot] = useState(DEFAULT_BACKUP_FORM);
  const [backupLogs, setBackupLogs] = useState([]);
  const [backupLoading, setBackupLoading] = useState(true);
  const [backupSaving, setBackupSaving] = useState(false);
  const [backupTesting, setBackupTesting] = useState(false);
  const [backupRunning, setBackupRunning] = useState(false);
  const [backupError, setBackupError] = useState("");
  const [backupSuccess, setBackupSuccess] = useState("");
  const [showLogsModal, setShowLogsModal] = useState(false);
  const [showAlertLogsModal, setShowAlertLogsModal] = useState(false);
  const [restoreForm, setRestoreForm] = useState(DEFAULT_RESTORE_FORM);
  const [restoreBackups, setRestoreBackups] = useState([]);
  const [restoreListing, setRestoreListing] = useState(false);
  const [restoreRunning, setRestoreRunning] = useState(false);
  const [restoreError, setRestoreError] = useState("");
  const [restoreSuccess, setRestoreSuccess] = useState("");
  const [restoreResult, setRestoreResult] = useState(null);
  const [slackForm, setSlackForm] = useState(DEFAULT_SLACK_FORM);
  const [slackSnapshot, setSlackSnapshot] = useState(DEFAULT_SLACK_FORM);
  const [slackSaving, setSlackSaving] = useState(false);
  const [slackTesting, setSlackTesting] = useState(false);
  const [slackTemplateType, setSlackTemplateType] = useState("backup_completed");
  const [slackError, setSlackError] = useState("");
  const [slackSuccess, setSlackSuccess] = useState("");
  const [runtimeGuardLogs, setRuntimeGuardLogs] = useState([]);
  const [runtimeGuardPath, setRuntimeGuardPath] = useState("");
  const [runtimeGuardLoading, setRuntimeGuardLoading] = useState(false);
  const [runtimeGuardError, setRuntimeGuardError] = useState("");
  const [alertHistory, setAlertHistory] = useState([]);
  const [alertHistoryLoading, setAlertHistoryLoading] = useState(false);
  const [alertHistoryError, setAlertHistoryError] = useState("");
  const [alertHistoryLastUpdatedAt, setAlertHistoryLastUpdatedAt] = useState(0);
  const [alertRefreshCooldownSec, setAlertRefreshCooldownSec] = useState(0);
  const alertHistoryLoadingRef = useRef(false);
  const alertHistoryLastLoadedAtRef = useRef(0);
  const [unenrollForm, setUnenrollForm] = useState(DEFAULT_UNENROLL_FORM);
  const [unenrollLoading, setUnenrollLoading] = useState(false);
  const [unenrollMessage, setUnenrollMessage] = useState("");
  const [unenrollDropdownOpen, setUnenrollDropdownOpen] = useState(false);
  const [unenrollSearch, setUnenrollSearch] = useState("");
  const selectedUnenrollVpnLabel = unenrollForm.vpnType === "sfos" ? "SFOS" : "OpenVPN (SG/Legacy)";

  useEffect(() => {
    try {
      sessionStorage.setItem(SECURITY_MONITOR_TAB_KEY, activeTab);
    } catch {
      // ignore storage failures
    }
  }, [activeTab]);

  const loadData = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await getSecurityMonitor();
      setData(response);
      setDraftSettings(response.settings || draftSettings);
    } catch (err) {
      setError(err.message || "보안 모니터 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const response = await getBackupSettings();
        if (!alive) return;
        const next = {
          ...DEFAULT_BACKUP_FORM,
          ...response.settings,
          ftpPassword: "",
          passwordChanged: false,
        };
        setBackupForm(next);
        setBackupSnapshot(next);
        setBackupLogs(response.logs || []);
        const slackNext = {
          ...DEFAULT_SLACK_FORM,
          ...(response.slack || {}),
          botToken: "",
          tokenChanged: false,
        };
        setSlackForm(slackNext);
        setSlackSnapshot(slackNext);
        setRestoreForm((prev) => ({
          ...prev,
          ftpHost: next.ftpHost || "",
          ftpUsername: next.ftpUsername || "",
          ftpRemotePath: next.ftpRemotePath || "",
        }));
      } catch (err) {
        if (!alive) return;
        setBackupError(err.message || "백업 설정을 불러오지 못했습니다.");
      } finally {
        if (alive) setBackupLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const loadRuntimeGuardLogs = async () => {
    setRuntimeGuardLoading(true);
    setRuntimeGuardError("");
    try {
      const result = await getRuntimeGuardLogs();
      setRuntimeGuardLogs(result.entries || []);
      setRuntimeGuardPath(result.path || "");
    } catch (err) {
      setRuntimeGuardError(err.message || "runtime guard 로그를 불러오지 못했습니다.");
    } finally {
      setRuntimeGuardLoading(false);
    }
  };

  const loadAlertHistory = async ({ force = false } = {}) => {
    const now = Date.now();
    if (alertHistoryLoadingRef.current) {
      return;
    }
    // Avoid burst requests (e.g., chained actions calling refresh repeatedly).
    if (!force && now - alertHistoryLastLoadedAtRef.current < 1500) {
      return;
    }
    alertHistoryLoadingRef.current = true;
    setAlertHistoryLoading(true);
    setAlertHistoryError("");
    try {
      const result = await getAlertHistory();
      setAlertHistory(result.logs || []);
      const fetchedAt = Date.now();
      const serverUpdatedAtMs = Number(result.serverTs || 0) * 1000;
      alertHistoryLastLoadedAtRef.current = fetchedAt;
      setAlertHistoryLastUpdatedAt(serverUpdatedAtMs > 0 ? serverUpdatedAtMs : fetchedAt);
    } catch (err) {
      setAlertHistoryError(err.message || "알람 이력을 불러오지 못했습니다.");
    } finally {
      alertHistoryLoadingRef.current = false;
      setAlertHistoryLoading(false);
    }
  };

  const handleManualAlertRefresh = async () => {
    if (alertHistoryLoading || alertRefreshCooldownSec > 0) {
      return;
    }
    setAlertRefreshCooldownSec(ALERT_REFRESH_COOLDOWN_SEC);
    await loadAlertHistory({ force: true });
  };

  useEffect(() => {
    if (alertRefreshCooldownSec <= 0) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      setAlertRefreshCooldownSec((prev) => (prev <= 1 ? 0 : prev - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [alertRefreshCooldownSec]);

  useEffect(() => {
    loadRuntimeGuardLogs();
    loadAlertHistory();
  }, []);

  const rows = useMemo(() => data?.rows || [], [data]);
  const selectedRow = useMemo(() => rows.find((row) => row.ip === selectedIp) || null, [rows, selectedIp]);
  const unenrollCandidates = useMemo(() => {
    return (clients || []).map((client) => {
      const resolvedType = deriveVpnType(client.vpnType, client.assignedIp);
      return {
        id: client.id,
        hostname: client.hostname || "",
        vpnType: resolvedType === "sfos" ? "sfos" : "openvpn",
        vpnLabelText: vpnLabel(client.vpnType, client.assignedIp),
        assignedIp: client.assignedIp || "",
      };
    }).filter((item) => item.hostname).sort((a, b) => a.hostname.localeCompare(b.hostname));
  }, [clients]);
  const filteredUnenrollCandidates = useMemo(() => {
    const query = (unenrollSearch || "").trim().toLowerCase();
    return unenrollCandidates.filter((item) => {
      if (!query) return true;
      return [item.hostname, item.vpnLabelText, item.assignedIp].join(" ").toLowerCase().includes(query);
    }).slice(0, 50);
  }, [unenrollCandidates, unenrollSearch]);
  const dailyOverflowRows = useMemo(() => data?.dailyOverflowRows || [], [data]);
  const dailySummaryCandidates = useMemo(() => data?.dailySummaryCandidates || [], [data]);
  const dailySummary = data?.dailySummary || {};
  const recentEvents = useMemo(() => data?.recentEvents || [], [data]);
  const filteredDateKeys = useMemo(() => buildDailySeriesBetween(filterStartDate, filterEndDate), [filterStartDate, filterEndDate]);

  const ipOptions = useMemo(() => {
    const values = new Set();
    rows.forEach((row) => values.add(row.ip));
    recentEvents.forEach((event) => event.ip && values.add(event.ip));
    dailyOverflowRows.forEach((row) => row.ip && values.add(row.ip));
    return ["all", ...Array.from(values).sort()];
  }, [rows, recentEvents, dailyOverflowRows]);

  const reasonOptions = useMemo(() => {
    const values = new Set();
    rows.forEach((row) => row.lastFailureReason && values.add(row.lastFailureReason));
    recentEvents.forEach((event) => event.reason && values.add(event.reason));
    dailyOverflowRows.forEach((row) => row.lastReason && values.add(row.lastReason));
    return ["all", ...Array.from(values).sort()];
  }, [rows, recentEvents, dailyOverflowRows]);

  const endpointOptions = useMemo(() => {
    const values = new Set();
    rows.forEach((row) => row.lastEndpoint && values.add(row.lastEndpoint));
    recentEvents.forEach((event) => event.endpoint && values.add(event.endpoint));
    dailyOverflowRows.forEach((row) => row.lastEndpoint && values.add(row.lastEndpoint));
    return ["all", ...Array.from(values).sort()];
  }, [rows, recentEvents, dailyOverflowRows]);

  const chartEvents = useMemo(() => {
    const allowedDates = new Set(filteredDateKeys);
    return recentEvents.filter((event) => {
      const dateKey = event.ts ? formatDateKey(new Date(event.ts * 1000)) : "";
      if (!allowedDates.has(dateKey)) return false;
      if (filterIp !== "all" && event.ip !== filterIp) return false;
      if (filterStatus !== "all" && event.type !== filterStatus) return false;
      if (filterReason !== "all" && (event.reason || "") !== filterReason) return false;
      if (filterEndpoint !== "all" && (event.endpoint || "") !== filterEndpoint) return false;
      const hour = event.ts ? new Date(event.ts * 1000).getHours() : 0;
      return hour >= filterHourFrom && hour <= filterHourTo;
    });
  }, [recentEvents, filteredDateKeys, filterIp, filterStatus, filterReason, filterEndpoint, filterHourFrom, filterHourTo]);

  const filteredOverflowRows = useMemo(() => {
    const allowedDates = new Set(filteredDateKeys);
    return dailyOverflowRows.filter((row) => {
      if (!allowedDates.has(row.date)) return false;
      if (filterIp !== "all" && row.ip !== filterIp) return false;
      if (filterReason !== "all" && (row.lastReason || "") !== filterReason) return false;
      if (filterEndpoint !== "all" && (row.lastEndpoint || "") !== filterEndpoint) return false;
      return true;
    });
  }, [dailyOverflowRows, filteredDateKeys, filterIp, filterReason, filterEndpoint]);

  const kpis = useMemo(() => {
    const banned = rows.filter((row) => row.status === "banned").length;
    const observed = rows.filter((row) => row.status !== "banned").length;
    const alerts = chartEvents.filter((event) => event.type === "alert").length;
    const failures = chartEvents.filter((event) => event.type === "failure").length;
    const overflow = filteredOverflowRows.reduce((sum, row) => sum + toNumber(row.overflowFailures), 0);
    return { banned, observed, alerts, failures, overflow };
  }, [rows, chartEvents, filteredOverflowRows]);

  const dailyBarData = useMemo(() => filteredDateKeys.map((dateKey) => ({
    label: dateKey.slice(5),
    value: filteredOverflowRows.filter((row) => row.date === dateKey).reduce((sum, row) => sum + toNumber(row.overflowFailures), 0),
  })).filter((item) => item.value > 0), [filteredDateKeys, filteredOverflowRows]);

  const lineData = useMemo(() => filteredDateKeys.map((dateKey) => ({
    label: dateKey.slice(5),
    value: chartEvents.filter((event) => event.ts && formatDateKey(new Date(event.ts * 1000)) === dateKey).length,
  })), [filteredDateKeys, chartEvents]);

  const donutData = useMemo(() => [
    { label: "차단", value: kpis.banned, color: "#ef6666" },
    { label: "관찰", value: kpis.observed, color: "#44cb98" },
    { label: "Alert", value: kpis.alerts, color: "#f7c66a" },
  ], [kpis]);

  const stackedData = useMemo(() => filteredDateKeys.map((dateKey) => {
    const dateEvents = chartEvents.filter((event) => event.ts && formatDateKey(new Date(event.ts * 1000)) === dateKey);
    return {
      label: dateKey.slice(5),
      failure: dateEvents.filter((event) => event.type === "failure").length,
      alert: dateEvents.filter((event) => event.type === "alert").length,
      ban: dateEvents.filter((event) => event.type === "ban").length,
      unban: dateEvents.filter((event) => event.type === "unban").length,
    };
  }).filter((item) => (item.failure + item.alert + item.ban + item.unban) > 0), [filteredDateKeys, chartEvents]);

  const heatmapRows = useMemo(() => filteredDateKeys.map((dateKey) => {
    const hours = {};
    for (let hour = 0; hour < 24; hour += 1) hours[hour] = 0;
    chartEvents.forEach((event) => {
      if (!event.ts) return;
      const date = new Date(event.ts * 1000);
      if (formatDateKey(date) !== dateKey) return;
      hours[date.getHours()] += 1;
    });
    return { label: dateKey.slice(5), hours };
  }), [filteredDateKeys, chartEvents]);

  const topIpData = useMemo(() => {
    const counts = new Map();
    chartEvents.forEach((event) => {
      const key = event.ip || "unknown";
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return Array.from(counts.entries()).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value).slice(0, 8);
  }, [chartEvents]);

  const totalOverflowFailures = useMemo(() => dailyOverflowRows.reduce((sum, row) => sum + Number(row.overflowFailures || 0), 0), [dailyOverflowRows]);

  const isSettingsDirty = useMemo(() => {
    const current = data?.settings || {};
    return Number(draftSettings.alertThreshold) !== Number(current.alertThreshold)
      || Number(draftSettings.banThreshold) !== Number(current.banThreshold)
      || Number(draftSettings.windowSeconds) !== Number(current.windowSeconds);
  }, [data, draftSettings]);

  const backupDirty = useMemo(() => {
    const current = JSON.stringify(buildBackupPayload(backupForm));
    const saved = JSON.stringify(buildBackupPayload(backupSnapshot));
    return current !== saved;
  }, [backupForm, backupSnapshot]);

  const filteredBackupLogs = useMemo(
    () => backupLogs.filter((log) => BACKUP_LOG_JOB_TYPES.has(String(log?.jobType || "").trim())),
    [backupLogs]
  );

  const slackDirty = useMemo(() => {
    const normalize = (form) => JSON.stringify({
      enabled: Boolean(form.enabled),
      channel: (form.channel || "").trim(),
      notifyCertificateExpiry: Boolean(form.notifyCertificateExpiry),
      notifyBackupCompleted: Boolean(form.notifyBackupCompleted),
      notifySecurityAlert: Boolean(form.notifySecurityAlert),
      notifyServiceDown: Boolean(form.notifyServiceDown),
      notifyResourceThreshold: Boolean(form.notifyResourceThreshold),
      cpuThreshold: Number(form.cpuThreshold),
      memoryThreshold: Number(form.memoryThreshold),
      diskThreshold: Number(form.diskThreshold),
      tokenChanged: Boolean(form.tokenChanged),
      botToken: form.botToken || "",
    });
    return normalize(slackForm) !== normalize(slackSnapshot);
  }, [slackForm, slackSnapshot]);

  const handleUnban = async () => {
    if (!selectedIp) return;
    try {
      await unbanSecurityIp({ ip: selectedIp, note: releaseNote });
      setReleaseNote("");
      await loadData();
    } catch (err) {
      setError(err.message || "차단 해제에 실패했습니다.");
    }
  };

  const handleUnenroll = async () => {
    const hostname = (unenrollForm.hostname || "").trim();
    if (!hostname) {
      setError("해지할 호스트명을 입력해 주세요.");
      return;
    }
    setUnenrollLoading(true);
    setUnenrollMessage("");
    setError("");
    try {
      const result = await unenrollSecurityClient({
        hostname,
        vpnType: unenrollForm.vpnType,
        note: unenrollForm.note,
      });
      setUnenrollMessage(
        `해지 완료: ${result.hostname} (${result.vpnType}) / IP 해제 ${result.releasedIpCount}건 / 시리얼 해제 ${result.serialUnlinked}건`
      );
      setUnenrollForm((prev) => ({ ...DEFAULT_UNENROLL_FORM, vpnType: prev.vpnType }));
      await loadData();
    } catch (err) {
      setError(err.message || "연결 해제에 실패했습니다.");
    } finally {
      setUnenrollLoading(false);
    }
  };

  const selectUnenrollHostname = (item) => {
    setUnenrollForm((prev) => ({
      ...prev,
      hostname: item.hostname,
      vpnType: item.vpnType,
    }));
    setUnenrollSearch(item.hostname);
    setUnenrollDropdownOpen(false);
  };

  const handleSaveSettings = async () => {
    try {
      await updateSecurityMonitorSettings({
        alertThreshold: Number(draftSettings.alertThreshold),
        banThreshold: Number(draftSettings.banThreshold),
        windowSeconds: Number(draftSettings.windowSeconds),
      });
      await loadData();
    } catch (err) {
      setError(err.message || "설정 저장에 실패했습니다.");
    }
  };

  const updateBackupField = (field, value) => {
    setBackupForm((prev) => ({ ...prev, [field]: value }));
  };

  const openPasswordEditor = () => {
    setBackupForm((prev) => ({ ...prev, passwordChanged: true, ftpPassword: "" }));
  };

  const cancelPasswordEdit = () => {
    setBackupForm((prev) => ({ ...prev, passwordChanged: false, ftpPassword: "" }));
  };

  const reloadBackupLogs = async () => {
    const response = await getBackupLogs();
    setBackupLogs(response.logs || []);
  };

  const saveBackup = async () => {
    setBackupSaving(true);
    setBackupError("");
    setBackupSuccess("");
    try {
      const response = await saveBackupSettings(buildBackupPayload(backupForm));
      const next = {
        ...DEFAULT_BACKUP_FORM,
        ...response.settings,
        ftpPassword: "",
        passwordChanged: false,
      };
      setBackupForm(next);
      setBackupSnapshot(next);
      setBackupSuccess("백업 설정을 저장했습니다.");
      await reloadBackupLogs();
    } catch (err) {
      setBackupError(err.message || "백업 설정 저장에 실패했습니다.");
    } finally {
      setBackupSaving(false);
    }
  };

  const testBackup = async () => {
    setBackupTesting(true);
    setBackupError("");
    setBackupSuccess("");
    try {
      const result = await testBackupSettings(buildBackupPayload(backupForm));
      setBackupSuccess(`FTP 연결 테스트 성공 (${result.remotePath})`);
      await reloadBackupLogs();
    } catch (err) {
      setBackupError(err.message || "FTP 연결 테스트에 실패했습니다.");
      await reloadBackupLogs();
    } finally {
      setBackupTesting(false);
    }
  };

  const runBackup = async () => {
    setBackupRunning(true);
    setBackupError("");
    setBackupSuccess("");
    try {
      const result = await runBackupNow({});
      setBackupSuccess(`백업이 완료되었습니다. (${result.result.backupId})`);
      await reloadBackupLogs();
      await loadAlertHistory();
    } catch (err) {
      setBackupError(err.message || "백업 실행에 실패했습니다.");
      await reloadBackupLogs();
      await loadAlertHistory();
    } finally {
      setBackupRunning(false);
    }
  };

  const updateRestoreField = (field, value) => {
    setRestoreForm((prev) => ({ ...prev, [field]: value }));
  };

  const updateSlackField = (field, value) => {
    setSlackForm((prev) => ({ ...prev, [field]: value }));
  };

  const openSlackTokenEditor = () => {
    setSlackForm((prev) => ({ ...prev, tokenChanged: true, botToken: "" }));
  };

  const cancelSlackTokenEdit = () => {
    setSlackForm((prev) => ({ ...prev, tokenChanged: false, botToken: "" }));
  };

  const loadRestoreBackups = async () => {
    setRestoreListing(true);
    setRestoreError("");
    setRestoreSuccess("");
    setRestoreResult(null);
    try {
      const result = await listRestoreBackups(buildRestoreBrowsePayload(restoreForm));
      const backups = result.backups || [];
      setRestoreBackups(backups);
      setRestoreForm((prev) => ({
        ...prev,
        backupId: prev.backupId && backups.includes(prev.backupId) ? prev.backupId : backups[0] || "",
      }));
      setRestoreSuccess(backups.length ? `백업 목록을 불러왔습니다. (${backups.length}건)` : "조회된 백업 목록이 없습니다.");
      await reloadBackupLogs();
    } catch (err) {
      setRestoreError(err.message || "백업 목록 조회에 실패했습니다.");
      await reloadBackupLogs();
    } finally {
      setRestoreListing(false);
    }
  };

  const runRestore = async () => {
    setRestoreRunning(true);
    setRestoreError("");
    setRestoreSuccess("");
    setRestoreResult(null);
    try {
      const result = await runRestoreNow(buildRestoreRunPayload(restoreForm));
      const label = restoreForm.mode === "validate" ? "백업 검증이 완료되었습니다." : "백업 복구가 완료되었습니다.";
      setRestoreSuccess(`${label} (${result.result.validation.backupId})`);
      setRestoreResult(result.result || null);
      await reloadBackupLogs();
      await loadRuntimeGuardLogs();
      await loadAlertHistory();
    } catch (err) {
      setRestoreError(err.message || "복구 작업 실행에 실패했습니다.");
      await reloadBackupLogs();
    } finally {
      setRestoreRunning(false);
    }
  };

  const saveSlack = async () => {
    setSlackSaving(true);
    setSlackError("");
    setSlackSuccess("");
    try {
      const result = await saveSlackSettings({
        enabled: Boolean(slackForm.enabled),
        botToken: slackForm.botToken,
        channel: slackForm.channel,
        notifyCertificateExpiry: Boolean(slackForm.notifyCertificateExpiry),
        notifyBackupCompleted: Boolean(slackForm.notifyBackupCompleted),
        notifySecurityAlert: Boolean(slackForm.notifySecurityAlert),
        notifyServiceDown: Boolean(slackForm.notifyServiceDown),
        notifyResourceThreshold: Boolean(slackForm.notifyResourceThreshold),
        cpuThreshold: Number(slackForm.cpuThreshold),
        memoryThreshold: Number(slackForm.memoryThreshold),
        diskThreshold: Number(slackForm.diskThreshold),
        tokenChanged: Boolean(slackForm.tokenChanged),
      });
      const next = {
        ...DEFAULT_SLACK_FORM,
        ...(result.slack || {}),
        botToken: "",
        tokenChanged: false,
      };
      setSlackForm(next);
      setSlackSnapshot(next);
      setSlackSuccess("슬랙 알림 설정을 저장했습니다.");
      await reloadBackupLogs();
      await loadAlertHistory();
    } catch (err) {
      setSlackError(err.message || "슬랙 알림 설정 저장에 실패했습니다.");
    } finally {
      setSlackSaving(false);
    }
  };

  const testSlack = async () => {
    setSlackTesting(true);
    setSlackError("");
    setSlackSuccess("");
    try {
      const result = await sendSlackTestMessage({ templateType: slackTemplateType });
      setSlackSuccess(`슬랙 테스트 메시지를 전송했습니다. (${result.channel})`);
      await reloadBackupLogs();
      await loadAlertHistory();
    } catch (err) {
      setSlackError(err.message || "슬랙 테스트 메시지 전송에 실패했습니다.");
      await reloadBackupLogs();
      await loadAlertHistory();
    } finally {
      setSlackTesting(false);
    }
  };

  const renderPasswordField = () => {
    if (backupForm.passwordChanged) {
      return (
        <div className="password-edit-shell">
          <input
            className="input"
            type="password"
            value={backupForm.ftpPassword}
            onChange={(e) => updateBackupField("ftpPassword", e.target.value)}
            placeholder="새 FTP 패스워드를 입력하세요"
          />
          {backupForm.passwordConfigured && (
            <button className="ghost-btn compact-btn" type="button" onClick={cancelPasswordEdit}>
              유지
            </button>
          )}
        </div>
      );
    }

    return (
      <div className="password-mask-row">
        <div className="password-mask-box">{backupForm.passwordMasked || "미설정"}</div>
        <button className="ghost-btn compact-btn" type="button" onClick={openPasswordEditor}>
          변경
        </button>
      </div>
    );
  };

  const renderSlackTokenField = () => {
    if (slackForm.tokenChanged) {
      return (
        <div className="password-edit-shell">
          <input
            className="input"
            type="password"
            value={slackForm.botToken}
            onChange={(e) => updateSlackField("botToken", e.target.value)}
            placeholder="xoxb- 로 시작하는 Bot Token"
          />
          {slackForm.tokenConfigured && (
            <button className="ghost-btn compact-btn" type="button" onClick={cancelSlackTokenEdit}>
              유지
            </button>
          )}
        </div>
      );
    }

    return (
      <div className="password-mask-row">
        <div className="password-mask-box">{slackForm.tokenMasked || "미설정"}</div>
        <button className="ghost-btn compact-btn" type="button" onClick={openSlackTokenEditor}>
          변경
        </button>
      </div>
    );
  };

  const renderUnenrollFormSection = () => (
    <>
      <h3 className="panel-title">클라이언트 연결 해제</h3>
      <p className="muted-text">OpenVPN은 클라이언트/IP lease를 삭제하고, SFOS는 클라이언트/IP lease 삭제와 시리얼 연결 해제를 함께 수행합니다.</p>
      <div className="field-group apc-select-shell unenroll-select-shell">
        <label>호스트명</label>
        <button
          className={`select-trigger ${unenrollDropdownOpen ? "open" : ""}`}
          type="button"
          onClick={() => setUnenrollDropdownOpen((prev) => !prev)}
        >
          <span>{unenrollForm.hostname || "호스트를 검색해서 선택하세요"}</span>
          <span className="select-caret">▼</span>
        </button>
        {unenrollDropdownOpen ? (
          <div className="apc-dropdown">
            <input
              className="input apc-dropdown-search"
              value={unenrollSearch}
              onChange={(event) => setUnenrollSearch(event.target.value)}
              placeholder="호스트명 / VPN 타입 / IP 검색"
            />
            <div className="apc-dropdown-list">
              {filteredUnenrollCandidates.map((item) => (
                <button
                  key={`${item.id}-${item.hostname}`}
                  className={`apc-dropdown-item ${unenrollForm.hostname === item.hostname ? "selected" : ""}`}
                  type="button"
                  onClick={() => selectUnenrollHostname(item)}
                >
                  <strong>{item.hostname}</strong>
                  <span>{item.vpnLabelText} / {item.assignedIp || "IP 미할당"}</span>
                </button>
              ))}
              {!filteredUnenrollCandidates.length ? <div className="apc-dropdown-empty">선택 가능한 호스트가 없습니다.</div> : null}
            </div>
          </div>
        ) : null}
      </div>
      <div className="field-group">
        <label>VPN 타입</label>
        <div className="readonly-display">{selectedUnenrollVpnLabel}</div>
      </div>
      <div className="unenroll-action-row">
        <div className="field-group unenroll-note-field">
          <label>처리 메모</label>
          <textarea
            className="input"
            rows="2"
            value={unenrollForm.note}
            onChange={(e) => setUnenrollForm((prev) => ({ ...prev, note: e.target.value }))}
            placeholder="선택 사항"
          />
        </div>
        <div className="button-group settings-actions unenroll-action-btn-wrap">
          <button
            className="primary-btn"
            type="button"
            onClick={handleUnenroll}
            disabled={unenrollLoading}
          >
            {unenrollLoading ? "처리 중..." : "연결 해제"}
          </button>
        </div>
      </div>
      {unenrollMessage && <p className="success-text">{unenrollMessage}</p>}
    </>
  );

  const renderVisualChart = () => {
    switch (chartType) {
        case "bar":
          return (
            <>
              <div className="panel"><h3 className="panel-title">일자별 추가 시도</h3><SvgBarChart items={dailyBarData} /></div>
              <div className="panel"><h3 className="panel-title">상위 IP 이벤트 수</h3><SvgBarChart items={topIpData.length ? topIpData : [{ label: "없음", value: 0 }]} color="#6ab8ff" height={300} labelArea={82} labelBottomPadding={34} /></div>
            </>
          );
      case "line":
        return <div className="panel"><h3 className="panel-title">기간별 이벤트 추세</h3><SvgLineChart items={lineData} /></div>;
      case "donut":
        return <div className="panel"><h3 className="panel-title">상태 / 알림 비중</h3><SvgDonutChart items={donutData} /></div>;
      case "stacked":
        return (
          <div className="panel">
            <h3 className="panel-title">일자별 이벤트 구성</h3>
            <SvgStackedChart
              items={stackedData}
              keys={[
                { id: "failure", label: "Failure", color: "#6ab8ff" },
                { id: "alert", label: "Alert", color: "#f7c66a" },
                { id: "ban", label: "Ban", color: "#ef6666" },
                { id: "unban", label: "Unban", color: "#44cb98" },
              ]}
            />
          </div>
        );
      case "heatmap":
        return <div className="panel"><h3 className="panel-title">시간대별 이벤트 히트맵</h3><HeatmapChart rows={heatmapRows} /></div>;
      case "kpi":
      default:
        return (
          <div className="stats-grid">
            <div className="stat-card"><span className="stat-label">기간 내 실패</span><strong className="stat-value">{kpis.failures}</strong></div>
            <div className="stat-card"><span className="stat-label">기간 내 Alert</span><strong className="stat-value">{kpis.alerts}</strong></div>
            <div className="stat-card"><span className="stat-label">현재 차단 대상</span><strong className="stat-value">{kpis.banned}</strong></div>
            <div className="stat-card"><span className="stat-label">추가 시도 누적</span><strong className="stat-value">{kpis.overflow}</strong></div>
          </div>
        );
    }
  };

  const renderOverviewTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="panel">
        <h3 className="panel-title">운영 요약</h3>
        <p className="muted-text">현재 수동 검토가 필요한 대상과 모니터 기준을 한눈에 확인합니다.</p>
        <div className="detail-list">
          <div className="detail-row"><span>슬랙 알림</span><strong>{data?.slackEnabled ? "활성" : "비활성"}</strong></div>
          <div className="detail-row"><span>알림 채널</span><strong>{data?.slackChannel || "#01-alert"}</strong></div>
          <div className="detail-row"><span>집계 시간</span><strong>{draftSettings.windowSeconds}초</strong></div>
          <div className="detail-row"><span>알림 기준</span><strong>{draftSettings.alertThreshold}회</strong></div>
          <div className="detail-row"><span>차단 기준</span><strong>{draftSettings.banThreshold}회</strong></div>
        </div>
      </div>
      <div className="panel">
        <h3 className="panel-title">빠른 현황</h3>
        <div className="detail-list">
          <div className="detail-row"><span>차단 대상</span><strong>{rows.filter((row) => row.status === "banned").length}건</strong></div>
          <div className="detail-row"><span>관찰 대상</span><strong>{rows.filter((row) => row.status !== "banned").length}건</strong></div>
          <div className="detail-row"><span>추가 시도 누적</span><strong>{totalOverflowFailures}회</strong></div>
          <div className="detail-row"><span>전일 요약 후보</span><strong>{dailySummaryCandidates.length}건</strong></div>
        </div>
      </div>
    </div>
  );

  const renderVisualTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="panel">
        <h3 className="panel-title">시각화 필터</h3>
        <div className="visual-filter-grid">
          <div className="field-group"><label>차트 유형</label><select className="select" value={chartType} onChange={(e) => setChartType(e.target.value)}>{CHART_TYPES.map((type) => <option key={type.id} value={type.id}>{type.label}</option>)}</select></div>
          <div className="field-group"><label>시작일</label><input className="input" type="date" value={filterStartDate} onChange={(e) => setFilterStartDate(e.target.value)} /></div>
          <div className="field-group"><label>종료일</label><input className="input" type="date" value={filterEndDate} onChange={(e) => setFilterEndDate(e.target.value)} /></div>
        </div>
        <div className="visual-filter-grid visual-filter-grid-half top-gap">
          <div className="field-group"><label>시간대 시작</label><select className="select" value={filterHourFrom} onChange={(e) => setFilterHourFrom(Number(e.target.value))}>{Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{hour === 0 ? "0시(24시)" : `${hour}시`}</option>)}</select></div>
          <div className="field-group"><label>시간대 종료</label><select className="select" value={filterHourTo} onChange={(e) => setFilterHourTo(Number(e.target.value))}>{Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{hour === 0 ? "0시(24시)" : `${hour}시`}</option>)}</select></div>
        </div>
        <div className="visual-filter-grid visual-filter-grid-four top-gap">
          <div className="field-group"><label>IP</label><select className="select" value={filterIp} onChange={(e) => setFilterIp(e.target.value)}>{ipOptions.map((ip) => <option key={ip} value={ip}>{ip === "all" ? "전체" : ip}</option>)}</select></div>
          <div className="field-group"><label>상태</label><select className="select" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}><option value="all">전체</option><option value="failure">Failure</option><option value="alert">Alert</option><option value="ban">Ban</option><option value="unban">Unban</option></select></div>
          <div className="field-group"><label>사유</label><select className="select" value={filterReason} onChange={(e) => setFilterReason(e.target.value)}>{reasonOptions.map((reason) => <option key={reason} value={reason}>{reason === "all" ? "전체" : reason}</option>)}</select></div>
          <div className="field-group"><label>엔드포인트</label><select className="select" value={filterEndpoint} onChange={(e) => setFilterEndpoint(e.target.value)}>{endpointOptions.map((endpoint) => <option key={endpoint} value={endpoint}>{endpoint === "all" ? "전체" : endpoint}</option>)}</select></div>
        </div>
        <p className="muted-text top-gap">기본값은 최근 7일 범위입니다. 시작일과 종료일을 직접 지정해 원하는 기간만 시각화할 수 있습니다.</p>
      </div>
      {renderVisualChart()}
    </div>
  );

  const renderReviewTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="panel">
        <h3 className="panel-title">수동 검토 대상</h3>
        <p className="muted-text">차단 또는 관찰 중인 IP를 선택한 뒤, 검토 메모와 함께 해제할 수 있습니다.</p>
        <div className="table-wrap"><table className="data-table"><thead><tr><th>IP</th><th>상태</th><th>현재 실패</th><th>누적 실패</th><th>최근 사유</th><th>최근 호스트</th><th>차단 시각</th></tr></thead><tbody>{rows.length === 0 ? <tr><td colSpan="7" className="muted-text">현재 검토 대상이 없습니다.</td></tr> : rows.map((row) => <tr key={row.ip} className={selectedIp === row.ip ? "selected-row" : ""} onClick={() => setSelectedIp(row.ip)}><td>{row.ip}</td><td>{row.status === "banned" ? "차단" : row.status === "released" ? "해제" : "관찰"}</td><td>{row.failureCount}</td><td>{row.cumulativeFailures}</td><td>{row.lastFailureReason || "-"}</td><td>{row.lastHostname || "-"}</td><td>{formatTs(row.bannedAt)}</td></tr>)}</tbody></table></div>
      </div>
      <div className="panel">
        <h3 className="panel-title">차단 해제</h3>
        {!selectedRow ? <p className="muted-text">차단 해제할 IP를 목록에서 선택해 주세요.</p> : <><div className="detail-list"><div className="detail-row"><span>선택 IP</span><strong>{selectedRow.ip}</strong></div><div className="detail-row"><span>상태</span><strong>{selectedRow.status}</strong></div><div className="detail-row"><span>최근 실패</span><strong>{selectedRow.lastFailureReason || "-"}</strong></div><div className="detail-row"><span>최근 이벤트</span><strong>{formatTs(selectedRow.lastFailureTs)}</strong></div></div><div className="field-group"><label>해제 메모</label><textarea className="input" rows="3" value={releaseNote} onChange={(e) => setReleaseNote(e.target.value)} placeholder="수동 검토 결과를 적어 주세요" /></div><div className="button-group settings-actions"><button className="primary-btn" type="button" onClick={handleUnban}>선택 IP 해제</button></div></>}
      </div>
    </div>
  );

  const renderUnenrollTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="panel">
        {renderUnenrollFormSection()}
      </div>
    </div>
  );

  const renderAnalysisTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="panel"><h3 className="panel-title">일별 추가 시도 현황</h3><p className="muted-text">첫 alert 이후 추가로 발생한 실패만 누적합니다. 향후 전일 08시 요약 발송의 기준 데이터로 사용됩니다.</p><div className="table-wrap"><table className="data-table"><thead><tr><th>일자</th><th>IP</th><th>추가 시도</th><th>최근 사유</th><th>최근 호스트</th><th>엔드포인트</th></tr></thead><tbody>{dailyOverflowRows.length === 0 ? <tr><td colSpan="6" className="muted-text">누적된 추가 시도가 없습니다.</td></tr> : dailyOverflowRows.map((row) => <tr key={`${row.date}-${row.ip}`}><td>{row.date}</td><td>{row.ip}</td><td>{row.overflowFailures}</td><td>{row.lastReason || "-"}</td><td>{row.lastHostname || "-"}</td><td>{row.lastEndpoint || "-"}</td></tr>)}</tbody></table></div></div>
      <div className="panel"><h3 className="panel-title">전일 요약 후보</h3><p className="muted-text">{dailySummary.enabled ? "슬랙 알람이 활성화되어 있어 전일 08시 기준 요약 알림을 보냅니다. 아래는 발송 후보 대상입니다." : "현재 전일 요약 발송은 비활성입니다. 알람 탭에서 슬랙 알림을 활성화하면 매일 지정 시각 기준으로 요약을 보냅니다."}</p><div className="detail-list"><div className="detail-row"><span>요약 발송 기능</span><strong>{dailySummary.enabled ? "활성" : "비활성"}</strong></div><div className="detail-row"><span>예정 시각</span><strong>매일 {dailySummary.scheduleHour ?? 8}:00 ({dailySummary.timezone || "Asia/Seoul"})</strong></div><div className="detail-row"><span>요약 기준</span><strong>추가 시도 {dailySummary.minOverflowFailures ?? 10}회 이상</strong></div></div><div className="table-wrap"><table className="data-table"><thead><tr><th>일자</th><th>IP</th><th>추가 시도</th><th>최근 사유</th><th>최근 호스트</th></tr></thead><tbody>{dailySummaryCandidates.length === 0 ? <tr><td colSpan="5" className="muted-text">현재 요약 후보가 없습니다.</td></tr> : dailySummaryCandidates.map((row) => <tr key={`summary-${row.date}-${row.ip}`}><td>{row.date}</td><td>{row.ip}</td><td>{row.overflowFailures}</td><td>{row.lastReason || "-"}</td><td>{row.lastHostname || "-"}</td></tr>)}</tbody></table></div></div>
    </div>
  );

  const renderSettingsTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="settings-top-grid">
        <div className="panel settings-panel">
          <h3 className="panel-title">모니터 기준 설정</h3>
          <div className="field-group">
            <label>알림 기준 실패 횟수</label>
            <input
              className="input"
              type="number"
              min="1"
              value={draftSettings.alertThreshold}
              onChange={(e) => setDraftSettings((prev) => ({ ...prev, alertThreshold: Number(e.target.value) }))}
            />
          </div>
          <div className="field-group">
            <label>차단 기준 실패 횟수</label>
            <input
              className="input"
              type="number"
              min="1"
              value={draftSettings.banThreshold}
              onChange={(e) => setDraftSettings((prev) => ({ ...prev, banThreshold: Number(e.target.value) }))}
            />
          </div>
          <div className="field-group">
            <label>집계 시간(초)</label>
            <input
              className="input"
              type="number"
              min="60"
              value={draftSettings.windowSeconds}
              onChange={(e) => setDraftSettings((prev) => ({ ...prev, windowSeconds: Number(e.target.value) }))}
            />
          </div>
          <div className="button-group settings-actions">
            <button className="primary-btn" type="button" onClick={handleSaveSettings} disabled={!isSettingsDirty}>
              설정 저장
            </button>
            <button className="ghost-btn" type="button" onClick={loadData}>
              새로고침
            </button>
          </div>
        </div>

        <div className="panel settings-panel">
          <div className="detail-header">
            <h3 className="panel-title">백업 설정</h3>
            <button className="ghost-btn compact-btn" type="button" onClick={() => setShowLogsModal(true)}>
              결과 로그 보기
            </button>
          </div>
          <p className="muted-text">
            관리자 영역에서 FTP 대상과 백업 주기를 관리합니다. 패스워드는 마스킹 상태로 유지되며 변경할 때만 새 값을 입력합니다.
          </p>

          {backupLoading ? (
            <p className="muted-text">백업 설정을 불러오는 중입니다...</p>
          ) : (
            <>
              <div className="field-group">
                <label>FTP 서버 IP</label>
                <input
                  className="input"
                  value={backupForm.ftpHost}
                  onChange={(e) => updateBackupField("ftpHost", e.target.value)}
                  placeholder="예: 10.0.0.50"
                />
              </div>

              <div className="two-col-grid">
                <div className="field-group">
                  <label>계정</label>
                  <input
                    className="input"
                    value={backupForm.ftpUsername}
                    onChange={(e) => updateBackupField("ftpUsername", e.target.value)}
                    placeholder="FTP 계정"
                  />
                </div>
                <div className="field-group">
                  <label>패스워드</label>
                  {renderPasswordField()}
                </div>
              </div>

              <div className="field-group">
                <label>업로드 경로</label>
                <input
                  className="input"
                  value={backupForm.ftpRemotePath}
                  onChange={(e) => updateBackupField("ftpRemotePath", e.target.value)}
                  placeholder="예: /certsvc/backups"
                />
              </div>

              <div className="backup-schedule-grid">
                <div className="field-group">
                  <label>백업 주기</label>
                  <select
                    className="select"
                    value={backupForm.scheduleType}
                    onChange={(e) => updateBackupField("scheduleType", e.target.value)}
                  >
                    <option value="manual">수동</option>
                    <option value="daily">매일</option>
                    <option value="weekly">매주</option>
                    <option value="monthly">매달</option>
                  </select>
                </div>
                <div className="field-group">
                  <label>백업 시간</label>
                  <input
                    className="input"
                    type="time"
                    value={backupForm.scheduleTime || ""}
                    onChange={(e) => updateBackupField("scheduleTime", e.target.value)}
                    disabled={backupForm.scheduleType === "manual"}
                  />
                </div>
                {backupForm.scheduleType === "weekly" && (
                  <div className="field-group">
                    <label>실행 요일</label>
                    <select
                      className="select"
                      value={backupForm.scheduleWeekday ?? 0}
                      onChange={(e) => updateBackupField("scheduleWeekday", Number(e.target.value))}
                    >
                      {WEEKDAY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {backupForm.scheduleType === "monthly" && (
                  <div className="field-group">
                    <label>실행 일자</label>
                    <input
                      className="input"
                      type="number"
                      min="1"
                      max="31"
                      value={backupForm.scheduleMonthday ?? 1}
                      onChange={(e) => updateBackupField("scheduleMonthday", Number(e.target.value))}
                    />
                  </div>
                )}
              </div>

              {backupError && <p className="error-text">{backupError}</p>}
              {backupSuccess && <p className="success-text">{backupSuccess}</p>}
              {backupDirty && <p className="muted-text">백업 설정 변경사항이 있습니다. 저장 후 주기 실행과 수동 실행에 반영됩니다.</p>}

              <div className="button-group settings-actions">
                <button className="ghost-btn" type="button" onClick={testBackup} disabled={backupTesting || backupSaving}>
                  {backupTesting ? "확인 중..." : "테스트 연결"}
                </button>
                <button className="ghost-btn" type="button" onClick={runBackup} disabled={backupRunning || backupSaving}>
                  {backupRunning ? "백업 중..." : "지금 백업 실행"}
                </button>
                <button className="primary-btn" type="button" onClick={saveBackup} disabled={backupSaving}>
                  {backupSaving ? "저장 중..." : "저장"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="panel settings-panel">
        <h3 className="panel-title">복구 실행</h3>
        <p className="muted-text">
          관리자 영역에서만 FTP 정보를 직접 입력해 백업 목록을 조회합니다. 기본은 검증모드이며, 복구모드를 선택하면 다운로드 후 실제 복구까지 수행합니다.
        </p>

        <div className="field-group">
          <label>FTP 서버 IP</label>
          <input
            className="input"
            value={restoreForm.ftpHost}
            onChange={(e) => updateRestoreField("ftpHost", e.target.value)}
            placeholder="예: 10.0.0.50"
          />
        </div>

        <div className="two-col-grid">
          <div className="field-group">
            <label>계정</label>
            <input
              className="input"
              value={restoreForm.ftpUsername}
              onChange={(e) => updateRestoreField("ftpUsername", e.target.value)}
              placeholder="FTP 계정"
            />
          </div>
          <div className="field-group">
            <label>패스워드</label>
            <input
              className="input"
              type="password"
              value={restoreForm.ftpPassword}
              onChange={(e) => updateRestoreField("ftpPassword", e.target.value)}
              placeholder="FTP 패스워드"
            />
          </div>
        </div>

        <div className="two-col-grid">
          <div className="field-group">
            <label>백업 경로</label>
            <input
              className="input"
              value={restoreForm.ftpRemotePath}
              onChange={(e) => updateRestoreField("ftpRemotePath", e.target.value)}
              placeholder="예: /certsvc/backups"
            />
          </div>
          <div className="field-group">
            <label>실행 모드</label>
            <select
              className="select"
              value={restoreForm.mode}
              onChange={(e) => updateRestoreField("mode", e.target.value)}
            >
              <option value="validate">검증모드</option>
              <option value="restore">복구모드</option>
            </select>
          </div>
        </div>

        <div className="two-col-grid">
          <div className="field-group">
            <label>백업 목록</label>
            <select
              className="select"
              value={restoreForm.backupId}
              onChange={(e) => updateRestoreField("backupId", e.target.value)}
              disabled={restoreBackups.length === 0}
            >
              <option value="">백업을 선택하세요</option>
              {restoreBackups.map((backupId) => (
                <option key={backupId} value={backupId}>
                  {backupId}
                </option>
              ))}
            </select>
          </div>
          <div className="field-group">
            <label>안내</label>
            <div className="readonly-note">
              {restoreForm.mode === "validate"
                ? "선택한 백업을 다운로드하고 검증까지만 수행합니다."
                : "다운로드 후 검증을 통과하면 실제 복구를 수행합니다."}
            </div>
          </div>
        </div>

        {restoreError && <p className="error-text">{restoreError}</p>}
        {restoreSuccess && <p className="success-text">{restoreSuccess}</p>}

        <div className="button-group settings-actions">
          <button className="ghost-btn" type="button" onClick={loadRestoreBackups} disabled={restoreListing || restoreRunning}>
            {restoreListing ? "조회 중..." : "백업 목록 조회"}
          </button>
          <button
            className={restoreForm.mode === "validate" ? "ghost-btn" : "primary-btn"}
            type="button"
            onClick={runRestore}
            disabled={restoreRunning || !restoreForm.backupId}
          >
            {restoreRunning
              ? restoreForm.mode === "validate"
                ? "검증 중..."
                : "복구 중..."
              : restoreForm.mode === "validate"
                ? "검증 실행"
                : "복구 실행"}
          </button>
        </div>
      </div>

      <div className="panel settings-panel">
        <h3 className="panel-title">전일 요약 준비 상태</h3>
        <p className="muted-text">{dailySummary.enabled ? "현재 전일 요약 자동 발송이 활성화되어 있습니다. 매일 지정 시각 기준으로 슬랙 요약 알림을 보냅니다." : "현재 전일 요약 자동 발송은 비활성입니다. 알람 탭에서 슬랙 알림을 활성화하면 매일 지정 시각 기준으로 슬랙 요약 알림을 보냅니다."}</p>
        <div className="detail-list">
          <div className="detail-row"><span>발송 기능</span><strong>{dailySummary.enabled ? "활성" : "비활성"}</strong></div>
          <div className="detail-row"><span>예정 시각</span><strong>매일 {dailySummary.scheduleHour ?? 8}:00</strong></div>
          <div className="detail-row"><span>기준 시간대</span><strong>{dailySummary.timezone || "Asia/Seoul"}</strong></div>
          <div className="detail-row"><span>후보 기준</span><strong>추가 시도 {dailySummary.minOverflowFailures ?? 10}회 이상</strong></div>
          <div className="detail-row"><span>마지막 발송 일자</span><strong>{dailySummary.lastSentDate || "-"}</strong></div>
        </div>
      </div>
    </div>
  );

  const renderRuntimeGuardPanel = () => (
    <div className="panel settings-panel">
      <div className="detail-header">
        <div>
          <h3 className="panel-title">Runtime Guard 로그</h3>
          <p className="muted-text">재부팅 후 서비스, 인터페이스, 방화벽 규칙 자동 점검 이력을 최근 기준으로 확인합니다.</p>
        </div>
        <button className="ghost-btn compact-btn" type="button" onClick={loadRuntimeGuardLogs} disabled={runtimeGuardLoading}>
          {runtimeGuardLoading ? "불러오는 중..." : "새로고침"}
        </button>
      </div>
      {runtimeGuardPath ? <p className="muted-text">로그 경로: {runtimeGuardPath}</p> : null}
      {runtimeGuardError ? <p className="error-text">{runtimeGuardError}</p> : null}
      <div className="log-list-shell">
        {runtimeGuardLogs.length === 0 ? (
          <p className="muted-text">표시할 runtime guard 로그가 없습니다.</p>
        ) : (
          runtimeGuardLogs.map((entry, index) => (
            <div className="inline-log-card" key={`${entry.ts || "no-ts"}-${index}`}>
              <div className="detail-row">
                <strong>{entry.level || "INFO"}</strong>
                <span className="muted-text">{entry.ts || "-"}</span>
              </div>
              <pre className="log-detail-pre">{entry.message || "-"}</pre>
            </div>
          ))
        )}
      </div>
    </div>
  );

  const renderRestoreResultPanel = () => {
    if (!restoreResult) return null;
    const validation = restoreResult.validation || {};
    const restore = restoreResult.restore || null;
    const checks = validation.checks || [];
    const stages = restore?.stages || [];
    const serviceChecklist = restore?.serviceChecklist || [];

    return (
      <div className="panel settings-panel">
        <h3 className="panel-title">{restoreResult.mode === "restore" ? "복구 결과" : "검증 결과"}</h3>
        <div className="detail-list">
          <div className="detail-row"><span>백업 ID</span><strong>{validation.backupId || "-"}</strong></div>
          <div className="detail-row"><span>실행 모드</span><strong>{restoreResult.mode === "restore" ? "복구모드" : "검증모드"}</strong></div>
          <div className="detail-row"><span>검증 상태</span><strong>{validation.valid ? "정상" : "오류 있음"}</strong></div>
          <div className="detail-row"><span>DB 객체 힌트</span><strong>{validation.dbObjectCountHint ?? 0}개</strong></div>
          {restore?.restorePoint ? <div className="detail-row"><span>복구 전 자동 백업</span><strong>{restore.restorePoint}</strong></div> : null}
          {restore ? <div className="detail-row"><span>복구 실행 단계</span><strong>{stages.length}건</strong></div> : null}
          {restore ? <div className="detail-row"><span>서비스 체크리스트</span><strong>{restore.serviceChecklistOk ? "정상" : "확인 필요"}</strong></div> : null}
        </div>

        <div className="field-group top-gap">
          <label>검증 체크 항목</label>
          <div className="check-result-list">
            {checks.map((item, index) => (
              <div className="check-result-card" key={`${item.name}-${index}`}>
                <div className="detail-row">
                  <strong>{item.name}</strong>
                  <span className={`status-chip status-${statusClassName(item.status)}`}>{formatStatusLabel(item.status)}</span>
                </div>
                <div className="muted-text">{item.detail || "-"}</div>
              </div>
            ))}
          </div>
        </div>

        {stages.length > 0 ? (
          <div className="field-group top-gap">
            <label>복구 실행 단계</label>
            <div className="check-result-list">
              {stages.map((item, index) => (
                <div className="check-result-card" key={`${item.name}-${index}`}>
                  <div className="detail-row">
                    <strong>{item.name}</strong>
                    <span className={`status-chip status-${statusClassName(item.status)}`}>{formatStatusLabel(item.status)}</span>
                  </div>
                  <div className="muted-text">{item.detail || "-"}</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {serviceChecklist.length > 0 ? (
          <div className="field-group top-gap">
            <label>복구 후 서비스 체크리스트</label>
            <div className="check-result-list">
              {serviceChecklist.map((item, index) => (
                <div className="check-result-card" key={`${item.name}-${index}`}>
                  <div className="detail-row">
                    <strong>{item.name}</strong>
                    <span className={`status-chip status-${item.ok ? "success" : "failed"}`}>{item.ok ? "정상" : "확인 필요"}</span>
                  </div>
                  <div className="muted-text">{item.detail || "-"}</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    );
  };

  const renderAlertHistoryPanel = () => (
    <div className="panel settings-panel">
      <div className="detail-header">
        <div>
          <h3 className="panel-title">알람 이력</h3>
          <p className="muted-text">슬랙 발송 및 자동 감지로 남은 최근 알람 이력을 확인합니다.</p>
        </div>
        <button className="ghost-btn compact-btn" type="button" onClick={() => setShowAlertLogsModal(true)}>
          결과 로그 보기
        </button>
      </div>
      {alertHistoryError ? <p className="error-text">{alertHistoryError}</p> : null}
      <div className="detail-list">
        <div className="detail-row"><span>최근 알람 수</span><strong>{alertHistory.length}건</strong></div>
        <div className="detail-row"><span>최근 상태</span><strong>{alertHistory[0]?.status || "-"}</strong></div>
        <div className="detail-row"><span>최근 유형</span><strong>{alertHistory[0]?.jobType || "-"}</strong></div>
        <div className="detail-row"><span>최근 시각</span><strong>{alertHistory[0] ? formatLogTime(alertHistory[0].ts) : "-"}</strong></div>
        <div className="detail-row"><span>마지막 갱신</span><strong>{alertHistoryLastUpdatedAt ? formatDateTimeSeoul(new Date(alertHistoryLastUpdatedAt)) : "-"}</strong></div>
      </div>
    </div>
  );

  const renderMonitorSettingsTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="settings-top-grid">
        <div className="panel settings-panel">
          <h3 className="panel-title">모니터 기준 설정</h3>
          <div className="field-group">
            <label>알림 기준 실패 횟수</label>
            <input
              className="input"
              type="number"
              min="1"
              value={draftSettings.alertThreshold}
              onChange={(e) => setDraftSettings((prev) => ({ ...prev, alertThreshold: Number(e.target.value) }))}
            />
          </div>
          <div className="field-group">
            <label>차단 기준 실패 횟수</label>
            <input
              className="input"
              type="number"
              min="1"
              value={draftSettings.banThreshold}
              onChange={(e) => setDraftSettings((prev) => ({ ...prev, banThreshold: Number(e.target.value) }))}
            />
          </div>
          <div className="field-group">
            <label>집계 시간(초)</label>
            <input
              className="input"
              type="number"
              min="60"
              value={draftSettings.windowSeconds}
              onChange={(e) => setDraftSettings((prev) => ({ ...prev, windowSeconds: Number(e.target.value) }))}
            />
          </div>
          <div className="button-group settings-actions">
            <button className="primary-btn" type="button" onClick={handleSaveSettings} disabled={!isSettingsDirty}>
              설정 저장
            </button>
            <button className="ghost-btn" type="button" onClick={loadData}>
              새로고침
            </button>
          </div>
        </div>

        <div className="panel settings-panel">
          <h3 className="panel-title">전일 요약 준비 상태</h3>
          <p className="muted-text">{dailySummary.enabled ? "현재 전일 요약 자동 발송이 활성화되어 있습니다. 매일 지정 시각 기준으로 슬랙 요약 알림을 보냅니다." : "현재 전일 요약 자동 발송은 비활성입니다. 알람 탭에서 슬랙 알림을 활성화하면 매일 지정 시각 기준으로 슬랙 요약 알림을 보냅니다."}</p>
          <div className="detail-list">
            <div className="detail-row"><span>발송 기능</span><strong>{dailySummary.enabled ? "활성" : "비활성"}</strong></div>
            <div className="detail-row"><span>예정 시각</span><strong>매일 {dailySummary.scheduleHour ?? 8}:00</strong></div>
            <div className="detail-row"><span>기준 시간대</span><strong>{dailySummary.timezone || "Asia/Seoul"}</strong></div>
            <div className="detail-row"><span>후보 기준</span><strong>추가 시도 {dailySummary.minOverflowFailures ?? 10}회 이상</strong></div>
            <div className="detail-row"><span>마지막 발송 일자</span><strong>{dailySummary.lastSentDate || "-"}</strong></div>
          </div>
        </div>
      </div>
      {renderRuntimeGuardPanel()}
    </div>
  );

  const renderBackupTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="panel settings-panel">
        <div className="detail-header">
          <h3 className="panel-title">백업 설정</h3>
          <button className="ghost-btn compact-btn" type="button" onClick={() => setShowLogsModal(true)}>
            결과 로그 보기
          </button>
        </div>
        <p className="muted-text">관리자 영역에서 FTP 대상과 백업 주기를 관리합니다.</p>

        {backupLoading ? (
          <p className="muted-text">백업 설정을 불러오는 중입니다...</p>
        ) : (
          <>
            <div className="field-group">
              <label>FTP 서버 IP</label>
              <input className="input" value={backupForm.ftpHost} onChange={(e) => updateBackupField("ftpHost", e.target.value)} placeholder="예: 10.0.0.50" />
            </div>
            <div className="two-col-grid">
              <div className="field-group">
                <label>계정</label>
                <input className="input" value={backupForm.ftpUsername} onChange={(e) => updateBackupField("ftpUsername", e.target.value)} placeholder="FTP 계정" />
              </div>
              <div className="field-group">
                <label>패스워드</label>
                {renderPasswordField()}
              </div>
            </div>
            <div className="field-group">
              <label>업로드 경로</label>
              <input className="input" value={backupForm.ftpRemotePath} onChange={(e) => updateBackupField("ftpRemotePath", e.target.value)} placeholder="예: /certsvc/backups" />
            </div>
            <div className="backup-schedule-grid">
              <div className="field-group">
                <label>백업 주기</label>
                <select className="select" value={backupForm.scheduleType} onChange={(e) => updateBackupField("scheduleType", e.target.value)}>
                  <option value="manual">수동</option>
                  <option value="daily">매일</option>
                  <option value="weekly">매주</option>
                  <option value="monthly">매달</option>
                </select>
              </div>
              <div className="field-group">
                <label>백업 시간</label>
                <input className="input" type="time" value={backupForm.scheduleTime || ""} onChange={(e) => updateBackupField("scheduleTime", e.target.value)} disabled={backupForm.scheduleType === "manual"} />
              </div>
              {backupForm.scheduleType === "weekly" && (
                <div className="field-group">
                  <label>실행 요일</label>
                  <select className="select" value={backupForm.scheduleWeekday ?? 0} onChange={(e) => updateBackupField("scheduleWeekday", Number(e.target.value))}>
                    {WEEKDAY_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </div>
              )}
              {backupForm.scheduleType === "monthly" && (
                <div className="field-group">
                  <label>실행 일자</label>
                  <input className="input" type="number" min="1" max="31" value={backupForm.scheduleMonthday ?? 1} onChange={(e) => updateBackupField("scheduleMonthday", Number(e.target.value))} />
                </div>
              )}
            </div>
            {backupError && <p className="error-text">{backupError}</p>}
            {backupSuccess && <p className="success-text">{backupSuccess}</p>}
            {backupDirty && <p className="muted-text">백업 설정 변경사항이 있습니다. 저장 후 주기 실행과 수동 실행에 반영됩니다.</p>}
            <div className="button-group settings-actions">
              <button className="ghost-btn" type="button" onClick={testBackup} disabled={backupTesting || backupSaving}>{backupTesting ? "확인 중..." : "테스트 연결"}</button>
              <button className="ghost-btn" type="button" onClick={runBackup} disabled={backupRunning || backupSaving}>{backupRunning ? "백업 중..." : "지금 백업 실행"}</button>
              <button className="primary-btn" type="button" onClick={saveBackup} disabled={backupSaving}>{backupSaving ? "저장 중..." : "저장"}</button>
            </div>
          </>
        )}
      </div>

      <div className="panel settings-panel">
        <h3 className="panel-title">복구 실행</h3>
        <p className="muted-text">FTP 정보를 직접 입력해 백업 목록을 조회합니다. 기본은 검증모드이며, 복구모드를 선택하면 다운로드 후 실제 복구까지 수행합니다.</p>
        <div className="field-group">
          <label>FTP 서버 IP</label>
          <input className="input" value={restoreForm.ftpHost} onChange={(e) => updateRestoreField("ftpHost", e.target.value)} placeholder="예: 10.0.0.50" />
        </div>
        <div className="two-col-grid">
          <div className="field-group">
            <label>계정</label>
            <input className="input" value={restoreForm.ftpUsername} onChange={(e) => updateRestoreField("ftpUsername", e.target.value)} placeholder="FTP 계정" />
          </div>
          <div className="field-group">
            <label>패스워드</label>
            <input className="input" type="password" value={restoreForm.ftpPassword} onChange={(e) => updateRestoreField("ftpPassword", e.target.value)} placeholder="FTP 패스워드" />
          </div>
        </div>
        <div className="two-col-grid">
          <div className="field-group">
            <label>백업 경로</label>
            <input className="input" value={restoreForm.ftpRemotePath} onChange={(e) => updateRestoreField("ftpRemotePath", e.target.value)} placeholder="예: /certsvc/backups" />
          </div>
          <div className="field-group">
            <label>실행 모드</label>
            <select className="select" value={restoreForm.mode} onChange={(e) => updateRestoreField("mode", e.target.value)}>
              <option value="validate">검증모드</option>
              <option value="restore">복구모드</option>
            </select>
          </div>
        </div>
        <div className="two-col-grid">
          <div className="field-group">
            <label>백업 목록</label>
            <select className="select" value={restoreForm.backupId} onChange={(e) => updateRestoreField("backupId", e.target.value)} disabled={restoreBackups.length === 0}>
              <option value="">백업을 선택하세요</option>
              {restoreBackups.map((backupId) => (
                <option key={backupId} value={backupId}>{backupId}</option>
              ))}
            </select>
          </div>
          <div className="field-group">
            <label>안내</label>
            <div className="password-mask-box">
              {restoreForm.mode === "validate" ? "선택한 백업을 다운로드하고 검증까지만 수행합니다." : "다운로드 후 검증을 통과하면 실제 복구를 수행합니다."}
            </div>
          </div>
        </div>
        {restoreError && <p className="error-text">{restoreError}</p>}
        {restoreSuccess && <p className="success-text">{restoreSuccess}</p>}
        <div className="button-group settings-actions">
          <button className="ghost-btn" type="button" onClick={loadRestoreBackups} disabled={restoreListing || restoreRunning}>{restoreListing ? "조회 중..." : "백업 목록 조회"}</button>
          <button className={restoreForm.mode === "validate" ? "ghost-btn" : "primary-btn"} type="button" onClick={runRestore} disabled={restoreRunning || !restoreForm.backupId}>
            {restoreRunning ? (restoreForm.mode === "validate" ? "검증 중..." : "복구 중...") : (restoreForm.mode === "validate" ? "검증 실행" : "복구 실행")}
          </button>
        </div>
      </div>
      {renderRestoreResultPanel()}
    </div>
  );

  const renderAlarmTab = () => (
    <div className="page-grid page-grid-tight">
      <div className="panel settings-panel">
        <h3 className="panel-title">알람 설정</h3>
        <p className="muted-text">슬랙 오픈 채널 `#01-alert` 기준으로 운영 알림을 보냅니다. Bot Token은 마스킹 상태로 유지됩니다.</p>
        <div className="two-col-grid">
          <div className="field-group">
            <label>알림 사용</label>
            <select className="select" value={slackForm.enabled ? "enabled" : "disabled"} onChange={(e) => updateSlackField("enabled", e.target.value === "enabled")}>
              <option value="enabled">활성</option>
              <option value="disabled">비활성</option>
            </select>
          </div>
          <div className="field-group">
            <label>채널</label>
            <input className="input" value={slackForm.channel} onChange={(e) => updateSlackField("channel", e.target.value)} placeholder="#01-alert" />
          </div>
        </div>
        <div className="field-group">
          <label>Bot Token</label>
          {renderSlackTokenField()}
        </div>
        <div className="backup-schedule-grid">
          <div className="field-group">
            <label>CPU 임계치(%)</label>
            <input className="input" type="number" min="1" max="100" value={slackForm.cpuThreshold} onChange={(e) => updateSlackField("cpuThreshold", Number(e.target.value))} />
          </div>
          <div className="field-group">
            <label>메모리 임계치(%)</label>
            <input className="input" type="number" min="1" max="100" value={slackForm.memoryThreshold} onChange={(e) => updateSlackField("memoryThreshold", Number(e.target.value))} />
          </div>
          <div className="field-group">
            <label>디스크 임계치(%)</label>
            <input className="input" type="number" min="1" max="100" value={slackForm.diskThreshold} onChange={(e) => updateSlackField("diskThreshold", Number(e.target.value))} />
          </div>
        </div>
        <div className="check-grid top-gap">
          <label className="check-item"><input type="checkbox" checked={slackForm.notifyCertificateExpiry} onChange={(e) => updateSlackField("notifyCertificateExpiry", e.target.checked)} />만료 알림</label>
          <label className="check-item"><input type="checkbox" checked={slackForm.notifyBackupCompleted} onChange={(e) => updateSlackField("notifyBackupCompleted", e.target.checked)} />백업 완료</label>
          <label className="check-item"><input type="checkbox" checked={slackForm.notifySecurityAlert} onChange={(e) => updateSlackField("notifySecurityAlert", e.target.checked)} />침입 시도 감지</label>
          <label className="check-item"><input type="checkbox" checked={slackForm.notifyServiceDown} onChange={(e) => updateSlackField("notifyServiceDown", e.target.checked)} />서비스 다운</label>
          <label className="check-item"><input type="checkbox" checked={slackForm.notifyResourceThreshold} onChange={(e) => updateSlackField("notifyResourceThreshold", e.target.checked)} />자원 임계치 초과</label>
        </div>
        <div className="two-col-grid top-gap">
          <div className="field-group">
            <label>테스트 템플릿</label>
            <select className="select" value={slackTemplateType} onChange={(e) => setSlackTemplateType(e.target.value)}>
              {SLACK_TEMPLATE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div className="field-group">
            <label>안내</label>
            <div className="readonly-note">임의 데이터를 넣은 샘플 메시지를 `#01-alert`로 보냅니다.</div>
          </div>
        </div>
        {slackError && <p className="error-text">{slackError}</p>}
        {slackSuccess && <p className="success-text">{slackSuccess}</p>}
        {slackDirty && <p className="muted-text">슬랙 설정 변경사항이 있습니다. 저장 후 테스트 메시지를 보내는 흐름을 추천합니다.</p>}
        <div className="button-group settings-actions">
          <button className="ghost-btn" type="button" onClick={testSlack} disabled={slackTesting || slackSaving}>{slackTesting ? "전송 중..." : "테스트 메시지 전송"}</button>
          <button className="primary-btn" type="button" onClick={saveSlack} disabled={slackSaving}>{slackSaving ? "저장 중..." : "슬랙 설정 저장"}</button>
        </div>
      </div>
      {renderAlertHistoryPanel()}
    </div>
  );

  const renderTabContent = () => {
    switch (activeTab) {
      case "review": return renderReviewTab();
      case "unenroll": return renderUnenrollTab();
      case "analysis": return renderAnalysisTab();
      case "settings": return renderMonitorSettingsTab();
      case "backup": return renderBackupTab();
      case "alarm": return renderAlarmTab();
      case "overview":
      default: return renderOverviewTab();
    }
  };

  return (
    <div className="page-grid page-grid-tight">
      <div className="panel security-monitor-shell">
        <div className="detail-header">
          <div>
            <h3 className="panel-title">보안 모니터</h3>
            <p className="muted-text">숨김 진입 페이지입니다. 목적별 탭으로 구분해 수동 검토, 분석, 설정을 나눠서 볼 수 있습니다.</p>
          </div>
          <div className="button-group"><button className="ghost-btn" type="button" onClick={loadData}>전체 새로고침</button></div>
        </div>
        {loading && <p className="muted-text">데이터를 불러오는 중입니다...</p>}
        {error && <p className="error-text">{error}</p>}
        <div className="tab-group security-tab-group">
          {TAB_ITEMS.map((tab) => <button key={tab.id} type="button" className={`tab-btn ${activeTab === tab.id ? "active" : ""}`} onClick={() => setActiveTab(tab.id)}>{tab.label}</button>)}
        </div>
      </div>
      {renderTabContent()}
      {showLogsModal && (
        <div className="modal-overlay" role="presentation" onClick={() => setShowLogsModal(false)}>
          <div className="modal-card logs-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="detail-header">
              <h3 className="panel-title">백업 결과 로그</h3>
              <div className="button-group">
                <button className="ghost-btn compact-btn" type="button" onClick={async () => await reloadBackupLogs()}>
                  새로고침
                </button>
                <button className="ghost-btn compact-btn" type="button" onClick={() => setShowLogsModal(false)}>
                  닫기
                </button>
              </div>
            </div>
            <div className="logs-shell">
              {filteredBackupLogs.length === 0 ? (
                <p className="muted-text">아직 백업 관련 로그가 없습니다.</p>
              ) : (
                filteredBackupLogs.map((log) => (
                  <div className="log-card" key={log.id}>
                    <div className="detail-row">
                      <strong>{log.message}</strong>
                      <span className={`status-chip status-${log.status}`}>{log.status}</span>
                    </div>
                    <div className="detail-list compact-detail-list">
                      <div className="detail-row"><span>유형</span><strong>{log.jobType}</strong></div>
                      <div className="detail-row"><span>실행 시각</span><strong>{formatLogTime(log.ts)}</strong></div>
                      <div className="detail-row"><span>트리거</span><strong>{log.trigger || "manual"}</strong></div>
                    </div>
                    <div className="field-group top-gap">
                      <label>상세 / 오류 메시지</label>
                      <pre className="log-detail-pre">{log.detail || "상세 메시지 없음"}</pre>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
      {showAlertLogsModal && (
        <div className="modal-overlay" role="presentation" onClick={() => setShowAlertLogsModal(false)}>
          <div className="modal-card logs-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="detail-header">
              <div>
                <h3 className="panel-title">알람 이력 로그</h3>
                <p className="muted-text">마지막 갱신: {alertHistoryLastUpdatedAt ? formatDateTimeSeoul(new Date(alertHistoryLastUpdatedAt)) : "-"}</p>
              </div>
              <div className="button-group">
                <button className="ghost-btn compact-btn" type="button" onClick={handleManualAlertRefresh} disabled={alertHistoryLoading || alertRefreshCooldownSec > 0}>
                  {alertHistoryLoading ? "불러오는 중..." : (alertRefreshCooldownSec > 0 ? `새로고침 (${alertRefreshCooldownSec}초)` : "새로고침")}
                </button>
                <button className="ghost-btn compact-btn" type="button" onClick={() => setShowAlertLogsModal(false)}>
                  닫기
                </button>
              </div>
            </div>
            <div className="logs-shell">
              {alertHistory.length === 0 ? (
                <p className="muted-text">표시할 알람 이력이 없습니다.</p>
              ) : (
                alertHistory.map((log) => (
                  <div className="log-card" key={log.id}>
                    <div className="detail-row">
                      <strong>{log.message}</strong>
                      <span className={`status-chip status-${log.status === "failed" ? "failed" : "success"}`}>{log.status}</span>
                    </div>
                    <div className="detail-list compact-detail-list">
                      <div className="detail-row"><span>유형</span><strong>{log.jobType}</strong></div>
                      <div className="detail-row"><span>실행 시각</span><strong>{formatLogTime(log.ts)}</strong></div>
                    </div>
                    <div className="field-group top-gap">
                      <label>상세 / 오류 메시지</label>
                      <pre className="log-detail-pre">{log.detail || "상세 메시지 없음"}</pre>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
