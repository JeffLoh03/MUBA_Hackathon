import { useMemo, useRef, useState } from "react";
import { analysisStages } from "../data/verificationStages";
import type { FactCheckReport, ProgressEvent, StageStatus, VerificationMode, VerificationStatus } from "../types/verification";

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const SUPPORTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const progressByStage: Record<string, number> = {
  "Image validation started": 2,
  "OCR and EXIF completed": 6,
  "Vision context analysis started": 8,
  "Vision context analysis completed": 10,
  "Vision fallback used": 10,
  "Image context converted to text claim": 11,
  "Input received": 3,
  "Input prepared": 8,
  "Claim extraction started": 12,
  "Gonka call completed": 18,
  "Gonka call failed": 18,
  "Claim extraction fallback used": 22,
  "Claim extraction completed": 24,
  "Search planning started": 27,
  "Search planning completed": 32,
  "Visible browser starting": 34,
  "Visible browser ready": 36,
  "Web search started": 38,
  "Visible browser search opened": 43,
  "Web search completed": 50,
  "Visible browser evidence opened": 54,
  "Evidence processing started": 58,
  "Evidence processing completed": 65,
  "Source credibility scored": 70,
  "Verifier 1 started": 74,
  "Verifier 1 completed": 81,
  "Verifier 2 started": 74,
  "Verifier 2 completed": 90,
  "Fallback verifier started": 74,
  "Fallback verifier completed": 94,
  "Verifier quorum recovery started": 91,
  "Verifier quorum recovery completed": 94,
  "Verifier steps skipped": 94,
  "Disagreement detected": 91,
  "Judge completed": 94,
  "Judge skipped": 94,
  "No judge needed": 94,
  "Consensus started": 96,
  "Consensus completed": 99,
};

function isArticleUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) && value.trim() === value;
  } catch {
    return false;
  }
}

export function useVerification() {
  const [status, setStatus] = useState<VerificationStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [input, setInput] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [mode, setMode] = useState<VerificationMode>("quick");
  const [error, setError] = useState("");
  const [showBrowser, setShowBrowser] = useState(false);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [report, setReport] = useState<FactCheckReport | null>(null);
  const [completedAt, setCompletedAt] = useState("");
  const abortController = useRef<AbortController | null>(null);

  const stages = useMemo(() => analysisStages.map((stage) => {
    let stageStatus: StageStatus = "waiting";
    if (progress >= stage.end) stageStatus = "complete";
    else if (progress >= stage.start) stageStatus = "active";
    return { ...stage, status: stageStatus };
  }), [progress]);

  function selectImage(file: File | null) {
    if (!file) return;
    if (!SUPPORTED_IMAGE_TYPES.has(file.type)) {
      setError("Choose a JPG, PNG, or WEBP image.");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setError("Image must be 10 MB or smaller.");
      return;
    }
    setImage(file);
    setError("");
  }

  async function startVerification() {
    const submittedInput = input.trim();
    if (!submittedInput && !image) {
      setError("Paste a claim or article URL, or attach an image.");
      return;
    }

    abortController.current?.abort();
    abortController.current = new AbortController();
    setError("");
    setProgress(1);
    setEvents([]);
    setReport(null);
    setCompletedAt("");
    setStatus("processing");

    try {
      let response: Response;
      if (image) {
        const formData = new FormData();
        formData.append("image", image);
        formData.append("caption", submittedInput);
        formData.append("mode", mode);
        formData.append("show_browser", String(showBrowser));
        response = await fetch("/api/verify/image/stream", {
          method: "POST",
          body: formData,
          signal: abortController.current.signal,
        });
      } else {
        response = await fetch("/api/verify/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: isArticleUrl(submittedInput) ? "" : submittedInput,
            url: isArticleUrl(submittedInput) ? submittedInput : "",
            mode,
            show_browser: showBrowser,
          }),
          signal: abortController.current.signal,
        });
      }

      if (!response.ok || !response.body) {
        let message = `Verification API returned HTTP ${response.status}.`;
        try {
          const body = await response.json() as { detail?: string };
          if (typeof body.detail === "string") message = body.detail;
        } catch {
          // Keep the safe status-based message when the response is not JSON.
        }
        throw new Error(message);
      }

      await readVerificationStream(response);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setError(requestError instanceof Error ? requestError.message : "Verification failed.");
      setStatus("idle");
    }
  }

  async function readVerificationStream(response: Response) {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const message = JSON.parse(line) as {
          type: "progress" | "report" | "error";
          data: ProgressEvent | { report: FactCheckReport; completed_at_utc: string } | { message: string };
        };
        if (message.type === "progress") {
          const event = message.data as ProgressEvent;
          setEvents((current) => [...current, event]);
          const next = progressByStage[event.stage];
          if (next !== undefined) setProgress((current) => Math.max(current, next));
        } else if (message.type === "report") {
          const result = message.data as { report: FactCheckReport; completed_at_utc: string };
          setReport(result.report);
          setCompletedAt(result.completed_at_utc);
          setProgress(100);
          setStatus("complete");
        } else {
          throw new Error((message.data as { message: string }).message);
        }
      }
      if (done) break;
    }
  }

  function reset() {
    abortController.current?.abort();
    setStatus("idle");
    setProgress(0);
    setInput("");
    setImage(null);
    setError("");
    setEvents([]);
    setReport(null);
    setCompletedAt("");
  }

  const sourceCount = useMemo(() => {
    const evidenceEvent = [...events].reverse().find((event) => event.stage === "Evidence processing completed");
    const value = evidenceEvent?.details.evidence_count;
    return typeof value === "number" ? value : 0;
  }, [events]);

  return {
    status, progress, input, setInput, image, selectImage, removeImage: () => setImage(null),
    mode, setMode, error, stages, startVerification, reset, showBrowser, setShowBrowser,
    events, sourceCount, report, completedAt,
  };
}
