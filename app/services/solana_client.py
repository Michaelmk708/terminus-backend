"""
solana_client.py
────────────────────────────────────────────────────────────────────
Terminus AI Oracle: Solana Smart Contract Integration

This module constructs, signs, and sends transactions to the Solana
Devnet Terminus smart contract. The AI Oracle keypair signs the
trigger_challenge instruction after verifying a medical document.

ARCHITECTURE:
  1. AI Oracle has a local keypair (loaded from ORACLE_KEYPAIR_PATH env)
  2. After OCR verification, we build a trigger_challenge TX
  3. TX is signed by both AI Oracle and Claimant
  4. Claimant's stake is transferred to the Vault PDA
  5. Vault state transitions: Active → ChallengePeriod

CRITICAL CONSTRAINTS:
  • trigger_challenge requires TWO signers: ai_oracle + claimant
  • Claimant must have enough lamports to cover stake + tx fees
  • Vault must be Active to accept a challenge
  • All pubkey formats must be base58 (Solana standard)

────────────────────────────────────────────────────────────────────
"""

import os
import json
import asyncio
import base64
from typing import Optional, Dict, Any
from datetime import datetime
from dotenv import load_dotenv

# Solana Python SDK
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.rpc.requests import GetAccountInfo
from solders.transaction import Transaction, VersionedTransaction
from solders.message import MessageV0
from solders.instruction import Instruction, AccountMeta
from solders.system_program import TransferParams, transfer
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Processed, Confirmed
from solana.rpc.types import TxOpts
from app.services.retry_utils import async_retry, RPC_RETRY_CONFIG

# Anchor IDL parsing
from construct import Container
import struct

load_dotenv()

# ════════════════════════════════════════════════════════════════════
#  CONFIGURATION & SETUP
# ════════════════════════════════════════════════════════════════════

# Solana Network Configuration
SOLANA_RPC_URL = os.getenv(
    "SOLANA_RPC_URL",
    "https://api.devnet.solana.com"
)
SOLANA_NETWORK = os.getenv("SOLANA_NETWORK", "devnet")

# Terminus Smart Contract
TERMINUS_PROGRAM_ID = os.getenv(
    "TERMINUS_PROGRAM_ID",
    "Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS"
)

# AI Oracle Keypair (CRITICAL: never expose in frontend)
ORACLE_KEYPAIR_PATH = os.getenv(
    "ORACLE_KEYPAIR_PATH",
    "./terminus_oracle_keypair.json"
)

# Challenge Parameters
CHALLENGE_STAKE_LAMPORTS = int(os.getenv("CHALLENGE_STAKE_LAMPORTS", "5000000"))  # 0.005 SOL
CHALLENGE_TIMEOUT_SECONDS = int(os.getenv("CHALLENGE_TIMEOUT_SECONDS", "2592000"))  # 30 days

# Vault PDA seed
VAULT_SEED = b"vault"

# VaultState enum (must match Rust contract)
class VaultState:
    ACTIVE = 0
    CHALLENGE_PERIOD = 1
    INCAPACITATED = 2
    DECEASED = 3


# ════════════════════════════════════════════════════════════════════
#  ERROR HANDLING
# ════════════════════════════════════════════════════════════════════

class SolanaClientError(Exception):
    """Base exception for Solana client errors."""
    pass


class InsufficientFundsError(SolanaClientError):
    """Claimant does not have enough lamports for stake + fees."""
    pass


class VaultNotActiveError(SolanaClientError):
    """Vault is not in Active state to accept a challenge."""
    pass


class InvalidPubkeyError(SolanaClientError):
    """Invalid Solana public key format."""
    pass


class TransactionFailedError(SolanaClientError):
    """Transaction was rejected or failed on-chain."""
    pass


# ════════════════════════════════════════════════════════════════════
#  ORACLE KEYPAIR MANAGEMENT
# ════════════════════════════════════════════════════════════════════

def _load_oracle_keypair() -> Keypair:
    """
    Loads the AI Oracle's keypair from ORACLE_KEYPAIR_PATH.
    
    Expected format: JSON array of 64 bytes (u8[64])
    Generated via: solana-keygen new -o terminus_oracle_keypair.json
    
    Raises:
        FileNotFoundError: If keypair file does not exist
        ValueError: If keypair format is invalid
    """
    if not os.path.exists(ORACLE_KEYPAIR_PATH):
        raise FileNotFoundError(
            f"🔐 [ORACLE] Keypair not found at {ORACLE_KEYPAIR_PATH}\n"
            f"   Generate via: solana-keygen new -o {ORACLE_KEYPAIR_PATH}"
        )
    
    try:
        with open(ORACLE_KEYPAIR_PATH, 'r') as f:
            secret_bytes = json.load(f)
        
        if not isinstance(secret_bytes, list) or len(secret_bytes) != 64:
            raise ValueError(
                f"Expected JSON array of 64 bytes, got {len(secret_bytes)} bytes"
            )
        
        keypair = Keypair.from_secret_key(bytes(secret_bytes))
        return keypair
    
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(
            f"Invalid keypair JSON at {ORACLE_KEYPAIR_PATH}: {str(e)}"
        )


# ════════════════════════════════════════════════════════════════════
#  VAULT PDA DERIVATION
# ════════════════════════════════════════════════════════════════════

def derive_vault_pda(owner_pubkey: str) -> tuple[str, int]:
    """
    Derives the Vault PDA address from the owner's pubkey.
    
    Seeds: [b"vault", owner_pubkey_bytes]
    Program: TERMINUS_PROGRAM_ID
    
    Args:
        owner_pubkey: Base58-encoded owner public key
        
    Returns:
        Tuple of (vault_pda_base58, bump_seed)
        
    Raises:
        InvalidPubkeyError: If owner_pubkey is not valid base58
    """
    try:
        # Convert base58 string to Pubkey object
        owner_key = Pubkey.from_string(owner_pubkey)
    except Exception as e:
        raise InvalidPubkeyError(
            f"Invalid owner pubkey format: {owner_pubkey}\n{str(e)}"
        )
    
    try:
        program_id = Pubkey.from_string(TERMINUS_PROGRAM_ID)
    except Exception as e:
        raise InvalidPubkeyError(
            f"Invalid program ID: {TERMINUS_PROGRAM_ID}\n{str(e)}"
        )
    
    vault_pda, bump = Pubkey.find_program_address(
        [VAULT_SEED, bytes(owner_key)],
        program_id
    )
    
    return str(vault_pda), bump


# ════════════════════════════════════════════════════════════════════
#  TRANSACTION BUILDING & SIGNING
# ════════════════════════════════════════════════════════════════════

async def _get_account_info(client: AsyncClient, pubkey: str) -> Optional[Dict[str, Any]]:
    """
    Fetches account info from Solana RPC (used for validation).
    
    Args:
        client: AsyncClient connected to Solana RPC
        pubkey: Base58-encoded public key
        
    Returns:
        Account info dict or None if not found
    """
    try:
        response = await client.get_account_info(Pubkey.from_string(pubkey), Processed)
        if response.value:
            return {
                "lamports": response.value.lamports,
                "owner": str(response.value.owner),
                "executable": response.value.executable,
                "data_len": len(response.value.data),
            }
        return None
    except Exception as e:
        print(f"⚠️  [RPC] Error fetching account info for {pubkey}: {str(e)}")
        return None


async def validate_vault_state(client: AsyncClient, vault_pda: str) -> bool:
    """
    Validates that the vault is in ACTIVE state.
    
    Reads the vault account and checks the state field (byte offset 104).
    
    Args:
        client: AsyncClient connected to Solana RPC
        vault_pda: Base58-encoded vault PDA
        
    Returns:
        True if vault is in ACTIVE state, False otherwise
        
    Raises:
        VaultNotActiveError: If vault is not in ACTIVE state
    """
    try:
        response = await client.get_account_info(Pubkey.from_string(vault_pda), Confirmed)
        
        if not response.value or not response.value.data:
            raise VaultNotActiveError(
                f"Vault account {vault_pda} not found or has no data"
            )
        
        # Vault account layout (Anchor discriminator + fields)
        # 8 bytes: Anchor discriminator
        # 32 bytes: owner (Pubkey)
        # 32 bytes: beneficiary (Pubkey)
        # 32 bytes: fiduciary (Pubkey)
        # 32 bytes: ai_oracle (Pubkey)
        # 1 byte: state (VaultState enum)
        # Offset of state = 8 + 32 + 32 + 32 + 32 = 136
        
        STATE_OFFSET = 136
        state_bytes = response.value.data[STATE_OFFSET:STATE_OFFSET + 1]
        
        if not state_bytes:
            raise VaultNotActiveError(
                f"Cannot read state from vault account {vault_pda}"
            )
        
        state = state_bytes[0]
        
        if state != VaultState.ACTIVE:
            state_name = {
                0: "ACTIVE",
                1: "CHALLENGE_PERIOD",
                2: "INCAPACITATED",
                3: "DECEASED"
            }.get(state, f"UNKNOWN({state})")
            
            raise VaultNotActiveError(
                f"Vault {vault_pda} is in {state_name} state, expected ACTIVE"
            )
        
        return True
    
    except Exception as e:
        if isinstance(e, VaultNotActiveError):
            raise
        raise VaultNotActiveError(
            f"Failed to validate vault state: {str(e)}"
        )


async def _validate_claimant_balance(
    client: AsyncClient,
    claimant_pubkey: str,
    required_lamports: int
) -> bool:
    """
    Validates that the claimant has sufficient lamports for stake + fees.
    
    Args:
        client: AsyncClient connected to Solana RPC
        claimant_pubkey: Base58-encoded claimant public key
        required_lamports: Minimum lamports required
        
    Returns:
        True if claimant has sufficient balance
        
    Raises:
        InsufficientFundsError: If claimant balance is insufficient
    """
    try:
        balance_response = await client.get_balance(
            Pubkey.from_string(claimant_pubkey),
            Confirmed
        )
        balance = balance_response.value
        
        if balance < required_lamports:
            raise InsufficientFundsError(
                f"Claimant balance {balance} lamports is less than required "
                f"{required_lamports} lamports (stake {CHALLENGE_STAKE_LAMPORTS} + fees)"
            )
        
        return True
    
    except Exception as e:
        if isinstance(e, InsufficientFundsError):
            raise
        raise InsufficientFundsError(
            f"Failed to validate claimant balance: {str(e)}"
        )


def _build_trigger_challenge_instruction(
    ai_oracle_pubkey: str,
    claimant_pubkey: str,
    vault_pda: str,
    claim_type: int,
    stake_amount: int,
) -> Instruction:
    """
    Builds the trigger_challenge instruction for the Terminus program.
    
    Instruction layout (Anchor):
      1. Signer: ai_oracle (must match vault.ai_oracle)
      2. Signer: claimant (transfers stake)
      3. Mutable: vault_account (state changes, receives stake)
      4. Mutable: system_program
      
    Args:
        ai_oracle_pubkey: Base58 ai_oracle signer
        claimant_pubkey: Base58 claimant signer
        vault_pda: Base58 vault PDA
        claim_type: 1=Medical (incapacitation), 2=Death
        stake_amount: Lamports to stake
        
    Returns:
        Instruction object ready to sign
    """
    program_id = Pubkey.from_string(TERMINUS_PROGRAM_ID)
    system_program_id = Pubkey.from_string("11111111111111111111111111111111")
    
    ai_oracle = Pubkey.from_string(ai_oracle_pubkey)
    claimant = Pubkey.from_string(claimant_pubkey)
    vault = Pubkey.from_string(vault_pda)
    
    # Instruction accounts (order matters!)
    accounts = [
        AccountMeta(ai_oracle, is_signer=True, is_writable=False),
        AccountMeta(claimant, is_signer=True, is_writable=True),
        AccountMeta(vault, is_signer=False, is_writable=True),
        AccountMeta(system_program_id, is_signer=False, is_writable=False),
    ]
    
    # Instruction data: discriminator + claim_type + stake_amount
    # Anchor discriminator: first 8 bytes of SHA256("global:trigger_challenge")
    import hashlib
    discriminator = hashlib.sha256(b"global:trigger_challenge").digest()[:8]
    
    # Encode claim_type (u8) and stake_amount (u64, little-endian)
    data = discriminator + struct.pack("<B", claim_type) + struct.pack("<Q", stake_amount)
    
    return Instruction(program_id, data, accounts)


async def trigger_challenge(
    vault_owner: str,
    claimant_pubkey: str,
    claim_type: int = 2,  # 2 = Death (default)
    stake_amount: Optional[int] = None,
) -> Dict[str, Any]:
    """
    PRIMARY ENTRY POINT for the Backend.
    
    Orchestrates the full flow:
      1. Load AI Oracle keypair
      2. Derive Vault PDA from owner
      3. Validate vault is ACTIVE
      4. Validate claimant has funds
      5. Build trigger_challenge instruction
      6. Sign with AI Oracle + Claimant (via CPI or pre-signed)
      7. Send transaction to Solana RPC
      8. Confirm transaction
      9. Return transaction signature
    
    Args:
        vault_owner: Base58 owner pubkey
        claimant_pubkey: Base58 claimant pubkey (fiduciary or beneficiary)
        claim_type: 1=Medical, 2=Death (default)
        stake_amount: Lamports to stake (default: CHALLENGE_STAKE_LAMPORTS)
        
    Returns:
        {
            "status": "success",
            "tx_signature": "5xQfB...",
            "vault_pda": "...",
            "challenge_started_at": "2026-04-09T...",
            "challenge_expires_at": "...",
        }
        
    Raises:
        SolanaClientError: If any step fails (network, validation, etc.)
    """
    raise SolanaClientError(
        "Direct backend trigger_challenge is disabled in production. "
        "Use the sequential dual-signing flow via /api/vault/{owner}/finalize-challenge."
    )


# ════════════════════════════════════════════════════════════════════
#  UTILS FOR FRONTEND INTEGRATION
# ════════════════════════════════════════════════════════════════════

@async_retry(config=RPC_RETRY_CONFIG, operation_name="solana.get_vault_state")
async def get_vault_state(vault_pda: str) -> Dict[str, Any]:
    """
    Reads the vault account state from Solana.
    
    Used by Frontend to check if trigger_challenge was successful.
    
    Args:
        vault_pda: Base58-encoded vault PDA
        
    Returns:
        {
            "state": 0,  # 0=Active, 1=ChallengePeriod, 2=Incapacitated, 3=Deceased
            "last_heartbeat": 1234567890,
            "challenge_end_time": 1234567895,
            "medical_allowance": 1000000,
            "claim_stake": 5000000,
            "pending_claim_type": 2,
        }
    """
    client = AsyncClient(SOLANA_RPC_URL)
    try:
        response = await client.get_account_info(Pubkey.from_string(vault_pda), Confirmed)
        
        if not response.value or not response.value.data:
            raise ValueError(f"Vault {vault_pda} not found")
        
        # Parse VaultAccount struct
        # Layout: discriminator(8) + owner(32) + beneficiary(32) + fiduciary(32)
        #         + ai_oracle(32) + state(1) + last_heartbeat(8) + challenge_end_time(8)
        #         + medical_allowance(8) + claim_stake(8) + pending_claim_type(1) + bump(1)
        
        data = response.value.data
        
        # Read fields at appropriate offsets
        state_offset = 136
        state = data[state_offset]
        
        last_heartbeat = struct.unpack("<q", data[137:145])[0]
        challenge_end_time = struct.unpack("<q", data[145:153])[0]
        medical_allowance = struct.unpack("<Q", data[153:161])[0]
        claim_stake = struct.unpack("<Q", data[161:169])[0]
        pending_claim_type = data[169]
        
        return {
            "state": state,
            "state_name": ["ACTIVE", "CHALLENGE_PERIOD", "INCAPACITATED", "DECEASED"][state],
            "last_heartbeat": last_heartbeat,
            "challenge_end_time": challenge_end_time,
            "medical_allowance": medical_allowance,
            "claim_stake": claim_stake,
            "pending_claim_type": pending_claim_type,
        }
    
    finally:
        await client.close()


# ════════════════════════════════════════════════════════════════════
#  MAIN & TESTING
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python solana_client.py <vault_owner> <claimant_pubkey> [claim_type]")
        print("Example: python solana_client.py C1... B2... 2")
        sys.exit(1)
    
    vault_owner = sys.argv[1]
    claimant = sys.argv[2]
    claim_type = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    
    result = asyncio.run(trigger_challenge(vault_owner, claimant, claim_type))
    print(f"\n✅ Result: {json.dumps(result, indent=2)}")
