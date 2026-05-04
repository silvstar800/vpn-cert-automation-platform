import { useEffect, useState } from "react";
import { downloadClientBackup, getClientBackupInfo } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import { vpnLabel } from "../utils/vpnLabel";
import { formatDateSeoul, formatDateTimeSeoul } from "../utils/time";

function formatSize(sizeBytes) {
  if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = sizeBytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[unitIndex]}`;
}

function resolveClientHttpsPort(portStatus) {
  const normalized = Number(portStatus);
  if (normalized === 1) return 1044;
  if (normalized === 2) return 5443;
  return 4443;
}

function formatIpHostForUrl(ip) {
  const value = String(ip || "").trim();
  if (!value) return "";
  return value.includes(":") && !value.startsWith("[") ? `[${value}]` : value;
}

export default function ClientDetailPage({ client }) {
  const [backupInfo, setBackupInfo] = useState(null);
  const [backupLoading, setBackupLoading] = useState(false);
  const [backupError, setBackupError] = useState("");
  const [backupDownloading, setBackupDownloading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadBackupInfo() {
      if (!client?.id) {
        setBackupInfo(null);
        setBackupError("");
        setBackupLoading(false);
        return;
      }
      setBackupLoading(true);
      setBackupError("");
      try {
        const response = await getClientBackupInfo(client.id);
        if (!cancelled) {
          setBackupInfo(response || null);
        }
      } catch (error) {
        if (!cancelled) {
          setBackupInfo(null);
          setBackupError(error.message || "백업 정보를 불러오지 못했습니다.");
        }
      } finally {
        if (!cancelled) {
          setBackupLoading(false);
        }
      }
    }

    loadBackupInfo();
    return () => {
      cancelled = true;
    };
  }, [client?.id]);

  const handleDownloadBackup = async () => {
    if (!client?.id || backupDownloading) return;
    setBackupDownloading(true);
    setBackupError("");
    try {
      const response = await downloadClientBackup(client.id);
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const filenameMatch = disposition.match(/filename="([^"]+)"/);
      const defaultName = backupInfo?.backup?.filename || `${client.hostname}-backup.bin`;
      const filename = filenameMatch ? filenameMatch[1] : defaultName;

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setBackupError(error.message || "백업 파일 다운로드에 실패했습니다.");
    } finally {
      setBackupDownloading(false);
    }
  };

  if (!client) {
    return (
      <div className="panel">
        <h3 className="panel-title">클라이언트 상세</h3>
        <p className="muted-text">클라이언트를 선택하면 상세 정보가 표시됩니다.</p>
      </div>
    );
  }

  const backup = backupInfo?.backup;
  const assignedIp = String(client.assignedIp || "").trim();
  const httpsPort = resolveClientHttpsPort(client.httpsPortStatus);
  const assignedIpUrl = assignedIp ? `https://${formatIpHostForUrl(assignedIp)}:${httpsPort}` : "";

  return (
    <div className="page-grid">
      <div className="panel detail-header">
        <div>
          <div className="page-eyebrow">클라이언트 상세</div>
          <h3>{client.hostname}</h3>
          <p className="muted-text">{client.tenant || "-"} | {vpnLabel(client.vpnType, client.assignedIp)}</p>
        </div>
        <StatusBadge status={client.status} />
      </div>

      <div className="two-col-grid">
        <div className="panel">
          <h3 className="panel-title">기본 정보</h3>
          <div className="detail-list">
            <div className="detail-row"><span>호스트명</span><strong>{client.hostname}</strong></div>
            <div className="detail-row"><span>장비 시리얼</span><strong>{client.serialNumber || "-"}</strong></div>
            <div className="detail-row"><span>VPN 유형</span><strong>{vpnLabel(client.vpnType, client.assignedIp)}</strong></div>
            <div className="detail-row"><span>인증서 CN</span><strong>{client.certCn}</strong></div>
            <div className="detail-row"><span>할당 IP</span><strong>{assignedIp ? <a href={assignedIpUrl} target="_blank" rel="noopener noreferrer">{assignedIp}</a> : "-"}</strong></div>
            <div className="detail-row"><span>MAC</span><strong>{client.mac || "-"}</strong></div>
          </div>
        </div>

        <div className="panel">
          <h3 className="panel-title">인증서 정보</h3>
          <div className="detail-list">
            <div className="detail-row"><span>발급자</span><strong>{client.issuer || "-"}</strong></div>
            <div className="detail-row"><span>만료일</span><strong>{formatDateSeoul(client.expireAt)}</strong></div>
            <div className="detail-row"><span>등록 시점</span><strong>{formatDateTimeSeoul(client.registeredAt || client.lastSeenAt)}</strong></div>
            <div className="detail-row"><span>원격 IP</span><strong>{client.remoteIp || "-"}</strong></div>
          </div>
        </div>
      </div>

      <div className="two-col-grid">
        <div className="panel">
          <h3 className="panel-title">연결 요약</h3>
          <div className="detail-list">
            <div className="detail-row"><span>상태</span><strong>{client.status || "-"}</strong></div>
            <div className="detail-row"><span>VPN 그룹</span><strong>{vpnLabel(client.vpnType, client.assignedIp)}</strong></div>
            <div className="detail-row"><span>고객사</span><strong>{client.tenant || "-"}</strong></div>
          </div>
        </div>

        <div className="panel">
          <h3 className="panel-title">백업 정보</h3>
          {backupLoading ? <p className="muted-text">백업 정보를 불러오는 중입니다...</p> : null}
          {!backupLoading && backupError ? <p className="error-text">{backupError}</p> : null}
          {!backupLoading && !backupError && backup ? (
            <div className="detail-list">
              <div className="detail-row"><span>최신 백업 파일</span><strong>{backup.filename || "-"}</strong></div>
              <div className="detail-row"><span>백업 일자</span><strong>{formatDateTimeSeoul(backup.updatedAt || backup.updatedAtDisplay)}</strong></div>
              <div className="detail-row"><span>파일 크기</span><strong>{formatSize(Number(backup.sizeBytes || 0))}</strong></div>
              <div className="button-group">
                <button className="primary-btn" type="button" onClick={handleDownloadBackup} disabled={backupDownloading}>
                  {backupDownloading ? "다운로드 중..." : "백업 다운로드"}
                </button>
              </div>
            </div>
          ) : null}
          {!backupLoading && !backupError && !backup ? (
            <p className="muted-text">등록된 백업 파일이 없습니다.</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}