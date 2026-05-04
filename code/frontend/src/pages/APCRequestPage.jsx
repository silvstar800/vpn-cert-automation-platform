import { useEffect, useMemo, useState } from "react";
import { requestAdminApc } from "../api/client";

export default function APCRequestPage({ assets = [], leases = [] }) {
  const [hostname, setHostname] = useState("");
  const [serialSearch, setSerialSearch] = useState("");
  const [selectedSerial, setSelectedSerial] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const xgsAssets = useMemo(() => {
    return assets
      .filter((asset) => String(asset.deviceModel || "").toUpperCase().includes("XGS"))
      .sort((a, b) => String(a.serialNumber || "").localeCompare(String(b.serialNumber || "")));
  }, [assets]);

  const connectedHostnames = useMemo(() => {
    return new Set(
      (leases || [])
        .filter((lease) => Boolean(lease?.isActive) && String(lease?.assignedIp || "").trim())
        .map((lease) => String(lease?.identity || "").trim())
        .filter(Boolean)
    );
  }, [leases]);

  const selectableAssets = useMemo(() => {
    return xgsAssets.filter((asset) => {
      const hasLinkedClient = Boolean(asset?.clientId);
      const hostname = String(asset?.hostname || "").trim();
      const hasAssignedIpOnClient = hasLinkedClient && Boolean(hostname) && connectedHostnames.has(hostname);
      return !hasAssignedIpOnClient;
    });
  }, [xgsAssets, connectedHostnames]);

  const filteredAssets = useMemo(() => {
    const query = serialSearch.trim().toLowerCase();
    return selectableAssets.filter((asset) => {
      if (!query) return true;
      return [asset.serialNumber, asset.customerName, asset.deviceModel].join(" ").toLowerCase().includes(query);
    }).slice(0, 50);
  }, [serialSearch, selectableAssets]);

  const selectedAsset = useMemo(() => selectableAssets.find((asset) => asset.serialNumber === selectedSerial) || null, [selectedSerial, selectableAssets]);
  const canSubmit = hostname.trim() && selectedSerial && adminPassword.trim();

  useEffect(() => {
    if (!selectedSerial) return;
    const exists = selectableAssets.some((asset) => asset.serialNumber === selectedSerial);
    if (!exists) {
      setSelectedSerial("");
      setSerialSearch("");
    }
  }, [selectedSerial, selectableAssets]);

  const selectAsset = (asset) => {
    setSelectedSerial(asset.serialNumber);
    setSerialSearch(asset.serialNumber);
    setDropdownOpen(false);
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!canSubmit || loading) return;
    setLoading(true);
    setError("");
    try {
      const response = await requestAdminApc({
        hostname: hostname.trim(),
        serialNumber: selectedAsset?.serialNumber || "",
        deviceModel: selectedAsset?.deviceModel || "",
        adminPassword: adminPassword.trim(),
      });
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const filenameMatch = disposition.match(/filename="([^"]+)"/);
      const filename = filenameMatch ? filenameMatch[1] : `${hostname.trim()}.apc`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      setResult({
        hostname: hostname.trim(),
        serialNumber: selectedAsset?.serialNumber || "-",
        deviceModel: selectedAsset?.deviceModel || "-",
        filename,
      });
    } catch (requestError) {
      setError(requestError.message || "APC 생성에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-grid">
      <div className="panel">
        <h3 className="panel-title">APC 요청 양식</h3>
        <form className="form-grid" onSubmit={handleSubmit}>
          <div className="field-group">
            <label>호스트명</label>
            <input
              className="input"
              value={hostname}
              onChange={(event) => setHostname(event.target.value)}
              placeholder="예: sfos-busan-01"
            />
          </div>

          <div className="field-group apc-select-shell">
            <label>시리얼 넘버</label>
            <button
              className={`select-trigger ${dropdownOpen ? "open" : ""}`}
              type="button"
              onClick={() => setDropdownOpen((prev) => !prev)}
            >
              <span>{selectedSerial || "시리얼을 검색해서 선택하세요"}</span>
              <span className="select-caret">▼</span>
            </button>
            {dropdownOpen ? (
              <div className="apc-dropdown">
                <input
                  className="input apc-dropdown-search"
                  value={serialSearch}
                  onChange={(event) => setSerialSearch(event.target.value)}
                  placeholder="시리얼 / 고객사명 / 모델 검색"
                />
                <div className="apc-dropdown-list">
                  {filteredAssets.map((asset) => (
                    <button
                      key={asset.serialNumber}
                      className={`apc-dropdown-item ${selectedSerial === asset.serialNumber ? "selected" : ""}`}
                      type="button"
                      onClick={() => selectAsset(asset)}
                    >
                      <strong>{asset.serialNumber}</strong>
                      <span>{asset.customerName || "외부 연동 대기"} / {asset.deviceModel || "-"}</span>
                    </button>
                  ))}
                  {!filteredAssets.length ? <div className="apc-dropdown-empty">연결 가능한 XGS 시리얼이 없습니다.</div> : null}
                </div>
              </div>
            ) : null}
          </div>

          <div className="field-group">
            <label>장비명/모델</label>
            <div className="readonly-display">{selectedAsset?.deviceModel || "시리얼 선택 시 자동 표시"}</div>
          </div>

          <div className="field-group">
            <label>SFOS admin 비밀번호</label>
            <input
              className="input"
              type="password"
              value={adminPassword}
              onChange={(event) => setAdminPassword(event.target.value)}
              placeholder="APC 적용에 사용할 관리자 비밀번호"
              autoComplete="new-password"
            />
          </div>

          {error ? <div className="error-text">{error}</div> : null}

          <div className="button-group">
            <button className="primary-btn" type="submit" disabled={!canSubmit || loading}>
              {loading ? "생성 중..." : "APC 생성"}
            </button>
            <button
              className="ghost-btn"
              type="button"
              onClick={() => {
                setHostname("");
                setSerialSearch("");
                setSelectedSerial("");
                setAdminPassword("");
                setDropdownOpen(false);
                setResult(null);
                setError("");
              }}
            >
              초기화
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <h3 className="panel-title">결과</h3>
        {!result ? (
          <p className="muted-text">아직 요청 기록이 없습니다. 입력 후 APC를 생성해 주세요.</p>
        ) : (
          <div className="detail-list">
            <div className="detail-row"><span>호스트명</span><strong>{result.hostname}</strong></div>
            <div className="detail-row"><span>시리얼</span><strong>{result.serialNumber}</strong></div>
            <div className="detail-row"><span>장비 모델</span><strong>{result.deviceModel}</strong></div>
            <div className="detail-row"><span>다운로드 파일</span><strong>{result.filename}</strong></div>
          </div>
        )}
      </div>
    </div>
  );
}
