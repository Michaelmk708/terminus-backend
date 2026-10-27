import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, Enum, Text, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class VaultState(str, enum.Enum):
    ACTIVE = "ACTIVE"
    IN_CHALLENGE = "IN_CHALLENGE"
    INCAPACITATED = "INCAPACITATED"
    DECEASED = "DECEASED"
    DISPUTED = "DISPUTED"
    SETTLED = "SETTLED"


class ClaimType(str, enum.Enum):
    DEATH = "DEATH"
    INCAPACITATION = "INCAPACITATION"


class ChallengeStatus(str, enum.Enum):
    PENDING = "PENDING"
    DEFENDED_SLASHED = "DEFENDED_SLASHED"  # Owner responded, claimant stake slashed
    EXPIRED_VALIDATED = "EXPIRED_VALIDATED"  # Owner silent, claim validated
    ARBITRATED = "ARBITRATED"


class Vault(Base):
    __tablename__ = "vaults"

    owner_pubkey = Column(String, primary_key=True, index=True)
    state = Column(Enum(VaultState), default=VaultState.ACTIVE, nullable=False)
    
    # Challenge Rules Configuration
    challenge_period_days = Column(Integer, default=30)  # Default 30-day window
    required_stake_lamports = Column(Float, default=1_000_000_000.0)  # e.g., 1 SOL stake requirement
    
    # Active Challenge Tracking
    active_challenge_id = Column(String, nullable=True)
    challenge_start_time = Column(DateTime, nullable=True)
    challenge_end_time = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime, 
        default=lambda: datetime.now(timezone.utc), 
        onupdate=lambda: datetime.now(timezone.utc)
    )
    trusted_recovery_pubkey = Column(String, nullable=True)
encrypted_share_hash = Column(String, nullable=True)

class VaultChallenge(Base):
    __tablename__ = "vault_challenges"

    id = Column(String, primary_key=True, index=True)  # UUID
    vault_owner = Column(String, ForeignKey("vaults.owner_pubkey"), nullable=False)
    claimant_pubkey = Column(String, nullable=False)
    claim_type = Column(Enum(ClaimType), nullable=False)
    
    # Evidence & Verification
    proof_job_id = Column(String, nullable=False)  # Refers to OracleJob ID
    ocr_confidence = Column(Float, nullable=False)
    zk_proof_hash = Column(String, nullable=False)
    
    # Financial Staking & Slashing
    staked_amount_lamports = Column(Float, nullable=False)
    slashed = Column(Boolean, default=False)
    slashed_recipient = Column(String, nullable=True)  # Recipient if slashed
    
    status = Column(Enum(ChallengeStatus), default=ChallengeStatus.PENDING, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)