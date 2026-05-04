import Sidebar from "./Sidebar";
import Header from "./Header";

export default function Layout({ children, activePage, setActivePage, username, onLogout, securityUnlocked }) {
  return (
    <div className="app-layout">
      <Sidebar
        activePage={activePage}
        setActivePage={setActivePage}
        username={username}
        onLogout={onLogout}
        securityUnlocked={securityUnlocked}
      />
      <main className="main-content">
        <Header activePage={activePage} />
        <section className="content-area">{children}</section>
      </main>
    </div>
  );
}
