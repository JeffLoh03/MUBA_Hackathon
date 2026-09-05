import { BookOpenCheck, Eye, EyeOff, LockKeyhole, Mail } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { AuthConnectionState, useAuth } from "../hooks/useAuth";

export function LoginPage() {
  const { state, loading, error: sessionError, signIn } = useAuth();
  const [showPassword, setShowPassword] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const location = useLocation();
  const setup = state?.setup_required ?? false;
  const requestedPath = (location.state as { from?: string } | null)?.from;
  const destination = requestedPath?.startsWith("/") && !requestedPath.startsWith("//") && !requestedPath.startsWith("/login") ? requestedPath : "/";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitted) return;
    setSubmitted(true);
    setError("");
    try {
      await signIn(email.trim(), password, setup);
      setPassword("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Sign-in failed. Please try again.");
    } finally {
      setSubmitted(false);
    }
  }

  if (loading || !state) return <AuthConnectionState />;
  if (state.authenticated) return <Navigate to={destination} replace />;

  return (
    <main className="login-page">
      <section className="login-context">
        <Link className="brand inverse" to="/"><span className="brand-mark light"><BookOpenCheck size={19} /></span><span>VERITY DESK</span></Link>
        <div><p className="eyebrow">Investigative workspace</p><h1>Evidence deserves<br />a clear record.</h1><p>Keep your investigations, evidence and Gonka request IDs together in a private workspace.</p></div>
        <span className="context-footnote">MULTI-MODEL ANALYSIS · EVIDENCE-LED REVIEW</span>
      </section>
      <section className="login-form-wrap">
        <form className="login-form" onSubmit={submit}>
          <div><p className="eyebrow">{setup ? "First-time setup" : "Secure access"}</p><h2>{setup ? "Create your desk" : "Sign in to your desk"}</h2><p>{setup ? "Create the owner account from this computer. Setup closes once the account is created." : "Use your desk account to access investigations and the transparency ledger."}</p></div>
          <label htmlFor="login-email">Email address<div className="login-input"><Mail size={17} /><input id="login-email" type="email" autoComplete="username" placeholder="analyst@organisation.com" value={email} onChange={(event) => { setEmail(event.target.value); setError(""); }} required disabled={submitted} /></div></label>
          <label htmlFor="login-password">Password<div className="login-input"><LockKeyhole size={17} /><input id="login-password" type={showPassword ? "text" : "password"} autoComplete={setup ? "new-password" : "current-password"} placeholder={setup ? "At least 12 characters" : "Enter your password"} value={password} onChange={(event) => { setPassword(event.target.value); setError(""); }} minLength={setup ? 12 : undefined} maxLength={128} required disabled={submitted} /><button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>
          {(error || sessionError) && <p className="field-error" role="alert">{error || sessionError}</p>}
          <button className="primary-button login-submit" type="submit" disabled={submitted}>{submitted ? (setup ? "Creating account…" : "Signing in…") : (setup ? "Create account" : "Sign in")}</button>
          <p className="demo-note">{setup ? "Choose a unique password of 12–128 characters. " : ""}Access is protected by a session cookie. Submitted claims and report metadata are saved; uploaded image bytes are not retained.</p>
        </form>
      </section>
    </main>
  );
}
