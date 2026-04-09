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
    # Phase 1: Verify the on-chain stake (Spam Protection)
    if not await verify_stake(stake_tx_id):
        raise HTTPException(status_code=402, detail="Valid $50 USDC stake not found.")

    path = os.path.join(TEMP_DIR, file.filename)
    try:
        # Phase 2: Ephemeral Storage
        with open(path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Phase 2: AI OCR Extraction
        ai_data = await process_document(path)
        
        if ai_data["confidence"] < 0.90:
            raise HTTPException(status_code=422, detail="AI confidence too low. Please re-upload a clearer image.")

        # Phase 3: Identity Matching against Web2 Database
        owner = db.query(OwnerStatus).filter(OwnerStatus.owner_name == username).first()
        if not owner or ai_data["extracted_name"].lower() != owner.owner_name.lower():
            raise HTTPException(status_code=403, detail="Name on document does not match Vault Owner.")

        # Phase 4: ZK-Proof to Solana Smart Contract
        sol_tx = await trigger_solana_state_change(
            vault_id=owner.id,
            event_type=ai_data["document_type"],
            metadata_hash=ai_data["zk_hash"]
        )

        return {"status": "SUCCESS", "challenge_period": "Started", "tx": sol_tx}

    finally:
        # Phase 5: The Purge (Compliance with Privacy NFRs)
        if os.path.exists(path):
            os.remove(path)
            print(f"🗑️ [PRIVACY] File {file.filename} deleted from server memory.")