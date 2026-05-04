const TITLES = {
  dashboard: "대시보드",
  clients: "클라이언트",
  "client-detail": "클라이언트 상세",
  assets: "장비/라이선스",
  expire: "인증서 만료",
  iplease: "IP 임대",
  system: "시스템 상태",
  apc: "APC 요청",
  settings: "설정",
  security: "보안 모니터",
};

export default function Header({ activePage }) {
  return (
    <header className="page-header">
      <div>
        <div className="page-eyebrow">운영 관리 센터</div>
        <h2>{TITLES[activePage] || "SSL VPN 인증서 관리"}</h2>
      </div>
      <div className="page-header-brand">
        <img src="/header-logo.png" alt="Elim.net" className="page-header-brand-image" />
      </div>
    </header>
  );
}
