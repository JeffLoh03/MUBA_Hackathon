import { Check, CheckCircle2, ChevronRight, Copy, Download, ExternalLink, RotateCcw, ShieldAlert } from "lucide-react";
import { type CSSProperties, useMemo, useState } from "react";
import type { EvidenceItem, FactCheckReport, VerificationMode } from "../types/verification";

interface ReportViewProps {
  report: FactCheckReport;
  completedAt: string;
  mode: VerificationMode;
  input: string;
  imageName: string;
  onReset: () => void;
}

function displayVerdict(verdict: string): string {
  return verdict.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function verdictHeadline(verdict: string): string {
  const normalized = verdict.toLowerCase().replaceAll("_", " ");
  const headlines: Record<string, string> = {
    "true": "Supported by evidence.",
    "mostly true": "Mostly supported.",
    "misleading": "True details, misleading context.",
    "mostly false": "Mostly contradicted.",
    "false": "Contradicted by evidence.",
    "unverified": "Not enough reliable evidence.",
    "insufficient evidence": "More context is needed.",
  };
  return headlines[normalized] ?? displayVerdict(verdict);
}

function reliability(item: EvidenceItem): "High" | "Medium" | "Low" {
  if (item.source_quality >= 0.7) return "High";
  if (item.source_quality >= 0.45) return "Medium";
  return "Low";
}

function shortModelName(modelId: string): string {
  const name = modelId.split("/").at(-1) ?? modelId;
  return name.replace(/-\d{4}$/, "");
}

export function ReportView({ report, completedAt, mode, input, imageName, onReset }: ReportViewProps) {
  const [notice, setNotice] = useState("");
  const credibility = report.source_credibility_assessment;
  const supportIds = useMemo(() => new Set(report.supporting_evidence.map((item) => item.evidence_id)), [report.supporting_evidence]);
  const contradictIds = useMemo(() => new Set(report.contradicting_evidence.map((item) => item.evidence_id)), [report.contradicting_evidence]);
  const successfulTraces = report.gonka_trace.filter((trace) => trace.success);
  const caseSeed = successfulTraces[0]?.response_body_id ?? completedAt;
  const caseId = caseSeed.replace(/[^a-z0-9]/gi, "").slice(-8).toUpperCase() || "LOCAL";
  const completedLabel = completedAt ? new Date(completedAt).toLocaleString() : "Completed";
  const submittedInput = input.trim();
  const normalizedVerdict = report.final_verdict.toLowerCase().replaceAll("_", " ");
  const truthIsUndetermined = [
    "unverified",
    "insufficient evidence",
    "not a verifiable factual claim",
  ].includes(normalizedVerdict);
  const evidenceFailure = report.limitations.find((item) =>
    item.startsWith("Web search") || item.startsWith("Search results were found"),
  );
  let sourceUrl = "";
  try {
    const parsed = new URL(submittedInput);
    if (["http:", "https:"].includes(parsed.protocol)) sourceUrl = submittedInput;
  } catch {
    sourceUrl = "";
  }
  const averageEvidenceQuality = report.all_evidence.length
    ? Math.round(report.all_evidence.reduce((total, item) => total + item.source_quality, 0) / report.all_evidence.length * 100)
    : 0;
  const averageModelConfidence = report.verifier_outputs.length
    ? Math.round(report.verifier_outputs.reduce((total, item) => total + item.confidence, 0) / report.verifier_outputs.length)
    : 0;
  const evidenceSignals = [
    { label: "Source trust", score: credibility?.source_trust_score ?? 0, note: credibility?.summary || "No source credibility assessment was available." },
    { label: "Independent support", score: Math.min(100, (credibility?.independent_source_count ?? 0) * 25), note: `${credibility?.independent_source_count ?? 0} independent source domains retained after deduplication.` },
    { label: "Evidence quality", score: averageEvidenceQuality, note: "Average deterministic quality score across the retained evidence ledger." },
    { label: "Model confidence", score: averageModelConfidence, note: "Average confidence reported by the independent Gonka reviewers." },
  ];

  const modelFindings = report.verifier_outputs.map((finding, index) => {
    const stepName = `verifier_${index + 1}`;
    const trace = report.gonka_trace.find((item) => item.step_name === stepName && item.success)
      ?? report.gonka_trace.find((item) => item.step_name.startsWith(stepName));
    return { ...finding, model: finding.model_id || trace?.requested_model_id || `Verifier ${index + 1}` };
  });

  async function copyReport() {
    await navigator.clipboard.writeText([
      "VERITY DESK REPORT",
      `Verdict: ${report.final_verdict}`,
      `Truth score: ${truthIsUndetermined ? "Undetermined (insufficient evidence)" : `${report.truth_score}/100`}`,
      `Confidence: ${report.confidence_score}/100`,
      `Claim: ${report.extracted_claim}`,
      `Submitted input: ${submittedInput || imageName}`,
      `Explanation: ${report.concise_explanation}`,
    ].join("\n"));
    setNotice("Report copied");
    window.setTimeout(() => setNotice(""), 1800);
  }

  return (
    <main className="report-page">
      <div className="report-toolbar no-print">
        <button className="text-button" type="button" onClick={onReset}><RotateCcw size={16} /> New investigation</button>
        <div>
          <button className="icon-command" type="button" onClick={copyReport} title="Copy report" aria-label="Copy report"><Copy size={17} /></button>
          <button className="export-button" type="button" onClick={() => window.print()}><Download size={17} /> Export PDF</button>
        </div>
      </div>

      <section className="report-masthead">
        <div className="report-meta"><span>CASE {caseId}</span><span>COMPLETED {completedLabel.toUpperCase()}</span></div>
        <div className="verdict-grid">
          <div><p className="eyebrow">Final assessment · {displayVerdict(report.final_verdict)}</p><h1>{verdictHeadline(report.final_verdict)}</h1><p className="verdict-summary">{report.concise_explanation}</p></div>
          <div className="score-block">
            <div className="score-ring" style={{ "--score": truthIsUndetermined ? 0 : report.truth_score } as CSSProperties}><span><strong>{truthIsUndetermined ? "?" : report.truth_score}</strong><small>{truthIsUndetermined ? "truth unknown" : "/ 100 truth"}</small></span></div>
            <div><strong>{report.confidence_score}% assessment confidence</strong><span>{report.all_evidence.length} sources reviewed</span><span>{credibility?.independent_source_count ?? 0} independent domains</span><span>{mode === "quick" ? "Quick" : "Professional"} review</span></div>
          </div>
        </div>
        <div className="claim-reference"><span>CHECKED CLAIM</span><strong>{report.extracted_claim || "No verifiable claim extracted"}</strong></div>
        {sourceUrl ? <div className="article-reference"><span>ANALYSED URL</span><a href={sourceUrl} target="_blank" rel="noreferrer">{sourceUrl}<ExternalLink size={14} /></a></div> : <div className="article-reference"><span>{imageName ? "IMAGE INPUT" : "SUBMITTED TEXT"}</span><strong>{imageName ? `${imageName}${submittedInput ? ` · ${submittedInput}` : ""}` : submittedInput}</strong></div>}
      </section>

      {report.image_context_assessment && <section className="report-section image-context-section">
        <div className="section-heading"><div><span>IMAGE</span><h2>Image context</h2></div><p>The image is checked through OCR, metadata, its caption and open-web evidence. This is not a pixel-level deepfake test.</p></div>
        <div className="image-context-grid">
          <div><small>CONTEXT VERDICT</small><strong>{report.image_context_assessment.verdict}</strong></div>
          <div><small>OCR TEXT</small><p>{report.image_context_assessment.ocr_text || "No readable text detected."}</p></div>
          <div><small>VISUAL CONTEXT</small><p>{report.image_context_assessment.visual_description || "No Gonka vision model configured; caption and OCR fallback used."}</p></div>
        </div>
      </section>}

      <section className="report-section">
        <div className="section-heading"><div><span>01</span><h2>Evidence profile</h2></div><p>Real signals from the retrieved pages, source rules and Gonka reviewers.</p></div>
        <div className="metric-list">
          {evidenceSignals.map((metric) => <div className="metric-row" key={metric.label}><div><strong>{metric.label}</strong><small>Observed signal</small></div><p>{metric.note}</p><div className="mini-track"><span style={{ width: `${metric.score}%` }} /></div><strong className="metric-score">{metric.score}</strong></div>)}
        </div>
      </section>

      <section className="report-section">
        <div className="section-heading"><div><span>02</span><h2>Gonka model opinions</h2></div><p>Independent reviews are shown separately; model agreement never replaces evidence.</p></div>
        <div className="model-grid">
          {modelFindings.map((finding, index) => <article className="model-finding" key={`${finding.model}-${index}`}><div><span className="model-initial">{shortModelName(finding.model).slice(0, 1)}</span><div><h3>{shortModelName(finding.model)}</h3><small>{finding.confidence}% confidence · {finding.support_score}/100 support</small></div></div><strong className={finding.support_score >= 60 ? "finding-verdict" : "finding-verdict warning-text"}><CheckCircle2 size={16} />{displayVerdict(finding.verdict)}</strong><p>{finding.reasoning_summary || "No public reasoning summary returned."}</p></article>)}
          {modelFindings.length === 0 && <p className="empty-report-row">No model opinion was produced for this input.</p>}
        </div>
      </section>

      <section className="report-section">
        <div className="section-heading"><div><span>03</span><h2>Website and source risk</h2></div><p>Rules applied after deduplication to reduce false confidence from repeated or weak sites.</p></div>
        <div className="risk-summary"><div><small>SOURCE TRUST</small><strong>{credibility?.source_trust_score ?? 0}/100</strong></div><div><small>WEBSITE RISK</small><strong className={`risk-${(credibility?.website_risk_level ?? "Unknown").toLowerCase()}`}>{credibility?.website_risk_level ?? "Unknown"}</strong></div><div><small>SYNDICATION RISK</small><strong>{credibility?.duplicate_or_syndication_risk ?? "Unknown"}</strong></div></div>
        <div className="rule-list">
          <div className={`rule-item ${(credibility?.official_source_count ?? 0) > 0 ? "pass" : "warning"}`}>{(credibility?.official_source_count ?? 0) > 0 ? <Check size={17} /> : <ShieldAlert size={17} />}<span><strong>Official-source confirmation</strong><small>{credibility?.official_source_count ?? 0} official sources identified.</small></span><b>{(credibility?.official_source_count ?? 0) > 0 ? "PASS" : "REVIEW"}</b></div>
          <div className={`rule-item ${(credibility?.independent_source_count ?? 0) >= 2 ? "pass" : "warning"}`}>{(credibility?.independent_source_count ?? 0) >= 2 ? <Check size={17} /> : <ShieldAlert size={17} />}<span><strong>Independent corroboration</strong><small>{credibility?.independent_source_count ?? 0} independent domains after duplicates were removed.</small></span><b>{(credibility?.independent_source_count ?? 0) >= 2 ? "PASS" : "REVIEW"}</b></div>
          {(credibility?.risk_signals ?? []).map((signal) => <div className="rule-item warning" key={signal}><ShieldAlert size={17} /><span><strong>Risk signal</strong><small>{signal}</small></span><b>FLAG</b></div>)}
        </div>
      </section>

      <section className="report-section sources-section">
        <div className="section-heading"><div><span>04</span><h2>Source ledger</h2></div><p>{report.all_evidence.length} usable sources checked. Open any row to inspect the original page.</p></div>
        <div className="source-table">
          <div className="source-row source-head"><span>Publisher</span><span>Evidence type</span><span>Date</span><span>Reliability</span><span>Stance</span><span /></div>
          {report.all_evidence.map((source) => {
            const level = reliability(source);
            const stance = supportIds.has(source.evidence_id) ? "Supports" : contradictIds.has(source.evidence_id) ? "Challenges" : "Context";
            return <a className="source-row" href={source.url} target="_blank" rel="noreferrer" key={source.evidence_id}><strong>{source.publisher || source.root_domain}</strong><span>{source.source_type}</span><span>{source.published_date || "Unknown"}</span><span><i className={`reliability ${level.toLowerCase()}`} />{level}</span><span className={`stance ${stance.toLowerCase()}`}>{stance}</span><ChevronRight size={16} /></a>;
          })}
          {report.all_evidence.length === 0 && <p className="empty-report-row">{evidenceFailure || "No usable evidence sources were retained."}</p>}
        </div>
      </section>

      <section className="report-section audit-section">
        <div className="section-heading"><div><span>05</span><h2>Gonka audit trail</h2></div><p>Response-body IDs and request or trace headers are preserved as separate fields.</p></div>
        <div className="audit-table">
          <div className="audit-row audit-head"><span>Step / model</span><span>Response ID</span><span>Request / trace ID</span><span>Latency</span></div>
          {report.gonka_trace.map((trace, index) => <div className="audit-row" key={`${trace.step_name}-${index}`}><span><strong>{trace.step_name}</strong><small>{trace.requested_model_id}</small></span><code>{trace.response_body_id || "Not returned"}</code><code>{trace.request_id || trace.trace_id || "Not returned"}</code><span className={trace.success ? "trace-ok" : "trace-failed"}>{trace.success ? `${Math.round(trace.latency_ms)} ms` : trace.error_type || "Failed"}</span></div>)}
        </div>
        {report.limitations.length > 0 && <div className="limitations"><strong>LIMITATIONS</strong>{report.limitations.map((item) => <p key={item}>{item}</p>)}</div>}
      </section>
      {notice && <div className="toast" role="status">{notice}</div>}
    </main>
  );
}
