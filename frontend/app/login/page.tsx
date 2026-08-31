"use client";

import { ArrowLeft, BookOpenCheck, Eye, EyeOff, LockKeyhole, Mail } from "lucide-react";
import { FormEvent, useState } from "react";
import Link from "next/link";

export default function LoginPage() {
  const [showPassword, setShowPassword] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    window.setTimeout(() => { window.location.href = "/"; }, 700);
  }

  return (
    <main className="login-page">
      <section className="login-context">
        <Link className="brand inverse" href="/"><span className="brand-mark light"><BookOpenCheck size={19} /></span><span>VERITY DESK</span></Link>
        <div><p className="eyebrow">Investigative workspace</p><h1>Evidence deserves<br />a clear record.</h1><p>Sign in to retain investigations, revisit source trails, and export complete verification reports.</p></div>
        <span className="context-footnote">MULTI-MODEL ANALYSIS · RULE-BASED REVIEW</span>
      </section>
      <section className="login-form-wrap">
        <Link className="back-link" href="/"><ArrowLeft size={16} /> Back to workspace</Link>
        <form className="login-form" onSubmit={submit}>
          <div><p className="eyebrow">Secure access</p><h2>Sign in to your desk</h2><p>Use your organisation credentials to continue.</p></div>
          <label>Email address<div className="login-input"><Mail size={17} /><input type="email" placeholder="analyst@organisation.com" required /></div></label>
          <label>Password<div className="login-input"><LockKeyhole size={17} /><input type={showPassword ? "text" : "password"} placeholder="Enter your password" required /><button type="button" onClick={() => setShowPassword((value) => !value)} title={showPassword ? "Hide password" : "Show password"} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>
          <div className="login-options"><label><input type="checkbox" /> Remember me</label><button type="button">Forgot password?</button></div>
          <button className="primary-button login-submit" type="submit">{submitted ? "Signing in..." : "Sign in"}</button>
          <p className="demo-note">Demo interface only. No credentials are stored or transmitted.</p>
        </form>
      </section>
    </main>
  );
}
