from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError
import pytesseract


MAX_IMAGE_BYTES = 10 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}


class ImageValidationError(Exception):
    pass


@dataclass(frozen=True)
class ProcessedImage:
    image_bytes: bytes
    mime_type: str
    ocr_text: str
    exif_summary: dict[str, str]
    width: int
    height: int
    ocr_error: str = ""


def validate_image_upload(image_bytes: bytes, mime_type: str) -> None:
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise ImageValidationError("Only JPG, JPEG, PNG, and WEBP images are supported.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ImageValidationError("Image exceeds the 10 MB maximum size.")
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.verify()
    except UnidentifiedImageError as exc:
        raise ImageValidationError("Uploaded file is not a valid image.") from exc


def process_image(image_bytes: bytes, mime_type: str) -> ProcessedImage:
    validate_image_upload(image_bytes, mime_type)
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    ocr_text, ocr_error = extract_ocr_text(image)
    return ProcessedImage(
        image_bytes=image_bytes,
        mime_type=mime_type,
        ocr_text=ocr_text,
        exif_summary=extract_exif_summary(image),
        width=image.width,
        height=image.height,
        ocr_error=ocr_error,
    )


def extract_ocr_text(image: Image.Image) -> tuple[str, str]:
    try:
        return " ".join(pytesseract.image_to_string(image).split()), ""
    except Exception as exc:
        return "", f"OCR unavailable: {exc}"


def extract_exif_summary(image: Image.Image) -> dict[str, str]:
    try:
        exif = image.getexif()
    except Exception:
        return {}
    if not exif:
        return {}
    summary: dict[str, str] = {}
    for tag_id, value in exif.items():
        label = str(tag_id)
        summary[label] = safe_exif_value(value)
        if len(summary) >= 20:
            break
    return summary


def safe_exif_value(value: Any) -> str:
    text = str(value)
    return text[:200]
