import { Check, Copy } from "lucide-react";
import { useState } from "react";
import type { GonkaTraceRecord } from "../types/verification";

function Identifier({ label, value }: { label: string; value: string | null }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value!);
      setCopied(true);
      setFailed(false);
    } catch { setFailed(true); }
  }
  return <div className="call-identifier"><dt>{label}</dt><dd><code>{value || "Not returned by Gonka"}</code>{value && <button className="copy-id no-print" type="button" onClick={() => void copy()} aria-label={`Copy ${label}`} title={`Copy ${label}`}>{copied ? <Check size={13} /> : <Copy size={13} />}</button>}</dd>{failed && <small role="status">Select the full ID above to copy it.</small>}</div>;
}

export function GonkaCallList({ calls }: { calls: GonkaTraceRecord[] }) {
  if (!calls.length) return <p className="empty-audit">No Gonka calls were recorded for this run.</p>;
  return <div className="gonka-call-list">{calls.map((call, index) => <article className="gonka-call" key={`${call.step_name}-${index}`}>
    <div className="gonka-call-heading"><div><strong>{call.step_name}</strong><span>{call.claim_index ? `Claim ${call.claim_index}` : "Shared step"}</span><small>{call.requested_model_id}</small>{call.returned_model_id && call.returned_model_id !== call.requested_model_id && <small>Returned model: {call.returned_model_id}</small>}</div><span className={call.success ? "trace-ok" : "trace-failed"}>{call.success ? `${Math.round(call.latency_ms)} ms` : call.error_type || "Failed"}</span></div>
    <dl className="call-identifiers"><Identifier label="Response ID" value={call.response_body_id} /><Identifier label="Request ID" value={call.request_id} /><Identifier label="Trace ID" value={call.trace_id} /></dl>
    {call.safe_error_message && <p className="call-error">{call.safe_error_message}</p>}
    <details className="call-metadata"><summary>Timing and token usage</summary><p>{new Date(call.timestamp_utc).toLocaleString()} · {Math.round(call.latency_ms)} ms</p><pre>{call.token_usage ? JSON.stringify(call.token_usage, null, 2) : "Token usage was not returned."}</pre></details>
  </article>)}</div>;
}
