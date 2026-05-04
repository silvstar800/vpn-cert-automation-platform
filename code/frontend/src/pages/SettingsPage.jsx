import { useEffect, useState } from "react";

const REFRESH_OPTIONS = [10, 30, 60];

export default function SettingsPage({ refreshIntervalSec = 10, onSaveRefreshInterval }) {
  const [draftInterval, setDraftInterval] = useState(String(refreshIntervalSec));
  const [savedMessage, setSavedMessage] = useState("");

  useEffect(() => {
    setDraftInterval(String(refreshIntervalSec));
  }, [refreshIntervalSec]);

  const handleSave = () => {
    const nextValue = Number(draftInterval);
    onSaveRefreshInterval?.(nextValue);
    setSavedMessage(`자동 새로고침 주기를 ${nextValue}초로 저장했습니다.`);
    window.setTimeout(() => setSavedMessage(""), 2000);
  };

  return (
    <div className="page-grid">
      <div className="two-col-grid">
        <div className="panel">
          <h3 className="panel-title">표시 설정</h3>
          <div className="form-grid">
            <div className="field-group">
              <label>자동 새로고침 주기</label>
              <select className="select" value={draftInterval} onChange={(event) => setDraftInterval(event.target.value)}>
                {REFRESH_OPTIONS.map((seconds) => (
                  <option key={seconds} value={seconds}>
                    {seconds}초
                  </option>
                ))}
              </select>
            </div>

            <div className="detail-list">
              <div className="detail-row"><span>현재 적용 주기</span><strong>{refreshIntervalSec}초</strong></div>
              <div className="detail-row"><span>현재 테마</span><strong>다크</strong></div>
              <div className="detail-row"><span>시간대</span><strong>Asia/Seoul</strong></div>
            </div>

            {savedMessage ? <p className="success-text">{savedMessage}</p> : null}

            <div className="button-group">
              <button className="primary-btn" type="button" onClick={handleSave}>
                저장
              </button>
            </div>
          </div>
        </div>

        <div className="panel">
          <h3 className="panel-title">운영 안내</h3>
          <div className="detail-list">
            <div className="detail-row"><span>웹 로그인</span><strong>세션 기반 보호</strong></div>
            <div className="detail-row"><span>보안 모니터</span><strong>Debug Mode 비밀번호 필요</strong></div>
            <div className="detail-row"><span>백업/알람</span><strong>보안 모니터 탭에서 관리</strong></div>
          </div>
        </div>
      </div>
    </div>
  );
}
