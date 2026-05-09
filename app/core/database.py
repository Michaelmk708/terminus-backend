from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, BigInteger
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
    solana_pubkey = Column(String, nullable=True, index=True)  # Link to Solana wallet (not unique for testing)
    
    status_record = relationship("OwnerStatus", back_populates="user", uselist=False)
    vault_state = relationship("VaultState", back_populates="user", uselist=False)

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


class VaultState(Base):
    """
    Cache of the on-chain Solana vault state.
    
    This table is synced from the Solana blockchain so the UI can load
    instantly without spamming RPC nodes. Populated by:
      1. /api/vault/sync endpoint (periodic polling)
      2. Event listeners (future: websocket subscriptions)
    
    VaultState enum values:
      0 = Active
      1 = ChallengePeriod
      2 = Incapacitated
      3 = Deceased
    """
    __tablename__ = "vault_state"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    
    # On-chain identifiers
    vault_pda = Column(String, unique=True, index=True)  # Vault account address
    vault_owner = Column(String)  # Owner's Solana pubkey
    
    # Vault state
    state = Column(Integer, default=0)  # 0=Active, 1=ChallengePeriod, 2=Incapacitated, 3=Deceased
    state_name = Column(String, default="Active")  # Human-readable for debugging
    
    # Vault timing
    last_heartbeat = Column(BigInteger)  # Unix timestamp
    challenge_end_time = Column(BigInteger, default=0)  # Unix timestamp
    
    # Vault funds
    medical_allowance = Column(BigInteger, default=0)  # Lamports
    claim_stake = Column(BigInteger, default=0)  # Lamports (pending claim)
    pending_claim_type = Column(Integer, default=0)  # 0=None, 1=Medical, 2=Death
    
    # Sync metadata
    last_synced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_rpc_signature = Column(String, nullable=True)  # Last TX that changed state
    
    user = relationship("User", back_populates="vault_state")


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True, nullable=False)
    phone = Column(String, nullable=False)
    otp_hash = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()