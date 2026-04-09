from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

# Defaults to SQLite for local testing if the cloud Postgres isn't ready
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./terminus_web2.db")

# SQLite needs check_same_thread=False
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String)  # Storing the user's phone for SMS alerts
    hashed_password = Column(String)
    role = Column(String) # 'owner' or 'beneficiary'
    
    status_record = relationship("OwnerStatus", back_populates="user", uselist=False)

class OwnerStatus(Base):
    __tablename__ = "owner_status"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    owner_name = Column(String)
    owner_phone = Column(String)
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    beneficiary_name = Column(String)
    beneficiary_email = Column(String) 
    beneficiary_phone = Column(String)
    
    is_beneficiary_confirmed = Column(Boolean, default=False)
    check_in_count = Column(Integer, default=0)

    user = relationship("User", back_populates="status_record")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()