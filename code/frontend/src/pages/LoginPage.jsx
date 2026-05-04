import { useState } from "react";
import { loginWeb } from "../api/client";

export default function LoginPage({ onLoginSuccess }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    if (!username.trim() || !password || loading) return;
    setLoading(true);
    setError("");
    try {
      const result = await loginWeb({ username: username.trim(), password });
      setPassword("");
      onLoginSuccess(result);
    } catch (requestError) {
      setError(requestError.message || "로그인에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="login-panel">
        <div className="login-brand">
          <img src="/header-logo.png" alt="Elim.net" className="login-brand-image" />
        </div>
        <div className="brand-badge">secure://operations</div>
        <h1>엘림넷 | SSL VPN 인증서 관리</h1>
        <p className="muted-text">운영 포털에 접근하려면 관리자 계정으로 로그인해 주세요.</p>

        <form className="form-grid" onSubmit={submit}>
          <div className="field-group">
            <label>아이디</label>
            <input
              className="input"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              maxLength={64}
              placeholder="admin"
            />
          </div>

          <div className="field-group">
            <label>비밀번호</label>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              maxLength={256}
              placeholder="관리자 비밀번호"
            />
          </div>

          {error ? <div className="error-text">{error}</div> : null}

          <div className="button-group">
            <button className="primary-btn" type="submit" disabled={!username.trim() || !password || loading}>
              {loading ? "로그인 중..." : "로그인"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
