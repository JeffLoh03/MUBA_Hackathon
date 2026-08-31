import { useEffect, useMemo, useState } from "react";
import { analysisStages } from "../data/mockVerification";
import type { StageStatus, VerificationMode, VerificationStatus } from "../types/verification";

export function useVerification() {
  const [status, setStatus] = useState<VerificationStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<VerificationMode>("quick");
  const [error, setError] = useState("");

  useEffect(() => {
    if (status !== "processing") return;
    const timer = window.setInterval(() => {
      setProgress((current) => {
        const next = Math.min(100, current + 1);
        if (next === 100) {
          window.clearInterval(timer);
          window.setTimeout(() => setStatus("complete"), 450);
        }
        return next;
      });
    }, 95);
    return () => window.clearInterval(timer);
  }, [status]);

  const stages = useMemo(() => analysisStages.map((stage) => {
    let stageStatus: StageStatus = "waiting";
    if (progress >= stage.end) stageStatus = "complete";
    else if (progress >= stage.start) stageStatus = "active";
    return { ...stage, status: stageStatus };
  }), [progress]);

  function startVerification() {
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
      setError("");
      setProgress(0);
      setStatus("processing");
    } catch {
      setError("Enter a complete news URL beginning with http:// or https://");
    }
  }

  function reset() {
    setStatus("idle");
    setProgress(0);
    setUrl("");
    setError("");
  }

  return { status, progress, url, setUrl, mode, setMode, error, stages, startVerification, reset };
}
