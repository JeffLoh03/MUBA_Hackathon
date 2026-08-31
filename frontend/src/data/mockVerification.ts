import type { AnalysisStage, EvidenceSource, ScoreMetric } from "../types/verification";

export const analysisStages: AnalysisStage[] = [
  { id: "deepseek", name: "DeepSeek", role: "Claim Analyst", description: "Extracting people, events, locations, dates and verifiable sub-claims.", start: 0, end: 24 },
  { id: "kimi", name: "Kimi", role: "Evidence & Image Checker", description: "Cross-checking claims against independent reporting and visual context.", start: 24, end: 59 },
  { id: "minimax", name: "MiniMax", role: "Challenger & Reviewer", description: "Testing contradictions, source duplication and gaps in the evidence.", start: 59, end: 81 },
  { id: "rules", name: "Rules", role: "Deterministic Verification", description: "Applying reliability, independence, recency and corroboration rules.", start: 81, end: 100 },
];

export const scoreMetrics: ScoreMetric[] = [
  { label: "Source reliability", weight: 35, score: 91, note: "Official and established news sources dominate." },
  { label: "Independent support", weight: 25, score: 78, note: "Four independent reporting chains identified." },
  { label: "Date relevance", weight: 15, score: 94, note: "Evidence falls within the event window." },
  { label: "Model agreement", weight: 15, score: 80, note: "All models agree on the core claim; one caveat remains." },
  { label: "Image consistency", weight: 10, score: 62, note: "Image is authentic but predates the reported event." },
];

export const evidenceSources: EvidenceSource[] = [
  { publisher: "Ministry of Communications", type: "Official statement", date: "26 Aug 2026", reliability: "High", stance: "Supports", url: "https://example.com/official" },
  { publisher: "Reuters", type: "News agency", date: "26 Aug 2026", reliability: "High", stance: "Supports", url: "https://example.com/reuters" },
  { publisher: "Associated Press", type: "News agency", date: "27 Aug 2026", reliability: "High", stance: "Supports", url: "https://example.com/ap" },
  { publisher: "Channel News Asia", type: "News organisation", date: "27 Aug 2026", reliability: "High", stance: "Context", url: "https://example.com/cna" },
  { publisher: "Regional Policy Monitor", type: "Research group", date: "28 Aug 2026", reliability: "Medium", stance: "Challenges", url: "https://example.com/policy" },
];

export const modelFindings = [
  { model: "DeepSeek", verdict: "Core claim supported", confidence: 88, note: "Identified three checkable claims. Two are directly supported; one requires contextual qualification." },
  { model: "Kimi", verdict: "Evidence consistent", confidence: 84, note: "Reliable reporting aligns on the event, but the accompanying image is from an earlier related incident." },
  { model: "MiniMax", verdict: "Qualified agreement", confidence: 76, note: "No direct contradiction found. Several outlets repeat the same wire report and were deduplicated." },
];
