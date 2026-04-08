import os
import shutil
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db, OwnerStatus
from app.services.ocr_engine import process_document
from app.services.solana_bridge import verify_stake, trigger_solana_state_change

router = APIRouter()
TEMP_DIR = "temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)

@router.post("/verify-claim")
async def verify_claim(
    username: str, 
    stake_tx_id: str, 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    # Phase 1: Stake Verification
    if not await verify_stake(stake_tx_id):
        raise HTTPException(status_code=402, detail="Stake verification failed.")

    path = os.path.join(TEMP_DIR, file.filename)
    try:
        # Phase 2: Ephemeral Storage
        with open(path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Phase 2: AI OCR Extraction
        ai_data = await process_document(path)
        if ai_data["confidence"] < 0.90:
            raise HTTPException(status_code=422, detail="Low AI confidence.")

        # Phase 3: Privacy Matching
        owner = db.query(OwnerStatus).filter(OwnerStatus.owner_name == username).first()
        if not owner or ai_data["extracted_name"].lower() != owner.owner_name.lower():
            raise HTTPException(status_code=403, detail="Document name mismatch.")

        # Phase 4: Blockchain Execution
        sol_tx = await trigger_solana_state_change(
            vault_id=owner.id,
            event_type=ai_data["document_type"],
            metadata_hash=ai_data["zk_hash"]
        )

        return {"status": "SUCCESS", "challenge_period": "Started", "tx": sol_tx}

    finally:
        # Phase 4: Purge
        if os.path.exists(path):
            os.remove(path)
            print(f"🗑️ File {file.filename} deleted from memory.")