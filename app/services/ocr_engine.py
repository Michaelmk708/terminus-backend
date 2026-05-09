import hashlib
from pathlib import Path


def _infer_document_type(extracted_text: str) -> str:
    lower_text = extracted_text.lower()
    if "death" in lower_text and "certificate" in lower_text:
        return "DEATH_CERTIFICATE"
    if "medical" in lower_text and "certificate" in lower_text:
        return "MEDICAL_CERTIFICATE"
    if "hospital" in lower_text or "doctor" in lower_text:
        return "MEDICAL_REPORT"
    return "UNKNOWN"


def _extract_text(file_path: str, suffix: str) -> str:
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise RuntimeError("PDF OCR backend unavailable: install pypdf") from exc

        reader = PdfReader(file_path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()

    try:
        import pytesseract
        from PIL import Image
    except Exception as exc:
        raise RuntimeError(
            "Image OCR backend unavailable: install pytesseract and pillow"
        ) from exc

    with Image.open(file_path) as img:
        return (pytesseract.image_to_string(img) or "").strip()


async def process_document(file_path: str):
    """
    Extracts OCR text and returns verification-safe metadata only.
    No raw PII is returned; only hashes and classification outputs.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Uploaded file does not exist: {file_path}")

    file_bytes = path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    extracted_text = _extract_text(file_path, path.suffix.lower())
    if not extracted_text:
        raise ValueError("OCR failed: no text extracted from document")

    doc_type = _infer_document_type(extracted_text)
    if doc_type == "UNKNOWN":
        raise ValueError("OCR verification failed: unsupported/invalid certificate type")

    normalized_text = " ".join(extracted_text.split())
    zk_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

    confidence = 0.9 if doc_type in {"DEATH_CERTIFICATE", "MEDICAL_CERTIFICATE"} else 0.75
    return {
        "document_type": doc_type,
        "confidence": confidence,
        "document_hash": file_hash,
        "zk_hash": zk_hash,
    }