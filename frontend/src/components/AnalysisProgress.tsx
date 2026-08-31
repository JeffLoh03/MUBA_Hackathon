import { Check, Circle, FileSearch, LoaderCircle } from "lucide-react";
import type { AnalysisStage, StageStatus, VerificationMode } from "../types/verification";

type ProgressStage = AnalysisStage & { status: StageStatus };
interface AnalysisProgressProps { progress: number; mode: VerificationMode; url: string; stages: ProgressStage[]; }

export function AnalysisProgress({ progress, mode, url, stages }: AnalysisProgressProps) {
  const active = stages.find((stage) => stage.status === "active") ?? stages.at(-1)!;
  const sourceTarget = mode === "quick" ? 5 : 18;
  const sourcesFound = Math.min(sourceTarget, Math.max(1, Math.round((progress / 100) * sourceTarget)));

  return (
    <section className="process-page" aria-live="polite">
      <div className="process-heading"><div><p className="eyebrow">Verification in progress</p><h1>Following the evidence trail.</h1></div><div className="progress-number"><strong>{progress}%</strong><span>overall progress</span></div></div>
      <div className="overall-track"><span style={{ width: `${progress}%` }} /></div>
      <div className="case-strip">
        <div><FileSearch size={18} /><span><small>ARTICLE</small>{new URL(url).hostname}</span></div>
        <div><small>REVIEW DEPTH</small><strong>{mode === "quick" ? "Quick" : "Professional"}</strong></div>
        <div><small>SOURCES FOUND</small><strong>{sourcesFound} / {sourceTarget}</strong></div>
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
      <div className="activity-line"><span className="pulse-dot" /><span><strong>{active.name}</strong> is active</span><span className="activity-message">{progress < 24 ? "Structuring article claims" : progress < 59 ? "Comparing independent sources" : progress < 81 ? "Looking for contradictions" : "Calculating final evidence score"}</span></div>
    </section>
  );
}
