import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AnalysisProgress } from "../components/AnalysisProgress";
import { AppHeader } from "../components/AppHeader";
import { ReportView } from "../components/ReportView";
import { analysisStages } from "../data/verificationStages";
import { useAuth } from "../hooks/useAuth";
import { progressByStage } from "../hooks/useVerification";
import { overallClaimProgress } from "../hooks/verificationStream";
import type { AuditRunDetail, StageStatus } from "../types/verification";

export function InvestigationPage() {
  const { runId = "" } = useParams();
  // Remount on navigation so another case's report never flashes while loading.
  return <SavedInvestigation key={runId} runId={runId} />;
}

function SavedInvestigation({ runId }: { runId: string }) {
  const { request } = useAuth();
  const navigate = useNavigate();
  const [record, setRecord] = useState<AuditRunDetail | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const response = await request(`/api/audits/${encodeURIComponent(runId)}`, {
          signal: controller.signal, cache: "no-store",
        });
        if (!response.ok) throw new Error(response.status === 404
          ? "This investigation was not found or is not available to your account."
          : `Could not load this investigation (HTTP ${response.status}).`);
        const next = await response.json() as AuditRunDetail;
        if (controller.signal.aborted) return;
        setRecord(next);
        setError("");
        // Read the existing job; never submit another verification or inference.
        if (next.status === "running") timer = setTimeout(() => void load(), 2000);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not load this investigation.");
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [runId, request, retry]);

  const progress = (record?.events ?? []).reduce((current, event) => {
    const step = progressByStage[event.stage];
    return step === undefined ? current : Math.max(current, overallClaimProgress(step, event.details));
  }, 1);
  const stages = analysisStages.map((stage) => ({
    ...stage, status: (progress >= stage.end ? "complete" : progress >= stage.start ? "active" : "waiting") as StageStatus,
  }));
  const evidenceEvent = record?.events.slice().reverse().find((event) => event.stage === "Evidence processing completed");
  const sourceCount = evidenceEvent?.details.evidence_count;

  return <div className="workspace-shell">
    <AppHeader />
    <nav className="report-page investigation-nav no-print" aria-label="Investigation navigation">
      <Link className="text-button" to={`/transparency?run=${encodeURIComponent(runId)}`}>Back to transparency</Link>
      <Link className="text-button" to="/">New investigation</Link>
    </nav>
    {error && <div className="report-page"><p className="transparency-error" role="alert">{error}</p><button className="primary-button" type="button" onClick={() => setRetry((value) => value + 1)}>Try again</button></div>}
    {!record && !error && <p className="report-page" role="status">Opening investigation…</p>}
    {record?.status === "running" && <>
      <p className="report-page investigation-notice" role="status">{error ? "Live updates paused. Try again to reconnect." : "This investigation is still running. Progress updates automatically."}</p>
      <AnalysisProgress progress={progress} mode={record.mode} input={record.article_url || record.input_text}
        imageName={record.image_name} stages={stages} events={record.events}
        sourceCount={typeof sourceCount === "number" ? sourceCount : 0}
        showBrowser={record.events.some((event) => event.stage === "Visible browser ready")} />
    </>}
    {record?.status === "failed" && <section className="report-page"><p className="eyebrow">Investigation stopped</p><h1>This check could not finish.</h1><p role="alert">{record.error_message || "The verification failed. Open Transparency for the saved progress events."}</p><p>Your saved history is still available. Starting a new investigation is a separate action.</p></section>}
    {record?.status === "completed" && (record.report
      ? <ReportView report={record.report} runId={record.id} completedAt={record.completed_at_utc || ""}
          mode={record.mode} input={record.article_url || record.input_text} imageName={record.image_name} onReset={() => navigate("/")} />
      : <p className="report-page" role="alert">This investigation finished, but its saved report is unavailable.</p>)}
  </div>;
}
