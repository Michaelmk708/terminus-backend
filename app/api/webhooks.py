from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db, OwnerStatus

router = APIRouter()

@router.post("/whatsapp-callback")
async def beneficiary_reply(request: Request, db: Session = Depends(get_db)):
    data = await request.form()
    message = data.get("Body", "").upper()
    sender = data.get("From") 

    # Find the record where this sender is the Beneficiary
    owner_record = db.query(OwnerStatus).filter(OwnerStatus.beneficiary_phone == sender).first()

    if owner_record:
        if "YES" in message:
            owner_record.is_beneficiary_confirmed = True
            db.commit()
            return {"status": "success", "message": "Beneficiary confirmed."}
        elif "NO" in message:
            return {"status": "declined", "message": "Role declined."}
            
    return {"status": "ignored", "message": "Sender not found."}