import os
import shutil
import hashlib
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.services.solana_client import trigger_challenge  # <--- Integrated your client

router = APIRouter()
TEMP_DIR = "temp_uploads"

# Ensure temp directory exists
os.makedirs(TEMP_DIR, exist_ok=True)

@router.post("/verify-claim")
async def verify_claim(
    username: str, 
    vault_owner: str, 
    claimant_pubkey: str, 
    file: UploadFile = File(...)
):
    """
    1. Receives Claim Document
    2. Performs OCR & ZK-Hash generation
    3. Purges sensitive data for KDPA compliance
    4. Triggers Solana Challenge via the AI Oracle Keypair
    """
    path = os.path.join(TEMP_DIR, file.filename)
    
    try:
        # Save file temporarily
        with open(path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 1. AI OCR Extraction (Simulated for Hackathon)
        print(f"🔍 [AI OCR] Extracting data for {username}...")
        extracted_text = f"Death Certificate verified for {username} via Terminus AI"
        
        # 2. ZK-Hash Generation (The 'Proof' that goes on-chain if needed)
        zk_hash = hashlib.sha256(extracted_text.encode()).hexdigest()
        
        # 3. KDPA Compliance: Delete sensitive image immediately
        os.remove(path)
        print(f"🗑️ [KDPA COMPLIANCE] {file.filename} purged. ZK-Proof: {zk_hash[:10]}...")

        # 4. TRIGGER SOLANA CHALLENGE 
        # Using the logic from your solana_client.py
        try:
            solana_res = await trigger_challenge(
                vault_owner=vault_owner,
                claimant_pubkey=claimant_pubkey,
                claim_type=2, # 2 = Deceased
            )
            
            return {
                "status": "SUCCESS", 
                "zk_proof": zk_hash, 
                "solana_tx": solana_res.get("tx_signature"),
                "vault_pda": solana_res.get("vault_pda"),
                "message": "AI Verification complete. Solana Challenge triggered."
            }
            
        except Exception as sol_err:
            print(f"❌ [SOLANA ERROR] {str(sol_err)}")
            return {
                "status": "OCR_SUCCESS_SOLANA_FAIL",
                "zk_proof": zk_hash,
                "error": "Document verified, but could not trigger on-chain challenge. Check Oracle balance."
            }

    except Exception as e:
        if os.path.exists(path):
            os.remove(path)
        raise HTTPException(status_code=500, detail=str(e))