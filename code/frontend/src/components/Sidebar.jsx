const menuItems = [
  { id: "dashboard", label: "대시보드" },
  { id: "clients", label: "클라이언트" },
  { id: "assets", label: "장비/라이선스" },
  { id: "expire", label: "인증서 만료" },
  { id: "iplease", label: "IP 임대" },
  { id: "system", label: "시스템 상태" },
  { id: "apc", label: "APC 요청" },
  { id: "settings", label: "설정" },
  { id: "security", label: "보안 모니터" },
];

export default function Sidebar({ activePage, setActivePage, username, onLogout, securityUnlocked = false }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="brand-badge">certsvc://console</div>
        <h1>SSL 인증서 관리</h1>
        <p>등록, 임대 추적, 인증서 수명주기를 실시간으로 관리합니다.</p>
      </div>

      <div className="account-chip-card">
        <div className="account-chip-user">{username || "admin"}</div>
        <button className="account-chip-action" type="button" onClick={onLogout}>
          로그아웃
        </button>
      </div>

      <nav className="sidebar-nav">
        {menuItems.map((item) => (
          <button
            key={item.id}
            className={`nav-btn ${activePage === item.id ? "active" : ""}`}
            onClick={() => setActivePage(item.id)}
          >
            <span>{item.label}</span>
            {item.id === "security" && securityUnlocked ? <span className="nav-pill">진입 가능</span> : null}
          </button>
        ))}
      </nav>
    </aside>
  );
}
