export type VerificationMode = "quick" | "professional";
export type VerificationStatus = "idle" | "processing" | "complete";
export type StageStatus = "waiting" | "active" | "complete";

export interface AnalysisStage {
  id: "claim" | "research" | "models" | "rules";
  name: string;
  role: string;
  description: string;
  start: number;
  end: number;
}

export interface EvidenceItem {
  evidence_id: string;
  title: string;
  url: string;
  root_domain: string;
  publisher: string;
  published_date: string;
  retrieved_at: string;
  excerpt: string;
  source_type: string;
  source_quality: number;
}

export interface SourceCredibilityAssessment {
  source_trust_score: number;
  website_risk_level: "Low" | "Medium" | "High" | "Unknown";
  independent_source_count: number;
  high_quality_source_count: number;
  official_source_count: number;
  missing_date_count: number;
  duplicate_or_syndication_risk: string;
  strongest_sources: string[];
  trust_signals: string[];
  risk_signals: string[];
  summary: string;
}

export interface VerifierOutput {
  model_id?: string;
  verdict: string;
  support_score: number;
  confidence: number;
  supporting_evidence: string[];
  contradicting_evidence: string[];
  context_mismatch: boolean;
  reasoning_summary: string;
  missing_information: string[];
}

export interface GonkaTraceRecord {
  step_name: string;
  requested_model_id: string;
  returned_model_id: string | null;
  response_body_id: string | null;
  request_id: string | null;
  trace_id: string | null;
  timestamp_utc: string;
  latency_ms: number;
  token_usage: Record<string, unknown> | null;
  success: boolean;
  error_type: string | null;
  safe_error_message: string | null;
}

export interface FactCheckReport {
  extracted_claim: string;
  extracted_claims: string[];
  final_verdict: string;
  truth_score: number;
  confidence_score: number;
  concise_explanation: string;
  supporting_evidence: EvidenceItem[];
  contradicting_evidence: EvidenceItem[];
  all_evidence: EvidenceItem[];
  source_credibility_assessment: SourceCredibilityAssessment | null;
  verifier_outputs: VerifierOutput[];
  judge_output: VerifierOutput | null;
  gonka_trace: GonkaTraceRecord[];
  limitations: string[];
  image_context_assessment: ImageContextAssessment | null;
}

export interface ImageContextAssessment {
  verdict: "Context Supported" | "Possible Context Mismatch" | "Misleading Caption" | "Insufficient Evidence";
  ocr_text: string;
  caption_or_claim: string;
  exif_summary: Record<string, string>;
  visual_description: string;
  reverse_image_note: string;
  limitations: string[];
}

export interface ProgressEvent {
  stage: string;
  details: Record<string, unknown>;
  timestamp_utc: string;
}
