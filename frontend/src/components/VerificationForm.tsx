import { ArrowRight, Link2, ShieldCheck, Zap } from "lucide-react";
import type { VerificationMode } from "../types/verification";

interface VerificationFormProps {
  url: string;
  mode: VerificationMode;
  error: string;
  onUrlChange: (value: string) => void;
  onModeChange: (mode: VerificationMode) => void;
  onSubmit: () => void;
}

export function VerificationForm({ url, mode, error, onUrlChange, onModeChange, onSubmit }: VerificationFormProps) {
  return (
    <section className="hero-workspace">
      <div className="section-label"><span>NEW INVESTIGATION</span><span>CASE 01428</span></div>
      <div className="hero-copy">
        <div><p className="eyebrow">Evidence-led news verification</p><h1>Check the claim.<br />Trace the evidence.</h1></div>
        <p className="intro">Submit a news article for a structured review across multiple AI analysts and a deterministic evidence framework.</p>
      </div>
      <div className="verification-panel">
        <label htmlFor="article-url">ARTICLE URL</label>
        <div className={error ? "url-row has-error" : "url-row"}>
          <div className="url-field"><Link2 size={19} /><input id="article-url" type="url" value={url} onChange={(event) => onUrlChange(event.target.value)} onKeyDown={(event) => event.key === "Enter" && onSubmit()} placeholder="https://news.example.com/article" aria-describedby={error ? "url-error" : undefined} /></div>
          <button className="primary-button" type="button" onClick={onSubmit}>Start verification <ArrowRight size={18} /></button>
        </div>
        {error && <p className="field-error" id="url-error">{error}</p>}
        <div className="mode-row" role="radiogroup" aria-label="Verification depth">
          <button className={mode === "quick" ? "mode-option active" : "mode-option"} onClick={() => onModeChange("quick")} role="radio" aria-checked={mode === "quick"}><span className="mode-icon"><Zap size={17} /></span><span><strong>Quick review</strong><small>3-5 trusted sources · ~2 minutes</small></span></button>
          <button className={mode === "professional" ? "mode-option active" : "mode-option"} onClick={() => onModeChange("professional")} role="radio" aria-checked={mode === "professional"}><span className="mode-icon"><ShieldCheck size={17} /></span><span><strong>Professional review</strong><small>15-20 sources · deeper cross-check</small></span></button>
        </div>
      </div>
      <div className="trust-note"><ShieldCheck size={17} /><span>Every verdict includes model opinions, rule deductions, and the complete score breakdown.</span></div>
    </section>
  );
}
