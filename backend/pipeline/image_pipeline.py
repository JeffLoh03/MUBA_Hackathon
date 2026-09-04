from __future__ import annotations

from typing import Any, Callable

from schemas.models import FactCheckReport, ImageContextAssessment
from services.gonka_client import GonkaCallFailed, GonkaClient, parse_json_object
from services.image_processor import ProcessedImage, process_image
from pipeline.text_pipeline import TextFactCheckPipeline


ProgressCallback = Callable[[str, dict[str, Any]], None]


class ImageFactCheckPipeline:
    def __init__(
        self,
        text_pipeline: TextFactCheckPipeline,
        gonka_client: GonkaClient,
        vision_model_id: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.text_pipeline = text_pipeline
        self.gonka_client = gonka_client
        self.vision_model_id = vision_model_id
        self.progress_callback = progress_callback

    def verify(self, *, image_bytes: bytes, mime_type: str, caption_or_claim: str = "") -> FactCheckReport:
        self._emit(
            "Image validation started",
            {
                "mime_type": mime_type,
                "size_bytes": len(image_bytes),
                "has_caption_or_claim": bool(caption_or_claim.strip()),
            },
        )
        processed = process_image(image_bytes, mime_type)
        self._emit(
            "OCR and EXIF completed",
            {
                "width": processed.width,
                "height": processed.height,
                "ocr_preview": processed.ocr_text[:300],
                "has_exif": bool(processed.exif_summary),
                "ocr_error": processed.ocr_error,
            },
        )
        caption = caption_or_claim.strip()
        visual_description = ""
        visible_text_from_vision = ""
        limitations = [
            "This is not pixel-level deepfake detection.",
            "Missing EXIF metadata is treated as neutral, not suspicious.",
            (
                "Reverse-image matching was not performed. "
                "The result evaluates the claim and context, not pixel-level authenticity."
            ),
        ]
        traces = []

        if self.vision_model_id:
            try:
                self._emit("Vision context analysis started", {"model": self.vision_model_id})
                vision_result = self.gonka_client.describe_image(
                    model_id=self.vision_model_id,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    caption=caption,
                    ocr_text=processed.ocr_text,
                )
                traces.append(vision_result.trace)
                parsed = parse_json_object(vision_result.text)
                visual_description = str(parsed.get("visual_description", ""))
                visible_text_from_vision = str(parsed.get("visible_text", ""))
                self._emit(
                    "Vision context analysis completed",
                    {
                        "model": self.vision_model_id,
                        "visual_description": visual_description[:300],
                        "visible_text": visible_text_from_vision[:300],
                        "request_id": vision_result.trace.request_id,
                        "trace_id": vision_result.trace.trace_id,
                    },
                )
            except (GonkaCallFailed, ValueError) as exc:
                if isinstance(exc, GonkaCallFailed):
                    traces.append(exc.trace)
                limitations.append(f"Vision model unavailable or unsupported; OCR fallback was used: {exc}")
                self._emit(
                    "Vision fallback used",
                    {
                        "reason": str(exc)[:300],
                        "fallback": "OCR, EXIF, and caption/context claim",
                    },
                )
        else:
            limitations.append("GONKA_VISION_MODEL is not configured; OCR and caption fallback was used.")
            self._emit(
                "Vision fallback used",
                {"reason": "GONKA_VISION_MODEL is not configured."},
            )

        combined_text = build_image_claim_text(processed, caption, visible_text_from_vision)
        self._emit(
            "Image context converted to text claim",
            {
                "combined_text_chars": len(combined_text),
                "has_ocr_text": bool(processed.ocr_text.strip()),
                "has_caption": bool(caption),
            },
        )
        if not combined_text.strip():
            assessment = ImageContextAssessment(
                verdict="Insufficient Evidence",
                ocr_text=processed.ocr_text,
                caption_or_claim=caption,
                exif_summary=processed.exif_summary,
                visual_description=visual_description,
                limitations=limitations
                + ["A standalone photograph without text or context needs a caption or claim before verification."],
            )
            return FactCheckReport(
                extracted_claim="",
                extracted_claims=[],
                final_verdict="Insufficient Evidence",
                truth_score=50,
                confidence_score=0,
                concise_explanation="Please provide a caption or contextual claim for this image.",
                all_evidence=[],
                gonka_trace=traces,
                limitations=assessment.limitations,
                image_context_assessment=assessment,
            )

        report = self.text_pipeline.verify(text=combined_text)
        image_verdict = derive_image_verdict(report.final_verdict)
        assessment = ImageContextAssessment(
            verdict=image_verdict,
            ocr_text=processed.ocr_text,
            caption_or_claim=caption,
            exif_summary=processed.exif_summary,
            visual_description=visual_description,
            limitations=limitations + ([processed.ocr_error] if processed.ocr_error else []),
        )
        return report.model_copy(
            update={
                "image_context_assessment": assessment,
                "gonka_trace": traces + report.gonka_trace,
                "limitations": report.limitations + assessment.limitations,
            }
        )

    def _emit(self, stage: str, details: dict[str, Any] | None = None) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(stage, details or {})


def build_image_claim_text(
    processed: ProcessedImage,
    caption: str,
    visible_text_from_vision: str,
) -> str:
    parts = []
    if caption.strip():
        parts.append(f"Caption or contextual claim: {caption.strip()}")
    if processed.ocr_text.strip():
        parts.append(f"OCR text: {processed.ocr_text.strip()}")
    if visible_text_from_vision.strip():
        parts.append(f"Visible text from vision model: {visible_text_from_vision.strip()}")
    if processed.exif_summary:
        parts.append(f"EXIF metadata summary: {processed.exif_summary}")
    return "\n".join(parts)


def derive_image_verdict(final_verdict: str) -> str:
    normalized = final_verdict.lower()
    if normalized in {"true", "mostly true"}:
        return "Context Supported"
    if normalized in {"false", "mostly false"}:
        return "Misleading Caption"
    if "misleading" in normalized or "mixed" in normalized:
        return "Possible Context Mismatch"
    return "Insufficient Evidence"
