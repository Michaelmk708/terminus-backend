import os
import tempfile
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from app.services.ocr_engine import process_document

router = APIRouter()
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}

@router.post("/verify-claim")
async def verify_claim(
    username: str = Form(...),
    vault_owner: str = Form(...),
    claimant_pubkey: str = Form(...),
    file: UploadFile = File(...)
):
    """
    1. Receives Claim Document
    2. Performs OCR & ZK-Hash generation
    3. Purges sensitive data for KDPA compliance
    4. Triggers Solana Challenge via the AI Oracle Keypair
    """
    temp_path = None
    extracted = None
    try:
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(status_code=400, detail="Unsupported file type")

        suffix = os.path.splitext(file.filename or "")[1].lower() or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_SIZE_BYTES:
                    raise HTTPException(status_code=413, detail="File too large (max 10MB)")
                temp_file.write(chunk)

        extracted = await process_document(temp_path)

        return {
            "status": "VERIFIED",
            "username": username,
            "vault_owner": vault_owner,
            "claimant_pubkey": claimant_pubkey,
            "document_type": extracted["document_type"],
            "confidence": extracted["confidence"],
            "zk_proof": extracted["zk_hash"],
            "document_hash": extracted["document_hash"],
            "message": "Document verification complete. Submit dual-sign challenge transaction.",
        }

    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"OCR verification failed: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                with open(temp_path, "wb") as wipe_f:
                    wipe_f.write(b"\x00" * 4096)
            finally:
                os.remove(temp_path)
        await file.close()
        del extracted