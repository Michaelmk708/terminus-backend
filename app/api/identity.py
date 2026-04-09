from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
import re
import base58
from app.core.database import get_db, User

router = APIRouter()

class IdentityLookupResponse(BaseModel):
    found: bool
    username: str | None = None
    email: str | None = None
    solana_pubkey: str | None = None
    friendly_name: str | None = None
    
    class Config:
        from_attributes = True

class IdentityRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=30, pattern="^[a-zA-Z0-9_-]+$")
    email: EmailStr
    solana_pubkey: str = Field(..., description="Base58-encoded Solana public key")
    full_name: str | None = Field(None, max_length=100)

class IdentityRegisterResponse(BaseModel):
    success: bool
    user_id: int
    username: str
    email: str
    solana_pubkey: str
    message: str

def normalize_identifier(identifier: str) -> tuple[str, str]:
    if "@" in identifier:
        return ("email", identifier.lower().strip())
    else:
        return ("username", identifier.lower().strip())

@router.get("/lookup/{identifier}")
async def lookup_identity(
    identifier: str,
    db: Session = Depends(get_db),
) -> IdentityLookupResponse:
    if not identifier or not identifier.strip():
        return IdentityLookupResponse(found=False)

    identifier_type, normalized_id = normalize_identifier(identifier)
    
    try:
        if identifier_type == "email":
            user = db.query(User).filter(User.email == normalized_id).first()
        else:
            user = db.query(User).filter(User.username == normalized_id).first()
        
        if not user:
            return IdentityLookupResponse(found=False)
        
        return IdentityLookupResponse(
            found=True,
            username=user.username,
            email=user.email,
            solana_pubkey=user.solana_pubkey,
            friendly_name=getattr(user, 'full_name', None),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lookup failed: {str(e)}")

@router.post("/register")
async def register_identity(request: IdentityRegisterRequest, db: Session = Depends(get_db)):
    # Registration logic as provided in identity.py
    existing_user = db.query(User).filter(User.username == request.username.lower()).first()
    if existing_user:
        raise HTTPException(status_code=409, detail="Username taken")
    
    new_user = User(
        username=request.username.lower(),
        email=request.email.lower(),
        solana_pubkey=request.solana_pubkey,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"success": True, "user_id": new_user.id, "username": new_user.username}