import { useEffect, useMemo, useState } from "react";
import { formatDateSeoul, formatDateTimeSeoul } from "../utils/time";

const STORAGE_KEYS = {
  search: "certsvc.assets.search",
  assetStatus: "certsvc.assets.assetStatus",
  licenseOption: "certsvc.assets.licenseOption",
  deviceModel: "certsvc.assets.deviceModel",
  selectedSerial: "certsvc.assets.selectedSerial",
};

const ASSET_STATUS_CLASS = {
  임대: "active",
  미회수: "warning",
  "재고(New)": "safe",
  "재고(Old)": "inactive",
  판매: "normal",
  폐기: "stopped",
  RMA대상: "critical",
};

function getStoredValue(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

function daysUntil(dateValue) {
  if (!dateValue) return Number.MAX_SAFE_INTEGER;
  const target = new Date(dateValue).getTime();
  const now = Date.now();
  return Math.ceil((target - now) / (1000 * 60 * 60 * 24));
}

function matchesLicenseOption(asset, option) {
  if (option === "all") return true;
  if (option === "base") return Number(asset.licenseFlags || 0) === 0;
  return Array.isArray(asset.licenseLabels) && asset.licenseLabels.includes(option);
}

function AssetStatusBadge({ label }) {
  const statusClass = ASSET_STATUS_CLASS[label] || "normal";
  return <span className={`status-badge ${statusClass}`}>{label || "-"}</span>;
}

export default function AssetsPage({ assets = [] }) {
  const [search, setSearch] = useState(() => getStoredValue(STORAGE_KEYS.search, ""));
  const [assetStatus, setAssetStatus] = useState(() => getStoredValue(STORAGE_KEYS.assetStatus, "all"));
  const [licenseOption, setLicenseOption] = useState(() => getStoredValue(STORAGE_KEYS.licenseOption, "all"));
  const [deviceModel, setDeviceModel] = useState(() => getStoredValue(STORAGE_KEYS.deviceModel, "all"));
  const [selectedSerial, setSelectedSerial] = useState(() => getStoredValue(STORAGE_KEYS.selectedSerial, ""));

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEYS.search, search);
      localStorage.setItem(STORAGE_KEYS.assetStatus, assetStatus);
      localStorage.setItem(STORAGE_KEYS.licenseOption, licenseOption);
      localStorage.setItem(STORAGE_KEYS.deviceModel, deviceModel);
      localStorage.setItem(STORAGE_KEYS.selectedSerial, selectedSerial);
    } catch {
      // ignore storage failures
    }
  }, [search, assetStatus, licenseOption, deviceModel, selectedSerial]);

  const deviceModelOptions = useMemo(() => {
    return [...new Set(assets.map((asset) => asset.deviceModel).filter(Boolean))].sort((a, b) =>
      a.localeCompare(b, "ko"),
    );
  }, [assets]);

  const filteredAssets = useMemo(() => {
    return [...assets]
      .filter((asset) => {
        const text = [asset.serialNumber, asset.customerName, asset.deviceModel].join(" ").toLowerCase();
        const matchSearch = !search || text.includes(search.toLowerCase());
        const matchAssetStatus = assetStatus === "all" || asset.assetStatusLabel === assetStatus;
        const matchLicense = matchesLicenseOption(asset, licenseOption);
        const matchDeviceModel = deviceModel === "all" || asset.deviceModel === deviceModel;
        return matchSearch && matchAssetStatus && matchLicense && matchDeviceModel;
      })
      .sort((a, b) => {
        const customerCompare = String(a.customerName || "").localeCompare(String(b.customerName || ""), "ko");
        if (customerCompare !== 0) return customerCompare;
        return daysUntil(a.licenseEndDate) - daysUntil(b.licenseEndDate);
      });
  }, [assets, search, assetStatus, licenseOption, deviceModel]);

  useEffect(() => {
    if (!filteredAssets.length) {
      setSelectedSerial("");
      return;
    }

    const exists = filteredAssets.some((asset) => asset.serialNumber === selectedSerial);
    if (!selectedSerial || !exists) {
      setSelectedSerial(filteredAssets[0].serialNumber);
    }
  }, [filteredAssets, selectedSerial]);

  const selectedAsset =
    filteredAssets.find((asset) => asset.serialNumber === selectedSerial) ||
    assets.find((asset) => asset.serialNumber === selectedSerial) ||
    filteredAssets[0] ||
    null;

  const stats = useMemo(() => {
    const expiringSoon = filteredAssets.filter((asset) => daysUntil(asset.licenseEndDate) <= 60).length;
    const syncedCustomers = filteredAssets.filter((asset) => asset.customerSyncStatus === 1).length;
    const selectedModelCount =
      deviceModel === "all" ? 0 : assets.filter((asset) => asset.deviceModel === deviceModel).length;
    return {
      total: assets.length,
      expiringSoon,
      syncedCustomers,
      selectedModelCount,
    };
  }, [assets, filteredAssets, deviceModel]);

  return (
    <div className="page-grid">
      <div className="stats-grid compact-stats assets-stats-grid">
        <div className="stat-card">
          <div className="stat-label">관리 장비</div>
          <div className="stat-value">{stats.total}</div>
          <div className="stat-sub">시리얼 기준 자산 수</div>
          {deviceModel !== "all" ? (
            <div className="asset-model-summary">
              <div className="asset-model-summary-divider" />
              <div className="asset-model-summary-row">
                <span>{deviceModel}</span>
                <strong>{stats.selectedModelCount}대</strong>
              </div>
            </div>
          ) : null}
        </div>
        <div className="stat-card">
          <div className="stat-label">60일 이내 만료</div>
          <div className="stat-value">{stats.expiringSoon}</div>
          <div className="stat-sub">사전 갱신 확인 필요</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">고객사 연동 완료</div>
          <div className="stat-value">{stats.syncedCustomers}</div>
          <div className="stat-sub">외부 서비스 JSON 반영 기준</div>
        </div>
      </div>

      <div className="panel toolbar-panel toolbar-panel-inline">
        <input
          className="input"
          placeholder="시리얼, 고객사명, 장비명·모델 검색"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />

        <select className="select" value={deviceModel} onChange={(event) => setDeviceModel(event.target.value)}>
          <option value="all">전체 장비 모델</option>
          {deviceModelOptions.map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </select>

        <select className="select" value={assetStatus} onChange={(event) => setAssetStatus(event.target.value)}>
          <option value="all">전체 장비 상태</option>
          <option value="임대">임대</option>
          <option value="미회수">미회수</option>
          <option value="재고(New)">재고(New)</option>
          <option value="재고(Old)">재고(Old)</option>
          <option value="판매">판매</option>
          <option value="폐기">폐기</option>
          <option value="RMA대상">RMA대상</option>
        </select>

        <select className="select" value={licenseOption} onChange={(event) => setLicenseOption(event.target.value)}>
          <option value="all">전체 라이선스</option>
          <option value="base">기본만</option>
          <option value="Enhanced">Enhanced 포함</option>
          <option value="NP">NP 포함</option>
          <option value="WP">WP 포함</option>
        </select>
      </div>

      <div className="two-col-grid assets-layout">
        <div className="panel">
          <div className="panel-header-row">
            <div>
              <h3 className="panel-title">장비/라이선스 목록</h3>
              <p className="panel-description">
                시리얼 기준으로 고객사명, 장비 모델, 라이선스와 운영 상태를 함께 추적합니다.
              </p>
            </div>
          </div>

          <table className="data-table">
            <thead>
              <tr>
                <th>고객사명</th>
                <th>시리얼</th>
                <th>장비명/모델</th>
                <th>라이선스 종류</th>
                <th>라이선스 만료</th>
                <th>장비 상태</th>
              </tr>
            </thead>
            <tbody>
              {filteredAssets.map((asset) => (
                <tr
                  key={asset.serialNumber}
                  className={selectedAsset?.serialNumber === asset.serialNumber ? "selected-row" : ""}
                  onClick={() => setSelectedSerial(asset.serialNumber)}
                >
                  <td>
                    <div className="row-title">{asset.customerName || "외부 연동 대기"}</div>
                    <div className="row-sub">{asset.customerSyncLabel || "외부 연동 대기"}</div>
                  </td>
                  <td>
                    <div className="row-title">{asset.serialNumber}</div>
                    <div className="row-sub">최근 수정 {formatDateTimeSeoul(asset.updatedAt)}</div>
                  </td>
                  <td>{asset.deviceModel || "-"}</td>
                  <td>{(asset.licenseLabels || ["기본"]).join(", ")}</td>
                  <td>{asset.licenseEndDate ? formatDateSeoul(asset.licenseEndDate) : "-"}</td>
                  <td>
                    <AssetStatusBadge label={asset.assetStatusLabel} />
                  </td>
                </tr>
              ))}
              {!filteredAssets.length ? (
                <tr className="placeholder-row">
                  <td colSpan={6}>조건에 맞는 장비가 없습니다.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div className="page-grid">
          {selectedAsset ? (
            <>
              <div className="panel">
                <div className="panel-header-row">
                  <div>
                    <h3 className="panel-title">장비 상세</h3>
                    <p className="panel-description">
                      enroll, APC, 외부 고객사 연동으로 들어온 정보를 시리얼 기준으로 관리합니다.
                    </p>
                  </div>
                </div>

                <div className="detail-list">
                  <div className="detail-row"><span>시리얼</span><strong>{selectedAsset.serialNumber}</strong></div>
                  <div className="detail-row"><span>고객사명</span><strong>{selectedAsset.customerName || "외부 연동 대기"}</strong></div>
                  <div className="detail-row"><span>장비명/모델</span><strong>{selectedAsset.deviceModel || "-"}</strong></div>
                  <div className="detail-row"><span>라이선스 종류</span><strong>{(selectedAsset.licenseLabels || ["기본"]).join(", ")}</strong></div>
                  <div className="detail-row"><span>라이선스 기간</span><strong>{selectedAsset.licenseStartDate ? formatDateSeoul(selectedAsset.licenseStartDate) : "-"} - {selectedAsset.licenseEndDate ? formatDateSeoul(selectedAsset.licenseEndDate) : "-"}</strong></div>
                  <div className="detail-row"><span>장비 상태</span><strong>{selectedAsset.assetStatusLabel || "-"}</strong></div>
                  <div className="detail-row"><span>고객사명 반영 방식</span><strong>{selectedAsset.customerSyncLabel || "외부 연동 대기"}</strong></div>
                </div>
              </div>

              <div className="panel">
                <div className="panel-header-row">
                  <div>
                    <h3 className="panel-title">히스토리</h3>
                    <p className="panel-description">
                      장비 등록, enroll, APC, 고객사 자동 연동 이력을 시간순으로 확인합니다.
                    </p>
                  </div>
                </div>

                <div className="asset-history-list">
                  {(selectedAsset.history || []).length ? (
                    selectedAsset.history.map((item) => (
                      <div className="asset-history-item" key={item.id}>
                        <div className="asset-history-meta">
                          <strong>{item.summary || "장비 이력"}</strong>
                          <span>{formatDateTimeSeoul(item.createdAt)}</span>
                        </div>
                        <div className="asset-history-type">{item.eventLabel || "history"}</div>
                        <p>{item.detail || "-"}</p>
                      </div>
                    ))
                  ) : (
                    <div className="asset-history-item">
                      <div className="asset-history-meta">
                        <strong>히스토리가 아직 없습니다.</strong>
                      </div>
                      <p>장비 등록, enroll, APC 요청, 고객사명 연동이 시작되면 여기에 누적됩니다.</p>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="panel">
              <h3 className="panel-title">장비 상세</h3>
              <p className="muted-text">장비 데이터를 아직 불러오지 못했습니다.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
