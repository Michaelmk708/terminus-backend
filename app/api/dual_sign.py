"""
api/dual_sign.py
────────────────────────────────────────────────────────────────────
Dual-Signature Transaction Handler

This endpoint is STEP 2 of the Sequential Dual-Signing Flow:

  1. Frontend signs instruction with Phantom (claimant signer)
  2. Frontend sends base64-encoded TX to THIS endpoint
  3. Backend deserializes TX
  4. Backend adds signature from AI_ORACLE keypair
  5. Backend submits fully-signed TX to Solana RPC
  6. Return TX signature to frontend

────────────────────────────────────────────────────────────────────
"""

import base64
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db, VaultState as VaultStateModel
from app.services.solana_client import (
    _load_oracle_keypair,
    derive_vault_pda,
    SOLANA_RPC_URL,
)
from app.services.retry_utils import retry_with_backoff, RPC_RETRY_CONFIG

# For transaction handling
try:
    from solders.transaction import VersionedTransaction, Transaction
    from solana.rpc.async_api import AsyncClient
except ImportError:
    raise ImportError(
        "solders library not installed. Run: pip install solders"
    )

router = APIRouter()


def _cosign_versioned_tx(tx: VersionedTransaction, oracle_keypair):
    message = tx.message
    num_required = message.header.num_required_signatures
    signer_keys = [str(k) for k in message.account_keys[:num_required]]
    oracle_pubkey = str(oracle_keypair.pubkey())

    if oracle_pubkey not in signer_keys:
        raise HTTPException(
            status_code=400,
            detail="Oracle pubkey is not a required signer in transaction message",
        )

    oracle_index = signer_keys.index(oracle_pubkey)
    claimant_signed = any(str(sig) != "1111111111111111111111111111111111111111111111111111111111111111" for sig in tx.signatures)
    if not claimant_signed:
        raise HTTPException(
            status_code=403,
            detail="No claimant signature detected in partially signed transaction",
        )

    signatures = list(tx.signatures)
    oracle_signature = oracle_keypair.sign_message(bytes(message))
    signatures[oracle_index] = oracle_signature
    return VersionedTransaction.populate(message, signatures)


def _cosign_legacy_tx(tx: Transaction, oracle_keypair):
    message = tx.message
    num_required = message.header.num_required_signatures
    signer_keys = [str(k) for k in message.account_keys[:num_required]]
    oracle_pubkey = str(oracle_keypair.pubkey())

    if oracle_pubkey not in signer_keys:
        raise HTTPException(
            status_code=400,
            detail="Oracle pubkey is not a required signer in transaction message",
        )

    signatures = list(tx.signatures)
    claimant_signed = any(str(sig) != "1111111111111111111111111111111111111111111111111111111111111111" for sig in signatures)
    if not claimant_signed:
        raise HTTPException(
            status_code=403,
            detail="No claimant signature detected in partially signed transaction",
        )

    oracle_index = signer_keys.index(oracle_pubkey)
    oracle_signature = oracle_keypair.sign_message(message.serialize())
    signatures[oracle_index] = oracle_signature
    return Transaction.populate(message, signatures)

# ════════════════════════════════════════════════════════════════════
#  REQUEST/RESPONSE MODELS
# ════════════════════════════════════════════════════════════════════


class DualSignRequest(BaseModel):
    claimant_signed_tx_base64: str = (
        "Base64-encoded transaction signed by claimant (Phantom)"
    )


class DualSignResponse(BaseModel):
    status: str
    tx_signature: str
    oracle_signed: bool
    submitted_to_solana: bool


# ════════════════════════════════════════════════════════════════════
#  DUAL-SIGN ENDPOINT
# ════════════════════════════════════════════════════════════════════


@router.post("/{owner_pubkey}/finalize-challenge", response_model=DualSignResponse)
async def finalize_challenge_with_oracle_signature(
    owner_pubkey: str,
    request: DualSignRequest,
    db: Session = Depends(get_db),
) -> DualSignResponse:
    """
    STEP 2 OF DUAL-SIGN FLOW:

    Receives a partially-signed transaction from frontend (claimant signed),
    adds the oracle signature (AI_ORACLE), and submits to Solana.

    Frontend has already:
      1. Built trigger_challenge instruction
      2. Signed with Phantom wallet (claimant)
      3. Serialized to base64
      4. Sent here via POST

    This endpoint will:
      1. Deserialize the base64 TX
      2. Verify claimant signature exists
      3. Add oracle signature
      4. Submit to Solana RPC
      5. Return TX signature to frontend

    Args:
        owner_pubkey: Vault owner's pubkey (for audit trail)
        request.claimant_signed_tx_base64: Partially-signed TX

    Returns:
        DualSignResponse with final TX signature

    Raises:
        400: Invalid TX format
        403: Claimant not actually signer
        500: Solana RPC error
    """
    print(
        f"\n⛓️  [DUAL-SIGN] Step 2: Receiving claimant-signed TX from frontend"
    )
    print(f"    Owner: {owner_pubkey[:8]}...{owner_pubkey[-4:]}")

    try:
        # ════════════════════════════════════════════════════════════════
        #  STEP 1: Deserialize the transaction from frontend
        # ════════════════════════════════════════════════════════════════

        print("  [1/5] Deserializing transaction from base64...")

        try:
            tx_bytes = base64.b64decode(request.claimant_signed_tx_base64)
            print(f"       ✓ Decoded {len(tx_bytes)} bytes")
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid base64 encoding: {str(e)}",
            )

        # Try to parse as VersionedTransaction first (modern), fallback to Transaction
        try:
            # Attempt VersionedTransaction (supports newer features)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            print("       ✓ Parsed as VersionedTransaction")
            is_versioned = True
        except Exception:
            try:
                # Fallback to regular Transaction
                tx = Transaction.from_bytes(tx_bytes)
                print("       ✓ Parsed as Transaction")
                is_versioned = False
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not parse transaction: {str(e)}",
                )

        # ════════════════════════════════════════════════════════════════
        #  STEP 2: Load oracle keypair
        # ════════════════════════════════════════════════════════════════

        print("  [2/5] Loading AI Oracle keypair...")

        try:
            oracle_keypair = _load_oracle_keypair()
            oracle_pubkey = str(oracle_keypair.pubkey())
            print(f"       ✓ Oracle: {oracle_pubkey[:8]}...{oracle_pubkey[-4:]}")
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to load oracle keypair: {str(e)}",
            )

        # ════════════════════════════════════════════════════════════════
        #  STEP 3: Add oracle signature
        # ════════════════════════════════════════════════════════════════

        print("  [3/5] Adding oracle signature to transaction...")

        try:
            if is_versioned:
                tx = _cosign_versioned_tx(tx, oracle_keypair)
            else:
                tx = _cosign_legacy_tx(tx, oracle_keypair)
            print("       ✓ Oracle signature inserted at required signer index")

        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to add oracle signature: {str(e)}",
            )

        # ════════════════════════════════════════════════════════════════
        #  STEP 4: Submit to Solana RPC
        # ════════════════════════════════════════════════════════════════

        print("  [4/5] Submitting fully-signed TX to Solana RPC...")
        print(f"       RPC: {SOLANA_RPC_URL}")

        try:
            client = AsyncClient(SOLANA_RPC_URL)

            # Serialize for submission
            tx_bytes = bytes(tx)

            async def _submit_tx():
                return await client.send_raw_transaction(tx_bytes)

            response = await retry_with_backoff(
                _submit_tx,
                config=RPC_RETRY_CONFIG,
                operation_name="dual_sign.submit_raw_transaction",
            )

            # Extract signature
            if hasattr(response, "value"):
                tx_signature = response.value
            else:
                tx_signature = response

            print(f"       ✓ TX submitted: {str(tx_signature)[:16]}...")

            await client.close()

        except Exception as e:
            print(f"       ✗ RPC error: {str(e)}")
            raise HTTPException(
                status_code=503,
                detail=f"Failed to submit to Solana: {str(e)}",
            )

        # ════════════════════════════════════════════════════════════════
        #  STEP 5: Update cache
        # ════════════════════════════════════════════════════════════════

        print("  [5/5] Updating Supabase cache...")

        try:
            vault_pda, _ = derive_vault_pda(owner_pubkey)
            vault_state = db.query(VaultStateModel).filter(
                VaultStateModel.vault_pda == vault_pda
            ).first()

            if vault_state:
                vault_state.state = 1  # ChallengePeriod
                vault_state.state_name = "ChallengePeriod"
                vault_state.last_rpc_signature = str(tx_signature)
                db.commit()
                print("       ✓ Cache updated")
            else:
                print("       ⚠️  Vault not in cache (will be synced on next query)")

        except Exception as e:
            db.rollback()
            print(f"       ⚠️  Cache update failed: {str(e)}")
            # Don't fail the entire request if cache update fails

        print(f"\n✅ [DUAL-SIGN] Challenge triggered successfully!")
        print(f"   TX Signature: {tx_signature}")

        return DualSignResponse(
            status="success",
            tx_signature=str(tx_signature),
            oracle_signed=True,
            submitted_to_solana=True,
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"\n✗ [DUAL-SIGN] Failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Dual-sign operation failed: {str(e)}",
        )
