import { BookOpenCheck, Database, LogOut } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

export function AppHeader() {
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [error, setError] = useState("");
  const { state, signOut } = useAuth();

  async function logout() {
    setLoggingOut(true);
    setError("");
    try { await signOut(); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Could not sign out."); }
    finally { setLoggingOut(false); }
  }

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/health", { signal: controller.signal })
      .then((response) => setBackendOnline(response.ok))
      .catch(() => setBackendOnline(false));
    return () => controller.abort();
  }, []);

  return (
    <header className="topbar no-print">
      <Link className="brand" to="/" aria-label="Verity Desk home"><span className="brand-mark"><BookOpenCheck size={19} /></span><span>VERITY DESK</span></Link>
      <nav className="header-actions" aria-label="Primary navigation">
        <span className={`system-status ${backendOnline === false ? "offline" : ""}`}><i /> {backendOnline === null ? "Checking backend" : backendOnline ? "Backend online" : "Backend offline"}</span>
        <Link className="login-link" to="/transparency" aria-label="Transparency ledger"><Database size={17} /><span>Transparency</span></Link>
        <button className="login-link" type="button" onClick={() => void logout()} disabled={loggingOut} title={state?.user?.email} aria-label="Sign out"><LogOut size={17} /><span>{loggingOut ? "Signing out…" : "Sign out"}</span></button>
        {error && <span className="header-error" role="alert">{error}</span>}
      </nav>
    </header>
  );
}
