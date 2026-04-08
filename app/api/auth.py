from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from app.core.database import get_db, User

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    phone: str
    password: str
    role: str # 'owner' or 'beneficiary'

@router.post("/signup")
async def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        phone=user_data.phone, # Storing user's phone
        hashed_password=pwd_context.hash(user_data.password),
        role=user_data.role
    )
    db.add(new_user)
    db.commit()
    return {"message": f"{user_data.role.capitalize()} account created successfully"}