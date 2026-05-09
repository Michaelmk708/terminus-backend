from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from app.core.database import get_db, OwnerStatus, User
from app.services.notifications import verify_otp

router = APIRouter()

class VaultUpdate(BaseModel):
    owner_username: str
    beneficiary_name: str
    beneficiary_email: EmailStr
    beneficiary_phone: str
    otp_code: str # Required OTP token 

@router.post("/update-vault-contacts")
async def update_vault_contacts(request: VaultUpdate, db: Session = Depends(get_db)):
    # 1. Route Protection: Require valid OTP token 
    if not verify_otp(request.owner_username, request.otp_code):
        raise HTTPException(status_code=401, detail="Invalid OTP. Update denied.")

    # ... (proceed with updating vault contacts as written previously) ...
    return {"status": "success", "message": "Contacts updated securely."}

class PanicRequest(BaseModel):
    owner_username: str
    otp_code: str # Required OTP token 

@router.post("/panic-button")
async def panic_button(request: PanicRequest):
    # 1. Route Protection: Require valid OTP token 
    if not verify_otp(request.owner_username, request.otp_code):
        raise HTTPException(status_code=401, detail="Invalid OTP. Panic action denied.")
    
    # 2. OTP is the required step-up gate before frontend builds panic tx.
    print(f"⚡ [PANIC BUTTON] OTP verified for {request.owner_username}.")
    return {
        "status": "authorized",
        "message": "OTP verified. Frontend may now build and send panic_button transaction.",
    }