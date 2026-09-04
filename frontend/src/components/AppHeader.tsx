import { BookOpenCheck, LogIn } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

export function AppHeader() {
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);

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
        <span className={`system-status ${backendOnline === false ? "offline" : ""}`}><i /> {backendOnline === null ? "Checking backend" : backendOnline ? "Gonka backend online" : "Backend offline"}</span>
        <Link className="login-link" to="/login"><LogIn size={17} /><span>Sign in</span></Link>
      </nav>
    </header>
  );
}
