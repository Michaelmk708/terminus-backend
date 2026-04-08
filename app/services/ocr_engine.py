import asyncio
import hashlib

async def process_document(file_path: str):
    """Phase 2: AI Classification."""
    print(f"🤖 AI Scanning {file_path}...")
    await asyncio.sleep(1.5)
    
    # Mock data for demonstration
    res = {
        "document_type": "DEATH_CERTIFICATE",
        "extracted_name": "aggie", 
        "event_date": "2026-04-09",
        "confidence": 0.98
    }
    
    # Generate the hash for Phase 3
    payload = f"{res['extracted_name']}-{res['event_date']}"
    res["zk_hash"] = hashlib.sha256(payload.encode()).hexdigest()
    
    return res