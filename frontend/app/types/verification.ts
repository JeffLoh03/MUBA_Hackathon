export type VerificationMode = "quick" | "professional";
export type VerificationStatus = "idle" | "processing" | "complete";
export type StageStatus = "waiting" | "active" | "complete";

export interface AnalysisStage {
  id: "deepseek" | "kimi" | "minimax" | "rules";
  name: string;
  role: string;
  description: string;
  start: number;
  end: number;
}

export interface ScoreMetric {
  label: string;
  weight: number;
  score: number;
  note: string;
}

export interface EvidenceSource {
  publisher: string;
  type: string;
  date: string;
  reliability: "High" | "Medium";
  stance: "Supports" | "Context" | "Challenges";
  url: string;
}
