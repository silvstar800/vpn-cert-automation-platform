import { useCallback, useEffect, useMemo, useState } from "react";
import Layout from "./components/Layout";
import DashboardPage from "./pages/DashboardPage";
import ClientsPage from "./pages/ClientsPage";
import ClientDetailPage from "./pages/ClientDetailPage";
import AssetsPage from "./pages/AssetsPage";
import ExpirePage from "./pages/ExpirePage";
import IPLeasePage from "./pages/IPLeasePage";
import SystemStatusPage from "./pages/SystemStatusPage";
import APCRequestPage from "./pages/APCRequestPage";
import SettingsPage from "./pages/SettingsPage";
import SecurityMonitorPage from "./pages/SecurityMonitorPage";
import LoginPage from "./pages/LoginPage";
import {
  getAuthMe,
  getClients,
  getEquipmentAssets,
  getLeases,
  getSecurityConsoleAccess,
  getSystemStatus,
  logoutWeb,
  openSecurityConsole,
  setSecurityConsoleAccess,
} from "./api/client";

const DEFAULT_PAGE = "dashboard";
const REFRESH_INTERVAL_STORAGE_KEY = "certsvc.settings.refreshIntervalSec";
const VALID_PAGES = new Set([
  "dashboard",
  "clients",
  "assets",
  "expire",
  "iplease",
  "system",
  "apc",
  "settings",
  "security",
]);

function parseHashRoute(hash) {
  const normalized = String(hash || "").replace(/^#\/?/, "").trim();

  if (!normalized) {
    return { page: DEFAULT_PAGE, clientId: null };
  }

  if (normalized.startsWith("client/")) {
    const clientId = normalized.split("/")[1] || "";
    return { page: "client-detail", clientId };
  }

  if (VALID_PAGES.has(normalized)) {
    return { page: normalized, clientId: null };
  }

  return { page: DEFAULT_PAGE, clientId: null };
}

function buildHashRoute(page, client = null) {
  if (page === "client-detail" && client?.id) {
    return `#/client/${client.id}`;
  }
  return `#/${VALID_PAGES.has(page) ? page : DEFAULT_PAGE}`;
}

function SecurityUnlockPanel({ onUnlock, error }) {
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    if (!password.trim() || loading) return;
    setLoading(true);
    try {
      await onUnlock(password.trim());
      setPassword("");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="panel security-unlock-panel">
      <h3 className="panel-title">보안 모니터 잠금 해제</h3>
      <p className="muted-text">Debug Mode 관리자 비밀번호를 입력하면 보안 모니터, 백업, 알람 기능을 열 수 있습니다.</p>
      <form className="form-grid" onSubmit={submit}>
        <div className="field-group">
          <label>관리자 비밀번호</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Debug Mode 비밀번호"
          />
        </div>
        {error ? <div className="error-text">{error}</div> : null}
        <div className="button-group">
          <button className="primary-btn" type="submit" disabled={!password.trim() || loading}>
            {loading ? "확인 중..." : "보안 모니터 열기"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function App() {
  const [activePage, setActivePage] = useState(DEFAULT_PAGE);
  const [selectedClient, setSelectedClient] = useState(null);
  const [routeClientId, setRouteClientId] = useState(null);
  const [clients, setClients] = useState([]);
  const [assets, setAssets] = useState([]);
  const [leases, setLeases] = useState([]);
  const [services, setServices] = useState([]);
  const [resources, setResources] = useState(null);
  const [apiError, setApiError] = useState("");
  const [loading, setLoading] = useState(true);
  const [authLoading, setAuthLoading] = useState(true);
  const [authState, setAuthState] = useState({ authenticated: false, username: "" });
  const [securityUnlocked, setSecurityUnlocked] = useState(getSecurityConsoleAccess);
  const [securityUnlockError, setSecurityUnlockError] = useState("");
  const [refreshIntervalSec, setRefreshIntervalSec] = useState(() => {
    try {
      const stored = Number(window.localStorage.getItem(REFRESH_INTERVAL_STORAGE_KEY));
      return [10, 30, 60].includes(stored) ? stored : 10;
    } catch {
      return 10;
    }
  });

  useEffect(() => {
    async function loadAuth() {
      setAuthLoading(true);
      try {
        const auth = await getAuthMe();
        setAuthState({
          authenticated: Boolean(auth?.authenticated),
          username: auth?.username || "admin",
        });
      } catch {
        setAuthState({ authenticated: false, username: "" });
      } finally {
        setAuthLoading(false);
      }
    }

    loadAuth();
  }, []);

  useEffect(() => {
    const syncRouteState = () => {
      const { page, clientId } = parseHashRoute(window.location.hash);
      setActivePage(page);
      setRouteClientId(clientId);
      if (page !== "client-detail") {
        setSelectedClient(null);
      }
    };

    if (!window.location.hash) {
      window.history.replaceState(null, "", buildHashRoute(DEFAULT_PAGE));
    }

    syncRouteState();
    window.addEventListener("hashchange", syncRouteState);
    return () => window.removeEventListener("hashchange", syncRouteState);
  }, []);

  const loadAll = useCallback(async (showSpinner = true) => {
    if (!authState.authenticated) return;

    if (showSpinner) {
      setLoading(true);
    }

    setApiError("");
    const results = await Promise.allSettled([
      getClients(),
      getEquipmentAssets(),
      getLeases(),
      getSystemStatus(),
    ]);

    const [clientsRes, assetsRes, leasesRes, systemRes] = results;
    const errors = [];

    if (clientsRes.status === "fulfilled") {
      setClients(Array.isArray(clientsRes.value) ? clientsRes.value : []);
    } else {
      errors.push("클라이언트 정보를 불러오지 못했습니다.");
      setClients([]);
    }

    if (assetsRes.status === "fulfilled") {
      setAssets(Array.isArray(assetsRes.value) ? assetsRes.value : []);
    } else {
      errors.push("장비 정보를 불러오지 못했습니다.");
      setAssets([]);
    }

    if (leasesRes.status === "fulfilled") {
      setLeases(Array.isArray(leasesRes.value) ? leasesRes.value : []);
    } else {
      errors.push("IP 임대 정보를 불러오지 못했습니다.");
      setLeases([]);
    }

    if (systemRes.status === "fulfilled") {
      setServices(Array.isArray(systemRes.value?.services) ? systemRes.value.services : []);
      setResources(systemRes.value?.resources || null);
    } else {
      errors.push("시스템 상태를 불러오지 못했습니다.");
      setServices([]);
      setResources(null);
    }

    setApiError(errors.join(" "));
    if (showSpinner) {
      setLoading(false);
    }
  }, [authState.authenticated]);

  useEffect(() => {
    if (!authState.authenticated) {
      setClients([]);
      setAssets([]);
      setLeases([]);
      setServices([]);
      setResources(null);
      setLoading(false);
      return;
    }

    loadAll(true);
  }, [authState.authenticated, loadAll]);

  useEffect(() => {
    if (!authState.authenticated) return undefined;

    const timer = window.setInterval(() => {
      loadAll(false);
    }, refreshIntervalSec * 1000);

    return () => window.clearInterval(timer);
  }, [authState.authenticated, loadAll, refreshIntervalSec]);

  useEffect(() => {
    if (!securityUnlocked) return undefined;

    const timer = window.setInterval(() => {
      if (!getSecurityConsoleAccess()) {
        setSecurityUnlocked(false);
      }
    }, 30 * 1000);

    return () => window.clearInterval(timer);
  }, [securityUnlocked]);

  useEffect(() => {
    if (activePage !== "client-detail") {
      return;
    }

    if (!routeClientId) {
      setSelectedClient(null);
      return;
    }

    const matched = clients.find((client) => String(client.id) === String(routeClientId)) || null;
    setSelectedClient(matched);
  }, [activePage, routeClientId, clients]);

  const navigateToPage = (page) => {
    const nextHash = buildHashRoute(page);
    setActivePage(page);
    setRouteClientId(null);
    setSelectedClient(null);
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
  };

  const openClientDetail = (client) => {
    const nextHash = buildHashRoute("client-detail", client);
    setSelectedClient(client);
    setRouteClientId(client?.id ?? null);
    setActivePage("client-detail");
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
  };

  const handleLoginSuccess = (result) => {
    setAuthState({ authenticated: true, username: result?.username || "admin" });
  };

  const handleLogout = async () => {
    try {
      await logoutWeb();
    } catch {
      // ignore logout errors
    }
    setSecurityConsoleAccess(false);
    setSecurityUnlocked(false);
    setSecurityUnlockError("");
    setAuthState({ authenticated: false, username: "" });
    window.location.hash = buildHashRoute(DEFAULT_PAGE);
  };

  const handleSecurityUnlock = async (password) => {
    try {
      setSecurityUnlockError("");
      await openSecurityConsole(password);
      setSecurityUnlocked(true);
    } catch (error) {
      setSecurityUnlockError(error.message || "보안 모니터 잠금 해제에 실패했습니다.");
    }
  };

  const handleSaveRefreshInterval = (nextValue) => {
    setRefreshIntervalSec(nextValue);
    try {
      window.localStorage.setItem(REFRESH_INTERVAL_STORAGE_KEY, String(nextValue));
    } catch {
      // ignore storage failures
    }
  };

  const handleAssetsChanged = useCallback(async () => {
    await loadAll(false);
  }, [loadAll]);

  const renderedPage = useMemo(() => {
    switch (activePage) {
      case "dashboard":
        return <DashboardPage clients={clients} services={services} />;
      case "clients":
        return <ClientsPage clients={clients} onSelectClient={openClientDetail} />;
      case "client-detail":
        return <ClientDetailPage client={selectedClient} />;
      case "assets":
        return <AssetsPage assets={assets} onAssetsChanged={handleAssetsChanged} />;
      case "expire":
        return <ExpirePage clients={clients} />;
      case "iplease":
        return <IPLeasePage leases={leases} />;
      case "system":
        return <SystemStatusPage services={services} resources={resources} />;
      case "apc":
        return <APCRequestPage assets={assets} leases={leases} />;
      case "settings":
        return <SettingsPage refreshIntervalSec={refreshIntervalSec} onSaveRefreshInterval={handleSaveRefreshInterval} />;
      case "security":
        return securityUnlocked ? (
          <SecurityMonitorPage clients={clients} />
        ) : (
          <SecurityUnlockPanel onUnlock={handleSecurityUnlock} error={securityUnlockError} />
        );
      default:
        return <DashboardPage clients={clients} services={services} />;
    }
  }, [activePage, assets, clients, handleAssetsChanged, leases, refreshIntervalSec, resources, securityUnlockError, securityUnlocked, selectedClient, services]);

  if (authLoading) {
    return <div className="app-loading">웹 콘솔 인증 상태를 확인하는 중입니다...</div>;
  }

  if (!authState.authenticated) {
    return (
      <div data-theme="dark">
        <LoginPage onLoginSuccess={handleLoginSuccess} />
      </div>
    );
  }

  return (
    <div data-theme="dark">
      <Layout
        activePage={activePage}
        setActivePage={navigateToPage}
        username={authState.username}
        onLogout={handleLogout}
        securityUnlocked={securityUnlocked}
      >
        {loading ? <div className="panel muted-text">백엔드 데이터를 불러오는 중입니다...</div> : null}
        {apiError ? <div className="panel error-text">{apiError}</div> : null}
        {renderedPage}
      </Layout>
    </div>
  );
}
