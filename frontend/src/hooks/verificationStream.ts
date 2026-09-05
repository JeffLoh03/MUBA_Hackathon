import type { FactCheckReport, ProgressEvent } from "../types/verification";

export type VerificationMessage =
  | { type: "run"; data: { run_id: string } }
  | { type: "progress"; data: ProgressEvent }
  | { type: "report"; data: { run_id: string; report: FactCheckReport; completed_at_utc: string } };

export async function consumeVerificationStream(response: Response, onMessage: (message: VerificationMessage) => void): Promise<void> {
  if (!response.body) throw new Error("The verification service returned an empty response.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;

  function consumeLine(line: string) {
    if (!line.trim()) return;
    let message: VerificationMessage | { type: "error"; data: { message: string } };
    try { message = JSON.parse(line) as typeof message; }
    catch { throw new Error("The verification service returned an unreadable update. Check the saved run in Transparency."); }
    if (!message || !["run", "progress", "report", "error"].includes(message.type) || !message.data) {
      throw new Error("The verification service returned an unexpected update.");
    }
    if (message.type === "error") throw new Error(message.data.message || "Verification failed.");
    onMessage(message);
    if (message.type === "report") completed = true;
  }

  try {
    while (!completed) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        consumeLine(line);
        if (completed) break;
      }
      if (done) {
        if (!completed) consumeLine(buffer);
        break;
      }
    }
    if (!completed) throw new Error("The connection ended before a report arrived. Check Transparency for the saved run, then retry if needed.");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function overallClaimProgress(stageProgress: number, details: Record<string, unknown>): number {
  const index = details.claim_index;
  const count = details.claim_count;
  if (typeof index !== "number" || typeof count !== "number" || count < 2 || index < 1 || index > count) return stageProgress;
  const fraction = Math.max(0, Math.min(1, (stageProgress - 24) / 75));
  return Math.round(24 + 75 * (index - 1 + fraction) / count);
}
