const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
const SECURITY_ACCESS_KEY = "certsvc.securityConsole.unlocked";
const SECURITY_TOKEN_KEY = "certsvc.securityConsole.token";
const SECURITY_TOKEN_EXPIRES_AT_KEY = "certsvc.securityConsole.expiresAt";
const SECURITY_TOKEN_TTL_MS = 30 * 60 * 1000;

function buildUrl(path) {
  return `${API_BASE}${path}`;
}

async function parseResponseError(response) {
  const text = await response.text();
  if (!text) {
    return `Request failed: ${response.status}`;
  }
  try {
    const parsed = JSON.parse(text);
    return parsed.detail || parsed.message || text;
  } catch {
    return text;
  }
}

async function request(path, options = {}) {
  const response = await fetch(buildUrl(path), {
    cache: "no-store",
    credentials: "include",
    ...options,
  });
  if (!response.ok) {
    throw new Error(await parseResponseError(response));
  }
  return response;
}

function securityHeaders(extraHeaders = {}) {
  const token = getSecurityConsoleToken();
  return {
    ...extraHeaders,
    ...(token ? { "X-Internal-Token": token } : {}),
  };
}

export function getSecurityConsoleToken() {
  try {
    const token = window.sessionStorage.getItem(SECURITY_TOKEN_KEY) || "";
    const expiresAt = Number(window.sessionStorage.getItem(SECURITY_TOKEN_EXPIRES_AT_KEY) || 0);
    if (!token) {
      return "";
    }
    if (!expiresAt || Date.now() >= expiresAt) {
      setSecurityConsoleToken("");
      return "";
    }
    return token;
  } catch {
    return "";
  }
}

export function getSecurityConsoleAccess() {
  try {
    const unlocked = window.sessionStorage.getItem(SECURITY_ACCESS_KEY) === "1";
    const expiresAt = Number(window.sessionStorage.getItem(SECURITY_TOKEN_EXPIRES_AT_KEY) || 0);
    if (!unlocked || !expiresAt || Date.now() >= expiresAt) {
      setSecurityConsoleAccess(false);
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

export function setSecurityConsoleAccess(unlocked) {
  try {
    if (unlocked) {
      window.sessionStorage.setItem(SECURITY_ACCESS_KEY, "1");
      window.sessionStorage.setItem(SECURITY_TOKEN_EXPIRES_AT_KEY, String(Date.now() + SECURITY_TOKEN_TTL_MS));
    } else {
      window.sessionStorage.removeItem(SECURITY_ACCESS_KEY);
      window.sessionStorage.removeItem(SECURITY_TOKEN_KEY);
      window.sessionStorage.removeItem(SECURITY_TOKEN_EXPIRES_AT_KEY);
    }
  } catch {
    // ignore storage failures
  }
}

export function setSecurityConsoleToken(token) {
  try {
    if (token) {
      window.sessionStorage.setItem(SECURITY_TOKEN_KEY, token);
      window.sessionStorage.setItem(SECURITY_TOKEN_EXPIRES_AT_KEY, String(Date.now() + SECURITY_TOKEN_TTL_MS));
    } else {
      window.sessionStorage.removeItem(SECURITY_TOKEN_KEY);
    }
  } catch {
    // ignore storage failures
  }
}

export async function getAuthMe() {
  const response = await fetch(buildUrl("/auth/me"), {
    cache: "no-store",
    credentials: "include",
  });
  if (response.status === 401) {
    return { authenticated: false };
  }
  if (!response.ok) {
    throw new Error(await parseResponseError(response));
  }
  return response.json();
}

export async function loginWeb(payload) {
  const response = await request("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function logoutWeb() {
  const response = await request("/auth/logout", {
    method: "POST",
  });
  setSecurityConsoleAccess(false);
  return response.json();
}

export async function openSecurityConsole(password) {
  const response = await request("/security/console/access", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const result = await response.json();
  if (result?.ok) {
    setSecurityConsoleAccess(true);
  }
  if (result?.token) {
    setSecurityConsoleToken(result.token);
  }
  return result;
}

export async function getClients() {
  const response = await request("/clients");
  return response.json();
}

export async function getEquipmentAssets() {
  const response = await request("/equipment-assets");
  return response.json();
}

export async function getLeases() {
  const response = await request("/leases");
  return response.json();
}

export async function getSystemStatus() {
  const response = await request("/system/status");
  return response.json();
}

export async function requestAdminApc({ hostname, serialNumber, deviceModel, adminPassword }) {
  return request("/admin/apc/request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hostname, serialNumber, deviceModel, adminPassword }),
  });
}

export async function getClientBackupInfo(clientId) {
  const response = await request(`/clients/${clientId}/backup`);
  return response.json();
}

export async function downloadClientBackup(clientId) {
  return request(`/clients/${clientId}/backup/download`);
}

export async function getRuntimeGuardLogs() {
  const response = await request("/runtime-guard/logs", {
    headers: securityHeaders(),
  });
  return response.json();
}

export async function getAlertHistory() {
  const response = await request("/alerts/history", {
    headers: securityHeaders(),
  });
  return response.json();
}

export async function getBackupSettings() {
  const response = await request("/backup/settings", {
    headers: securityHeaders(),
  });
  return response.json();
}

export async function getBackupLogs() {
  const response = await request("/backup/logs", {
    headers: securityHeaders(),
  });
  return response.json();
}

export async function testBackupSettings(payload) {
  const response = await request("/backup/settings/test", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function saveBackupSettings(payload) {
  const response = await request("/backup/settings", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function runBackupNow(payload = {}) {
  const response = await request("/backup/run", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function listRestoreBackups(payload) {
  const response = await request("/backup/restore/list", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function runRestoreNow(payload) {
  const response = await request("/backup/restore/run", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function saveSlackSettings(payload) {
  const response = await request("/backup/slack/settings", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function sendSlackTestMessage(payload) {
  const response = await request("/backup/slack/test", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function getSecurityMonitor() {
  const response = await request("/security/monitor", {
    headers: securityHeaders(),
  });
  return response.json();
}

export async function updateSecurityMonitorSettings(payload) {
  const response = await request("/security/monitor/settings", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function unbanSecurityIp(payload) {
  const response = await request("/security/monitor/unban", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}

export async function unenrollSecurityClient(payload) {
  const response = await request("/security/monitor/unenroll", {
    method: "POST",
    headers: securityHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return response.json();
}
