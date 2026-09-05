import { MonitorUp, Plus, Send, ShieldCheck, X, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { VerificationMode } from "../types/verification";

interface VerificationFormProps {
  input: string;
  image: File | null;
  mode: VerificationMode;
  error: string;
  showBrowser: boolean;
  onInputChange: (value: string) => void;
  onImageSelect: (file: File | null) => void;
  onImageRemove: () => void;
  onModeChange: (mode: VerificationMode) => void;
  onShowBrowserChange: (value: boolean) => void;
  onSubmit: () => void;
}

function ImageAttachment({ image, onRemove }: { image: File; onRemove: () => void }) {
  const [previewUrl] = useState(() => URL.createObjectURL(image));

  useEffect(() => () => URL.revokeObjectURL(previewUrl), [previewUrl]);

  return <div className="image-attachment">
    <img src={previewUrl} alt="Selected upload preview" />
    <span><strong>{image.name}</strong><small>{(image.size / 1024 / 1024).toFixed(2)} MB</small></span>
    <button type="button" onClick={onRemove} title="Remove image" aria-label="Remove image"><X size={16} /></button>
  </div>;
}

export function VerificationForm({ input, image, mode, error, showBrowser, onInputChange, onImageSelect, onImageRemove, onModeChange, onShowBrowserChange, onSubmit }: VerificationFormProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);

  return (
    <section className="hero-workspace">
      <div className="section-label"><span>NEW INVESTIGATION</span><span>LIVE GONKA REVIEW</span></div>
      <div className="hero-copy">
        <div><p className="eyebrow">Evidence-led news verification</p><h1>Check the claim.<br />Trace the evidence.</h1></div>
        <p className="intro">Paste a claim or article link, or attach a news image. Three Gonka models review the evidence while source rules check credibility.</p>
      </div>
      <div className="verification-panel">
        <label htmlFor="verification-input">WHAT SHOULD WE CHECK?</label>
        <div
          className={`chat-composer ${error ? "has-error" : ""} ${dragActive ? "drag-active" : ""}`}
          onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={(event) => { if (event.currentTarget === event.target) setDragActive(false); }}
          onDrop={(event) => {
            event.preventDefault();
            setDragActive(false);
            onImageSelect(event.dataTransfer.files[0] ?? null);
          }}
        >
          {image && <ImageAttachment key={`${image.name}-${image.size}-${image.lastModified}`} image={image} onRemove={onImageRemove} />}
          <textarea
            id="verification-input"
            value={input}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSubmit();
              }
            }}
            placeholder={image ? "Add the caption or claim shown with this image..." : "Paste a news claim, article URL, or statement to verify..."}
            aria-describedby={error ? "verification-error" : undefined}
            rows={4}
          />
          <div className="composer-actions">
            <input
              ref={fileInput}
              className="hidden-file-input"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(event) => {
                onImageSelect(event.target.files?.[0] ?? null);
                event.target.value = "";
              }}
            />
            <button className="attach-button" type="button" onClick={() => fileInput.current?.click()} title="Attach JPG, PNG, or WEBP image" aria-label="Attach image"><Plus size={20} /></button>
            <span className="composer-hint">Text, URL, or image · Shift + Enter for new line</span>
            <button className="send-button" type="button" onClick={onSubmit}><span>Verify</span><Send size={17} /></button>
          </div>
        </div>
        {error && <p className="field-error" id="verification-error">{error}</p>}
        <div className="mode-row" role="radiogroup" aria-label="Verification depth">
          <button className={mode === "quick" ? "mode-option active" : "mode-option"} onClick={() => onModeChange("quick")} role="radio" aria-checked={mode === "quick"}><span className="mode-icon"><Zap size={17} /></span><span><strong>Quick review</strong><small>One research pass · up to 5 sources per claim · two models</small></span></button>
          <button className={mode === "professional" ? "mode-option active" : "mode-option"} onClick={() => onModeChange("professional")} role="radio" aria-checked={mode === "professional"}><span className="mode-icon"><ShieldCheck size={17} /></span><span><strong>Professional review</strong><small>AI research plan + evidence-gap analysis + targeted follow-up · up to 12 sources per claim</small></span></button>
        </div>
        <p className="composer-hint">{mode === "professional" ? "Professional takes longer: it examines missing context and counter-evidence, searches again when needed, then sends the expanded evidence to both models." : "Quick uses a single research pass and two model reviews. Both modes can review up to three extracted claims."}</p>
        <label className="browser-toggle">
          <input type="checkbox" checked={showBrowser} onChange={(event) => onShowBrowserChange(event.target.checked)} />
          <span className="browser-toggle-box"><MonitorUp size={17} /></span>
          <span><strong>Show live browser window</strong><small>Open Chrome locally while evidence pages are checked.</small></span>
        </label>
      </div>
      <div className="trust-note"><ShieldCheck size={17} /><span>Every verdict includes model opinions, source risk, evidence links and Gonka request IDs.</span></div>
    </section>
  );
}
