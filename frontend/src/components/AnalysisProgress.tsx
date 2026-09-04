import { Check, Circle, FileSearch, Image as ImageIcon, LoaderCircle, MessageSquareText } from "lucide-react";
import type { AnalysisStage, ProgressEvent, StageStatus, VerificationMode } from "../types/verification";

type ProgressStage = AnalysisStage & { status: StageStatus };
interface AnalysisProgressProps {
  progress: number;
  mode: VerificationMode;
  input: string;
  imageName: string;
  stages: ProgressStage[];
  events: ProgressEvent[];
  sourceCount: number;
  showBrowser: boolean;
}

function isArticleUrl(value: string): boolean {
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

function eventDetail(event: ProgressEvent): string {
  const details = event.details;
  if (typeof details.query === "string") return details.query;
  if (typeof details.url === "string") return details.url;
  if (typeof details.claim === "string") return details.claim;
  if (typeof details.fallback_claim === "string") return `Fallback: ${details.fallback_claim}`;
  if (typeof details.model === "string") return details.model;
  if (typeof details.ocr_preview === "string" && details.ocr_preview) return `OCR: ${details.ocr_preview}`;
  if (typeof details.final_verdict === "string") {
    const verdict = details.final_verdict.toLowerCase();
    return verdict === "unverified"
      ? `${details.final_verdict} | truth score not issued`
      : `${details.final_verdict} | score ${String(details.truth_score ?? "-")}`;
  }
  if (typeof details.evidence_count === "number") return `${details.evidence_count} usable sources retained`;
  if (typeof details.provider_error_count === "number" && details.provider_error_count > 0) {
    return `${String(details.raw_result_count ?? 0)} results | ${details.provider_error_count} searches failed`;
  }
  if (typeof details.raw_result_count === "number") return `${details.raw_result_count} search results found`;
  if (typeof details.source_trust_score === "number") return `Trust ${details.source_trust_score}/100 | ${String(details.website_risk_level)} risk`;
  if (typeof details.latency_ms === "number") return `${Math.round(details.latency_ms)} ms`;
  if (typeof details.safe_error_message === "string") return details.safe_error_message;
  return "Step recorded";
}

export function AnalysisProgress({ progress, mode, input, imageName, stages, events, sourceCount, showBrowser }: AnalysisProgressProps) {
  const active = stages.find((stage) => stage.status === "active") ?? stages.at(-1)!;
  const sourceTarget = mode === "quick" ? 5 : 12;
  const recentEvents = events.slice(-7).reverse();
  const submitted = input.trim();
  const sourceType = imageName ? "IMAGE" : isArticleUrl(submitted) ? "ARTICLE" : "TEXT CLAIM";
  const sourceValue = imageName || (isArticleUrl(submitted) ? new URL(submitted).hostname : submitted);
  const SourceIcon = imageName ? ImageIcon : isArticleUrl(submitted) ? FileSearch : MessageSquareText;

  return (
    <section className="process-page" aria-live="polite">
      <div className="process-heading"><div><p className="eyebrow">Verification in progress</p><h1>Following the evidence trail.</h1></div><div className="progress-number"><strong>{progress}%</strong><span>overall progress</span></div></div>
      <div className="overall-track"><span style={{ width: `${progress}%` }} /></div>
      <div className="case-strip">
        <div><SourceIcon size={18} /><span><small>{sourceType}</small><span className="source-value">{sourceValue || "Attached image"}</span></span></div>
        <div><small>REVIEW DEPTH</small><strong>{mode === "quick" ? "Quick" : "Professional"}</strong></div>
        <div><small>USABLE SOURCES</small><strong>{sourceCount} / {sourceTarget}</strong></div>
      </div>
      <div className="stage-list">
        {stages.map((stage, index) => (
          <div className={`stage-row ${stage.status}`} key={stage.id}>
            <div className="stage-index">{stage.status === "complete" ? <Check size={18} /> : stage.status === "active" ? <LoaderCircle className="spin" size={18} /> : <Circle size={14} />}</div>
            <div className="stage-name"><small>0{index + 1}</small><strong>{stage.name}</strong><span>{stage.role}</span></div>
            <p>{stage.description}</p>
            <span className="stage-state">{stage.status === "complete" ? "Complete" : stage.status === "active" ? "Running" : "Queued"}</span>
          </div>
        ))}
      </div>
      <div className="activity-line"><span className="pulse-dot" /><span><strong>{active.name}</strong> is active</span><span className="activity-message">{showBrowser ? "Chrome demonstration enabled" : "Structured audit events are live"}</span></div>
      <section className="live-ledger">
        <div className="live-ledger-heading"><span>LIVE ACTIVITY</span><span>{events.length} EVENTS</span></div>
        {recentEvents.length === 0 ? <p className="ledger-empty">Connecting to the verification service...</p> : recentEvents.map((event, index) => (
          <div className="ledger-event" key={`${event.timestamp_utc}-${index}`}>
            <time>{new Date(event.timestamp_utc).toLocaleTimeString()}</time>
            <strong>{event.stage}</strong>
            <span>{eventDetail(event)}</span>
          </div>
        ))}
      </section>
    </section>
  );
}
