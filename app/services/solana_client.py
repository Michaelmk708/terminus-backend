import os
import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.hash import Hash
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Finalized, Confirmed
from app.core.config import settings

logger = logging.getLogger("SolanaClient")

PRIMARY_RPC = getattr(settings, "SOLANA_RPC_URL", os.getenv("SOLANA_RPC_URL", "https://api.devnet.solana.com"))
SOLANA_RPC_URL = PRIMARY_RPC

FALLBACK_RPCS = [
    os.getenv("SOLANA_FALLBACK_RPC_1", "https://devnet.helius-rpc.com/?api-key=public"),
    "https://api.devnet.solana.com"
]

TERMINUS_PROGRAM_ID = Pubkey.from_string(
    os.getenv("TERMINUS_PROGRAM_ID", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
)
PROGRAM_ID = TERMINUS_PROGRAM_ID


class TransactionFailedError(Exception):
    """Raised when a Solana transaction fails during execution, confirmation, or times out."""
    pass


def _load_oracle_keypair() -> Keypair:
    """Loads Oracle Keypair from env var ORACLE_KEYPAIR_SECRET or returns a generated Keypair."""
    secret = os.getenv("ORACLE_KEYPAIR_SECRET")
    if secret:
        return Keypair.from_base58_string(secret)
    return Keypair()


ORACLE_KEYPAIR = _load_oracle_keypair()


class SolanaClientService:
    @staticmethod
    async def get_rpc_client() -> AsyncClient:
        """Attempts connection to primary RPC, failing over to secondary nodes on error."""
        endpoints = [PRIMARY_RPC] + [url for url in FALLBACK_RPCS if url != PRIMARY_RPC]
        for url in endpoints:
            client = AsyncClient(url, commitment=Finalized)
            try:
                if await client.is_connected():
                    return client
                await client.close()
            except Exception as e:
                await client.close()
                logger.warning(f"⚠️ RPC connection failed for {url}: {str(e)}")

        raise RuntimeError("❌ All Solana RPC nodes are unreachable.")

    @classmethod
    def derive_vault_pda(cls, vault_owner: str) -> Tuple[Pubkey, int]:
        """Derives Program Derived Address (PDA) for a given vault owner."""
        owner_pubkey = Pubkey.from_string(vault_owner)
        return Pubkey.find_program_address(
            [b"vault", bytes(owner_pubkey)],
            PROGRAM_ID
        )

    @classmethod
    async def send_and_confirm_transaction(
        cls,
        ixs: List[Instruction],
        signers: List[Keypair],
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Builds, signs, broadcasts, and confirms a VersionedTransaction."""
        client = await cls.get_rpc_client()
        env = getattr(settings, "ENVIRONMENT", os.getenv("ENVIRONMENT", "development")).lower()

        try:
            latest_blockhash_resp = await client.get_latest_blockhash(commitment=Finalized)
            recent_blockhash = latest_blockhash_resp.value.blockhash

            payer = signers[0].pubkey()
            compiled_msg = MessageV0.try_compile(
                payer=payer,
                instructions=ixs,
                address_lookup_table_accounts=[],
                recent_blockhash=recent_blockhash
            )
            tx = VersionedTransaction(compiled_msg, signers)

            tx_res = await client.send_transaction(tx)
            tx_sig = str(tx_res.value)
            logger.info(f"🚀 [SOLANA BROADCAST] Signature: {tx_sig}")

            confirmed = False
            for attempt in range(1, 15):
                await asyncio.sleep(2)
                status_resp = await client.get_signature_statuses([tx_res.value])
                status = status_resp.value[0]

                if status is not None:
                    if status.err is not None:
                        raise TransactionFailedError(f"On-chain execution error: {status.err}")
                    
                    status_str = str(status.confirmation_status).lower()
                    if status_str in ("confirmed", "finalized"):
                        confirmed = True
                        logger.info(f"✅ [SOLANA FINALIZED] TX {tx_sig} finalized.")
                        break

            if not confirmed:
                raise TransactionFailedError(f"Transaction {tx_sig} was not finalized within timeout.")

            tx_info = await client.get_transaction(
                tx_res.value,
                commitment=Finalized,
                max_supported_transaction_version=0
            )
            logs = tx_info.value.transaction.meta.log_messages if tx_info.value and tx_info.value.transaction.meta else []

            return {
                "tx_signature": tx_sig,
                "status": "FINALIZED",
                "logs": logs,
                "is_mock": False
            }

        except TransactionFailedError as e:
            logger.error(f"❌ [SOLANA TX FAILED]: {str(e)}")
            raise e
        except Exception as e:
            err_str = str(e)
            logger.error(f"❌ [SOLANA ERROR]: {err_str}")
            
            # Strict Gate: Fail immediately in staging/production, forbid mock simulation signatures
            if env in ["production", "staging"]:
                raise TransactionFailedError(f"Real-chain transaction execution failed: {err_str}") from e

            # Dev-only fallback
            if "AccountNotFound" in err_str or "SendTransactionPreflightFailureMessage" in err_str:
                mock_sig = "5DevnetMockTxSignatureForTesting111111111111111111111111111111111111111111111111111"
                logger.warning(f"⚠️ Devnet preflight simulation fallback triggered in dev mode: {mock_sig}")
                return {"tx_signature": mock_sig, "status": "devnet_simulated", "is_mock": True}
            
            raise TransactionFailedError(str(e)) from e
        finally:
            await client.close()

    @classmethod
    async def trigger_challenge(
        cls,
        vault_owner: str,
        claimant_pubkey: str,
        claim_type: int = 2
    ) -> Dict[str, Any]:
        """Triggers an on-chain challenge using VersionedTransactions."""
        vault_pda, _ = cls.derive_vault_pda(vault_owner)
        claimant = Pubkey.from_string(claimant_pubkey)

        instruction_discriminator = bytes([211, 12, 43, 88, 190, 44, 11, 3])
        data = instruction_discriminator + bytes([claim_type])

        accounts = [
            AccountMeta(pubkey=vault_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=claimant, is_signer=False, is_writable=False),
            AccountMeta(pubkey=ORACLE_KEYPAIR.pubkey(), is_signer=True, is_writable=True),
        ]

        challenge_ix = Instruction(PROGRAM_ID, data, accounts)

        return await cls.send_and_confirm_transaction(
            ixs=[challenge_ix],
            signers=[ORACLE_KEYPAIR]
        )

    @classmethod
    async def verify_settlement(cls, vault_owner: str) -> Dict[str, Any]:
        """Reads on-chain account state to confirm settlement state."""
        client = await cls.get_rpc_client()
        vault_pda, _ = cls.derive_vault_pda(vault_owner)

        try:
            account_info = await client.get_account_info(vault_pda, commitment=Finalized)

            if not account_info.value:
                return {"is_settled": False, "reason": "Vault account does not exist"}

            raw_data = account_info.value.data
            is_settled = bool(raw_data[8]) if len(raw_data) > 8 else False

            return {
                "vault_pda": str(vault_pda),
                "is_settled": is_settled,
                "raw_data_len": len(raw_data)
            }
        finally:
            await client.close()
    
    @classmethod
    async def ping_solana_heartbeat(cls, vault_owner: str) -> Dict[str, Any]:
        """Pings the smart contract to reset the Dead Man's Switch timer."""
        vault_pda, _ = cls.derive_vault_pda(vault_owner)
        
        # Placeholder instruction discriminator for PingHeartbeat
        data = bytes([10, 20, 30, 40, 50, 60, 70, 80]) 
        
        accounts = [
            AccountMeta(pubkey=vault_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=ORACLE_KEYPAIR.pubkey(), is_signer=True, is_writable=True),
        ]
        
        ping_ix = Instruction(PROGRAM_ID, data, accounts)
        
        return await cls.send_and_confirm_transaction(
            ixs=[ping_ix],
            signers=[ORACLE_KEYPAIR]
        )


# Module-level aliases to support direct imports across app/api modules
derive_vault_pda = SolanaClientService.derive_vault_pda
trigger_challenge = SolanaClientService.trigger_challenge
verify_settlement = SolanaClientService.verify_settlement
ping_solana_heartbeat = SolanaClientService.ping_solana_heartbeat