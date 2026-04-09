"""
api/vault.py
────────────────────────────────────────────────────────────────────
Vault State Management REST API

Endpoints:
  POST   /api/vault/initialize     - Create a vault on-chain
  GET    /api/vault/{owner}        - Get cached vault state (fast)
  POST   /api/vault/{owner}/sync   - Sync from Solana RPC (slow, authoritative)
  POST   /api/vault/{owner}/trigger-challenge - Initiate challenge
  GET    /api/vault/{owner}/state  - Get current vault state name

────────────────────────────────────────────────────────────────────
"""

import asyncio
import os
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.database import get_db, User, VaultState as VaultStateModel, OwnerStatus
from app.services.solana_client import (
    derive_vault_pda,
    trigger_challenge,
    get_vault_state,
    CHALLENGE_TIMEOUT_SECONDS,
    SolanaClientError,
)

router = APIRouter()

# ════════════════════════════════════════════════════════════════════
#  REQUEST/RESPONSE MODELS
# ════════════════════════════════════════════════════════════════════

class InitializeVaultRequest(BaseModel):
    owner_username: str
    owner_email: EmailStr
    owner_pubkey: str = Field(..., description="Owner's Solana pubkey (base58)")
    beneficiary_pubkey: str
    fiduciary_pubkey: str
    deposit_amount: float = Field(..., gt=0, description="Amount in SOL")


class SyncVaultRequest(BaseModel):
    owner_pubkey: str = Field(..., description="Base58 Solana pubkey")
    force_refresh: bool = Field(False, description="Ignore cache, fetch from RPC")


class TriggerChallengeRequest(BaseModel):
    claimant_pubkey: str = Field(..., description="Beneficiary or Fiduciary")
    claim_type: int = Field(2, description="1=Medical, 2=Death")
    stake_amount: Optional[int] = Field(None, description="Lamports (default: 0.005 SOL)")


class VaultStateResponse(BaseModel):
    vault_pda: str
    state: int
    state_name: str
    last_heartbeat: int
    challenge_end_time: int
    medical_allowance: int
    claim_stake: int
    pending_claim_type: int
    last_synced_at: str
    cached: bool = True


class ChallengeStartedResponse(BaseModel):
    status: str
    tx_signature: str
    vault_pda: str
    challenge_expires_at: str
    challenge_duration_seconds: int = CHALLENGE_TIMEOUT_SECONDS


# ════════════════════════════════════════════════════════════════════
#  VAULT STATE INITIALIZATION
# ════════════════════════════════════════════════════════════════════

@router.post("/initialize")
async def initialize_vault(
    request: InitializeVaultRequest,
    db: Session = Depends(get_db),
):
    """
    Initializes a new Terminus vault on-chain.
    
    This endpoint should be called after the Frontend successfully calls
    the initialize_vault smart contract instruction. We record the vault
    metadata in the Supabase cache so future queries are fast.
    
    Flow:
      1. Frontend connects wallet (Phantom)
      2. Frontend calls initialize_vault instruction (with owner signer)
      3. Frontend sends owner's pubkey + metadata to this endpoint
      4. Backend stores vault metadata + creates VaultState cache row
      5. Frontend queries /api/vault/{owner} to confirm initialization
    
    Args:
        request.owner_username: Backend username (for OwnerStatus lookup)
        request.owner_email: Owner's email
        request.owner_pubkey: Owner's Solana pubkey (base58)
        request.beneficiary_pubkey: Beneficiary's Solana address
        request.fiduciary_pubkey: Fiduciary's Solana address
        request.deposit_amount: Initial deposit (SOL)
    
    Returns:
        vault_pda: The derived vault PDA
        message: Confirmation
    """
    try:
        # Step 1: Find or create User record
        user = db.query(User).filter(User.email == request.owner_email).first()
        
        if not user:
            user = User(
                username=request.owner_username,
                email=request.owner_email,
                role="owner",
                solana_pubkey=request.owner_pubkey
            )
            db.add(user)
            db.flush()
        else:
            # PRODUCTION FIX: Update solana_pubkey only if not already set
            if user.solana_pubkey is None:
                user.solana_pubkey = request.owner_pubkey
        
        # Step 2: Derive Vault PDA using owner's actual pubkey
        vault_pda, bump = derive_vault_pda(request.owner_pubkey)
        
        # Step 3: Create or update VaultState cache
        vault_state = db.query(VaultStateModel).filter(
            VaultStateModel.vault_pda == vault_pda
        ).first()
        
        if not vault_state:
            vault_state = VaultStateModel(
                user_id=user.id,
                vault_pda=vault_pda,
                vault_owner=request.owner_pubkey,
                state=0,  # Active
                state_name="Active",
                last_heartbeat=int(datetime.now(timezone.utc).timestamp()),
                challenge_end_time=0,
                medical_allowance=0,
                claim_stake=0,
                pending_claim_type=0,
                last_synced_at=datetime.now(timezone.utc),
            )
            db.add(vault_state)
        
        # Step 4: Update OwnerStatus if it exists
        status = db.query(OwnerStatus).filter(OwnerStatus.user_id == user.id).first()
        if not status:
            status = OwnerStatus(
                user_id=user.id,
                owner_name=request.owner_username,
            )
            db.add(status)
        
        db.commit()
        
        return {
            "status": "success",
            "vault_pda": vault_pda,
            "vault_bump": bump,
            "message": f"Vault initialized at {vault_pda}",
        }
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to initialize vault: {str(e)}")


# ════════════════════════════════════════════════════════════════════
#  VAULT STATE QUERIES (FAST & SLOW)
# ════════════════════════════════════════════════════════════════════

@router.get("/{owner_pubkey}")
async def get_vault_state_cached(
    owner_pubkey: str,
    force_refresh: bool = Query(False, description="Ignore cache, fetch from RPC"),
    db: Session = Depends(get_db),
) -> VaultStateResponse:
    """
    Retrieves vault state from the cache (fast) or RPC (slow).
    
    This is the PRIMARY endpoint for the Frontend Dashboard.
    It returns instantly from the cache if the data is fresh.
    
    Args:
        owner_pubkey: Owner's Solana pubkey (base58)
        force_refresh: If true, bypasses cache and queries Solana RPC
    
    Returns:
        VaultStateResponse with current vault state
        
    Raises:
        404: If vault not found
        503: If RPC is unreachable (and force_refresh=True)
    """
    try:
        # Derive vault PDA
        vault_pda, _ = derive_vault_pda(owner_pubkey)
        
        # Query cache
        vault_state = db.query(VaultStateModel).filter(
            VaultStateModel.vault_pda == vault_pda
        ).first()
        
        if not vault_state:
            raise HTTPException(status_code=404, detail=f"Vault not found for {owner_pubkey}")
        
        # Check if cache is stale (older than 30 seconds)
        last_synced = vault_state.last_synced_at
        if last_synced.tzinfo is None:
            last_synced = last_synced.replace(tzinfo=timezone.utc)
        cache_age = datetime.now(timezone.utc) - last_synced
        is_stale = cache_age > timedelta(seconds=30)
        
        if force_refresh or is_stale:
            print(f"🔄 [VAULT] Syncing state from RPC for {vault_pda[:8]}...")
            try:
                on_chain_state = await get_vault_state(vault_pda)
                
                # Update cache
                vault_state.state = on_chain_state.get("state")
                vault_state.state_name = on_chain_state.get("state_name", "Unknown")
                vault_state.last_heartbeat = on_chain_state.get("last_heartbeat", 0)
                vault_state.challenge_end_time = on_chain_state.get("challenge_end_time", 0)
                vault_state.medical_allowance = on_chain_state.get("medical_allowance", 0)
                vault_state.claim_stake = on_chain_state.get("claim_stake", 0)
                vault_state.pending_claim_type = on_chain_state.get("pending_claim_type", 0)
                vault_state.last_synced_at = datetime.now(timezone.utc)
                
                db.commit()
                cached = False
            
            except Exception as e:
                print(f"   ⚠️  RPC sync failed: {str(e)}")
                if force_refresh:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Cannot reach Solana RPC: {str(e)}"
                    )
                # Fall through to return stale cache
                cached = True
        else:
            cached = True
        
        return VaultStateResponse(
            vault_pda=vault_state.vault_pda,
            state=vault_state.state,
            state_name=vault_state.state_name,
            last_heartbeat=vault_state.last_heartbeat,
            challenge_end_time=vault_state.challenge_end_time,
            medical_allowance=vault_state.medical_allowance,
            claim_stake=vault_state.claim_stake,
            pending_claim_type=vault_state.pending_claim_type,
            last_synced_at=vault_state.last_synced_at.isoformat(),
            cached=cached,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching vault state: {str(e)}")


@router.post("/{owner_pubkey}/sync")
async def sync_vault_state(
    owner_pubkey: str,
    db: Session = Depends(get_db),
):
    """
    Manually trigger a sync from Solana RPC → Database cache.
    
    Used by:
      • Watchdog service (periodic checks)
      • Frontend after on-chain TX confirmation
      • Admin dashboard
    
    Returns:
        Updated vault state
    """
    try:
        vault_pda, _ = derive_vault_pda(owner_pubkey)
        
        # Fetch from RPC
        on_chain_state = await get_vault_state(vault_pda)
        
        # Update cache
        vault_state = db.query(VaultStateModel).filter(
            VaultStateModel.vault_pda == vault_pda
        ).first()
        
        if not vault_state:
            raise HTTPException(status_code=404, detail=f"Vault not found")
        
        vault_state.state = on_chain_state.get("state")
        vault_state.state_name = on_chain_state.get("state_name", "Unknown")
        vault_state.last_heartbeat = on_chain_state.get("last_heartbeat", 0)
        vault_state.challenge_end_time = on_chain_state.get("challenge_end_time", 0)
        vault_state.medical_allowance = on_chain_state.get("medical_allowance", 0)
        vault_state.claim_stake = on_chain_state.get("claim_stake", 0)
        vault_state.pending_claim_type = on_chain_state.get("pending_claim_type", 0)
        vault_state.last_synced_at = datetime.now(timezone.utc)
        
        db.commit()
        
        return {
            "status": "synced",
            "vault_pda": vault_pda,
            "state": vault_state.state_name,
        }
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


# ════════════════════════════════════════════════════════════════════
#  CHALLENGE MANAGEMENT
# ════════════════════════════════════════════════════════════════════

@router.post("/{owner_pubkey}/trigger-challenge")
async def trigger_challenge_endpoint(
    owner_pubkey: str,
    request: TriggerChallengeRequest,
    db: Session = Depends(get_db),
) -> ChallengeStartedResponse:
    """
    Triggers a challenge on the vault (for medical proof or death claim).
    
    Flow:
      1. Backend verifies the medical/death certificate (OCR + AI)
      2. Backend calls this endpoint with claimant's pubkey
      3. AI Oracle keypair signs the trigger_challenge instruction
      4. Claimant's wallet (Frontend) also signs (CPI or pre-signed)
      5. TX is sent to Solana
      6. Vault transitions: Active → ChallengePeriod
      7. Challenge timer starts (5 seconds for demo, 30 days for production)
    
    Args:
        owner_pubkey: Vault owner's pubkey
        claimant_pubkey: Fiduciary (medical) or Beneficiary (death)
        claim_type: 1=Medical, 2=Death
        stake_amount: Optional stake override (default: 0.005 SOL)
    
    Returns:
        Challenge initiated response with expiration time
        
    Raises:
        400: Invalid request
        402: Claimant insufficient funds
        403: Vault not in Active state
        500: RPC or signing error
    """
    try:
        vault_pda, _ = derive_vault_pda(owner_pubkey)
        
        # Call the Solana client
        result = await trigger_challenge(
            vault_owner=owner_pubkey,
            claimant_pubkey=request.claimant_pubkey,
            claim_type=request.claim_type,
            stake_amount=request.stake_amount,
        )
        
        # Update cache
        vault_state = db.query(VaultStateModel).filter(
            VaultStateModel.vault_pda == vault_pda
        ).first()
        
        if vault_state:
            vault_state.state = 1  # ChallengePeriod
            vault_state.state_name = "ChallengePeriod"
            vault_state.challenge_end_time = int(
                (datetime.now(timezone.utc) + timedelta(seconds=CHALLENGE_TIMEOUT_SECONDS)).timestamp()
            )
            vault_state.pending_claim_type = request.claim_type
            vault_state.claim_stake = request.stake_amount or 5000000
            vault_state.last_synced_at = datetime.now(timezone.utc)
            db.commit()
        
        return ChallengeStartedResponse(
            status="success",
            tx_signature=result["tx_signature"],
            vault_pda=vault_pda,
            challenge_expires_at=result["challenge_expires_at"],
        )
    
    except SolanaClientError as e:
        db.rollback()
        error_msg = str(e)
        
        if "insufficient" in error_msg.lower():
            raise HTTPException(status_code=402, detail=error_msg)
        elif "not active" in error_msg.lower():
            raise HTTPException(status_code=403, detail=error_msg)
        elif "invalid" in error_msg.lower():
            raise HTTPException(status_code=400, detail=error_msg)
        else:
            raise HTTPException(status_code=500, detail=error_msg)
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Challenge trigger failed: {str(e)}")
