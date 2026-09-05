import { CheckCircle2, ChevronRight, Database, RefreshCw, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { AppHeader } from "../components/AppHeader";
import { GonkaCallList } from "../components/GonkaCallList";
import { ReportView } from "../components/ReportView";
import { useAuth } from "../hooks/useAuth";
import type { AuditRunDetail, AuditRunSummary } from "../types/verification";


function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "In progress";
}

function readableArticleTitle(value: string): string {
  try {
    const url = new URL(value);
    const hostname = url.hostname.replace(/^www\./, "");
    const segments = url.pathname.split("/").filter(Boolean).reverse();
    const rawSlug = segments.find((segment) => !/^\d+$/.test(segment)) || "";
    const slug = decodeURIComponent(rawSlug)
      .replace(/\.(?:html?|php)$/i, "")
      .replace(/[-_]\d{5,}$/, "")
      .replace(/[-_]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!slug) return `Article from ${hostname}`;
    return `${slug.charAt(0).toUpperCase()}${slug.slice(1)} — ${hostname}`;
  } catch {
    return value;
  }
}

function inputLabel(run: AuditRunSummary): string {
  const genericMultiClaim = run.extracted_claim.trim().toLowerCase() === "multiple claims reviewed";
  if (run.extracted_claim && !genericMultiClaim) return run.extracted_claim;
  if (run.article_url) return readableArticleTitle(run.article_url);
  if (run.image_name) return `${run.image_name}${run.input_text ? ` · ${run.input_text}` : ""}`;
  return run.input_text || "Input pending";
}

function shortId(value: string): string {
  return value.slice(0, 8).toUpperCase();
}

export function TransparencyPage() {
  const { request } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [runs, setRuns] = useState<AuditRunSummary[]>([]);
  const selectedId = params.get("run") || "";
  const [revision, setRevision] = useState(0);
  const [detail, setDetail] = useState<AuditRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await request("/api/audits?limit=50", { cache: "no-store" });
      if (!response.ok) throw new Error(`Audit API returned HTTP ${response.status}.`);
      const body = await response.json() as { runs: AuditRunSummary[] };
      setRuns(body.runs);
      setRevision((current) => current + 1);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not load audit history.");
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    if (!selectedId && runs[0]) setParams({ run: runs[0].id }, { replace: true });
  }, [selectedId, runs, setParams]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    setDetail(null);
    setError("");
    request(`/api/audits/${encodeURIComponent(selectedId)}`, { signal: controller.signal, cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`Audit record returned HTTP ${response.status}.`);
        return response.json() as Promise<AuditRunDetail>;
      })
      .then((record) => { if (!controller.signal.aborted) setDetail(record); })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted) return;
        setError(requestError instanceof Error ? requestError.message : "Could not load the audit record.");
      });
    return () => controller.abort();
  }, [selectedId, revision, request]);

  const totals = useMemo(() => ({
    completed: runs.filter((run) => run.status === "completed").length,
    calls: runs.reduce((total, run) => total + run.gonka_call_count, 0),
  }), [runs]);

  return (
    <div className="workspace-shell">
      <AppHeader />
      <main className="transparency-page">
        <section className="transparency-hero">
          <div><p className="eyebrow">SQLite-backed accountability</p><h1>Transparency ledger.</h1><p>Every investigation keeps its public progress events, final evidence report, model identity, latency, and Gonka request and trace identifiers.</p></div>
          <div className="transparency-stats"><div><strong>{runs.length}</strong><span>recorded runs</span></div><div><strong>{totals.completed}</strong><span>completed</span></div><div><strong>{totals.calls}</strong><span>Gonka calls</span></div></div>
        </section>

        <div className="ledger-toolbar"><span><Database size={16} /> Local SQLite audit database · uploaded image bytes are not stored</span><button type="button" onClick={() => void loadRuns()} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={15} /> Refresh</button></div>
        {error && <p className="transparency-error"><ShieldAlert size={16} />{error}</p>}

        <section className="transparency-layout">
          <aside className="run-list" aria-label="Verification history">
            <div className="run-list-heading"><strong>INVESTIGATIONS</strong><span>Newest first</span></div>
            {runs.map((run) => { const title = inputLabel(run); return <div key={run.id}><Link className={`run-card ${selectedId === run.id ? "active" : ""}`} to={`/investigations/${encodeURIComponent(run.id)}`} aria-label={`Open investigation: ${title}`} title={title}><span><b>CASE {shortId(run.id)}</b><small>{formatDate(run.created_at_utc)}</small></span><strong>{title}</strong><span><i className={`run-status ${run.status}`} />{run.final_verdict || run.status}<small>{run.gonka_call_count} model calls</small></span><ChevronRight size={16} /></Link><Link className="text-button run-audit-link" to={`/transparency?run=${encodeURIComponent(run.id)}`}>View audit details</Link></div>; })}
            {!loading && runs.length === 0 && <p className="empty-audit">No investigations have been recorded yet. Complete a verification to create the first audit record.</p>}
          </aside>

          <div className="audit-detail">
            {detail ? <>
              <div className="audit-detail-heading"><div><p className="eyebrow">Case {shortId(detail.id)}</p><h2>{detail.final_verdict || detail.status}</h2></div><span className={`audit-status ${detail.status}`}>{detail.status === "completed" ? <CheckCircle2 size={15} /> : <ShieldAlert size={15} />}{detail.status}</span></div>
              <dl className="audit-facts"><div><dt>RUN ID</dt><dd><code>{detail.id}</code></dd></div><div><dt>MODE</dt><dd>{detail.mode}</dd></div><div><dt>CREATED</dt><dd>{formatDate(detail.created_at_utc)}</dd></div><div><dt>COMPLETED</dt><dd>{formatDate(detail.completed_at_utc)}</dd></div><div><dt>TRUTH SCORE</dt><dd>{detail.report?.claim_reports?.length ? "Per claim below" : detail.truth_score ?? "—"}</dd></div><div><dt>CONFIDENCE</dt><dd>{detail.report?.claim_reports?.length ? "Per claim below" : detail.confidence_score ?? "—"}</dd></div></dl>
              <div className="audit-input"><small>VERIFIED INPUT</small><p>{detail.article_url || detail.input_text || detail.image_name}</p>{detail.extracted_claim && <><small>EXTRACTED CLAIM</small><strong>{detail.extracted_claim}</strong></>}</div>

              <section className="stored-section"><h3>Gonka inference calls</h3><p>Full identifiers for every shared and claim-specific inference.</p><GonkaCallList calls={detail.gonka_calls} /></section>

              <section className="stored-section"><div><span>02</span><h3>Processing events</h3></div><p>The chronological, public operational trail. Private chain-of-thought is never stored.</p><div className="stored-event-list">{detail.events.map((event) => <div key={event.sequence_number}><time>{new Date(event.timestamp_utc).toLocaleTimeString()}</time><strong>{event.stage}</strong><code title={JSON.stringify(event.details)}>{JSON.stringify(event.details)}</code></div>)}{detail.events.length === 0 && <p className="empty-audit">No processing events were recorded.</p>}</div></section>
              {detail.error_message && <p role="alert" className="transparency-error">{detail.error_message}</p>}
              {detail.report && <section className="saved-report"><h3>Saved verification report</h3><ReportView key={detail.id} report={detail.report} runId={detail.id} completedAt={detail.completed_at_utc || ""} mode={detail.mode} input={detail.article_url || detail.input_text} imageName={detail.image_name} onReset={() => navigate("/")} /></section>}
            </> : <p className="empty-audit detail-empty">{selectedId && !error ? "Loading audit record…" : "Select an investigation to inspect its stored audit trail."}</p>}
          </div>
        </section>
      </main>
    </div>
  );
}
