from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from app.core.database import get_db, OwnerStatus, User

router = APIRouter()

class VaultUpdate(BaseModel):
    owner_username: str
    beneficiary_name: str
    beneficiary_email: EmailStr
    beneficiary_phone: str

@router.post("/update-vault-contacts")
async def update_vault_contacts(request: VaultUpdate, db: Session = Depends(get_db)):
    # Find the owner user
    user = db.query(User).filter(User.username == request.owner_username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Owner not found")

    # Check if a status record exists, if not create one
    status = db.query(OwnerStatus).filter(OwnerStatus.user_id == user.id).first()
    if not status:
        status = OwnerStatus(user_id=user.id, owner_name=user.username)
        db.add(status)

    # Upload/Update the contact information
    status.owner_phone = user.phone # Pulled from User profile
    status.beneficiary_name = request.beneficiary_name
    status.beneficiary_email = request.beneficiary_email
    status.beneficiary_phone = request.beneficiary_phone
    
    db.commit()
    return {
        "status": "success", 
        "message": "Owner and Beneficiary contact info successfully uploaded and linked."
    }