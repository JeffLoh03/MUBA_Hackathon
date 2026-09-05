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
  claim_index?: number | null;
  claim?: string | null;
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
  deep_review?: {
    status: "completed" | "partial" | "failed";
    summary: string;
    gaps: string[];
    follow_up_queries: string[];
    initial_source_count: number;
    additional_source_count: number;
    limitations: string[];
  } | null;
  claim_reports?: FactCheckReport[];
  unreviewed_claims?: string[];
  review_status?: "completed" | "partial" | "failed";
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
  verdict: "Context Supported" | "Possible Context Mismatch" | "Misleading Caption" | "Insufficient Evidence" | "Multiple claims reviewed";
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

export interface AuditRunSummary {
  id: string;
  input_type: "text" | "url" | "image";
  input_text: string;
  article_url: string;
  image_name: string;
  mode: VerificationMode;
  status: "running" | "completed" | "failed";
  created_at_utc: string;
  completed_at_utc: string | null;
  extracted_claim: string;
  final_verdict: string | null;
  truth_score: number | null;
  confidence_score: number | null;
  error_message: string | null;
  gonka_call_count: number;
}

export interface StoredGonkaCall extends GonkaTraceRecord {
  sequence_number: number;
}

export interface AuditRunDetail extends Omit<AuditRunSummary, "gonka_call_count"> {
  report: FactCheckReport | null;
  events: Array<ProgressEvent & { sequence_number: number }>;
  gonka_calls: StoredGonkaCall[];
}
