"use client";

import { Check, CheckCircle2, ChevronRight, Copy, Download, ExternalLink, RotateCcw, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { evidenceSources, modelFindings, scoreMetrics } from "../data/mockVerification";
import type { VerificationMode } from "../types/verification";

interface ReportViewProps { mode: VerificationMode; url: string; onReset: () => void; }

export function ReportView({ mode, url, onReset }: ReportViewProps) {
  const [notice, setNotice] = useState("");
  const sourceCount = mode === "quick" ? 5 : 18;
  const score = Math.round(scoreMetrics.reduce((total, metric) => total + metric.score * (metric.weight / 100), 0));

  async function copyReport() {
    await navigator.clipboard.writeText(`VERITY DESK REPORT\nVerdict: Likely true (${score}/100)\nSource: ${url}\nSources checked: ${sourceCount}\nCaveat: The accompanying image predates the reported event.`);
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
        <div className="report-meta"><span>CASE 01428</span><span>COMPLETED 31 AUG 2026 · 22:48 MYT</span></div>
        <div className="verdict-grid">
          <div><p className="eyebrow">Final assessment</p><h1>Likely true,<br />with context.</h1><p className="verdict-summary">The central event is supported by reliable, independent evidence. However, the article&apos;s image is authentic but comes from an earlier related event and may mislead readers about timing.</p></div>
          <div className="score-block">
            <div className="score-ring" style={{ "--score": score } as React.CSSProperties}><span><strong>{score}</strong><small>/ 100</small></span></div>
            <div><strong>Strong evidence</strong><span>{sourceCount} sources reviewed</span><span>4 independent origin chains</span></div>
          </div>
        </div>
        <div className="article-reference"><span>ANALYSED URL</span><a href={url} target="_blank" rel="noreferrer">{url}<ExternalLink size={14} /></a></div>
      </section>

      <section className="report-section">
        <div className="section-heading"><div><span>01</span><h2>Evidence score</h2></div><p>Weighted calculation from the rule-based verification layer.</p></div>
        <div className="metric-list">
          {scoreMetrics.map((metric) => <div className="metric-row" key={metric.label}><div><strong>{metric.label}</strong><small>{metric.weight}% weight</small></div><p>{metric.note}</p><div className="mini-track"><span style={{ width: `${metric.score}%` }} /></div><strong className="metric-score">{metric.score}</strong></div>)}
        </div>
      </section>

      <section className="report-section">
        <div className="section-heading"><div><span>02</span><h2>Model opinions</h2></div><p>Agreement is informative, but never substitutes for reliable evidence.</p></div>
        <div className="model-grid">
          {modelFindings.map((finding) => <article className="model-finding" key={finding.model}><div><span className="model-initial">{finding.model.slice(0, 1)}</span><div><h3>{finding.model}</h3><small>{finding.confidence}% confidence</small></div></div><strong className="finding-verdict"><CheckCircle2 size={16} />{finding.verdict}</strong><p>{finding.note}</p></article>)}
        </div>
      </section>

      <section className="report-section">
        <div className="section-heading"><div><span>03</span><h2>Rule review</h2></div><p>Fixed checks applied after the model analysis.</p></div>
        <div className="rule-list">
          <div className="rule-item pass"><Check size={17} /><span><strong>Official-source confirmation</strong><small>Primary statement found and matched to the event.</small></span><b>PASS</b></div>
          <div className="rule-item pass"><Check size={17} /><span><strong>Independent corroboration</strong><small>Copied wire reports counted as one original source.</small></span><b>PASS</b></div>
          <div className="rule-item warning"><ShieldAlert size={17} /><span><strong>Image recency</strong><small>Context image predates the claim by 14 months.</small></span><b>-8 PTS</b></div>
        </div>
      </section>

      <section className="report-section sources-section">
        <div className="section-heading"><div><span>04</span><h2>Source ledger</h2></div><p>{sourceCount} sources checked · showing the five most relevant.</p></div>
        <div className="source-table">
          <div className="source-row source-head"><span>Publisher</span><span>Evidence type</span><span>Date</span><span>Reliability</span><span>Stance</span><span /></div>
          {evidenceSources.map((source) => <a className="source-row" href={source.url} target="_blank" rel="noreferrer" key={source.publisher}><strong>{source.publisher}</strong><span>{source.type}</span><span>{source.date}</span><span><i className={`reliability ${source.reliability.toLowerCase()}`} />{source.reliability}</span><span className={`stance ${source.stance.toLowerCase()}`}>{source.stance}</span><ChevronRight size={16} /></a>)}
        </div>
        {mode === "professional" && <p className="additional-sources">+13 additional sources are included in the exported report.</p>}
      </section>
      {notice && <div className="toast" role="status">{notice}</div>}
    </main>
  );
}
