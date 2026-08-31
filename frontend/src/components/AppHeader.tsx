import { BookOpenCheck, LogIn } from "lucide-react";
import { Link } from "react-router-dom";

export function AppHeader() {
  return (
    <header className="topbar no-print">
      <Link className="brand" to="/" aria-label="Verity Desk home"><span className="brand-mark"><BookOpenCheck size={19} /></span><span>VERITY DESK</span></Link>
      <nav className="header-actions" aria-label="Primary navigation">
        <span className="system-status"><i /> Systems online</span>
        <Link className="login-link" to="/login"><LogIn size={17} /><span>Sign in</span></Link>
      </nav>
    </header>
  );
}
