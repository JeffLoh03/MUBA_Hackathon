import type { AnalysisStage } from "../types/verification";

export const analysisStages: AnalysisStage[] = [
  { id: "claim", name: "Claim map", role: "Gonka analysis", description: "Extracting checkable claims and planning independent evidence searches.", start: 0, end: 30 },
  { id: "research", name: "Evidence trail", role: "Open-web research", description: "Searching, opening and deduplicating sources before credibility scoring.", start: 30, end: 70 },
  { id: "models", name: "Model review", role: "DeepSeek, MiniMax + Kimi", description: "Two independent reviews followed by Kimi's final evidence-based decision audit.", start: 70, end: 94 },
  { id: "rules", name: "Consensus rules", role: "Deterministic verification", description: "Combining evidence quality, source risk and model agreement.", start: 94, end: 100 },
];
