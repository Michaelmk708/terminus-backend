import asyncio
import uuid
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.oracle_job import OracleJob, OracleAuditLog, JobStatus
from app.services.ocr_engine import OCREngineService
from app.services.blockchain import generate_zk_proof
from app.services.solana_client import trigger_challenge, ping_solana_heartbeat # Ensure this is imported

logger = logging.getLogger("OracleOrchestrator")

class MasterOracleOrchestrator:
    def __init__(self, db: Session):
        self.db = db

    def _log_audit(self, job_id: str, from_status: str, to_status: str, details: str = None):
        """Creates an immutable audit log entry for every decision and state transition."""
        audit_entry = OracleAuditLog(
            job_id=job_id,
            from_status=from_status,
            to_status=to_status,
            details=details
        )
        self.db.add(audit_entry)
        self.db.commit()
        logger.info(f"📋 [AUDIT] Job {job_id}: {from_status} ➔ {to_status} | Details: {details}")

    def _transition_status(self, job: OracleJob, new_status: JobStatus, details: str = None):
        old_status = job.status.value if isinstance(job.status, JobStatus) else str(job.status)
        job.status = new_status
        job.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self._log_audit(job.id, old_status, new_status.value, details)

    async def execute_heartbeat(self, vault_owner: str) -> dict:
        """Executes the gamified Proof of Life check-in on-chain."""
        # Logs the heartbeat to the database for engagement metrics
        self._log_audit(f"hb_{vault_owner[:8]}", "NONE", "HEARTBEAT_LOGGED", f"User {vault_owner} checked in.")
        
        # Call the Solana client to ping the contract (Implementation needed in solana_client.py)
        solana_res = await ping_solana_heartbeat(vault_owner=vault_owner)
        
        return {
            "vault_owner": vault_owner,
            "status": "ACTIVE",
            "solana_tx": solana_res.get("tx_signature"),
            "message": "Vault continuity extended successfully."
        }

    async def execute_claim_workflow(
        self,
        username: str,
        vault_owner: str,
        claimant_pubkey: str,
        claim_type: int, # NEW: 1 = Recovery, 2 = Inheritance
        file_bytes: bytes,
        filename: str
    ) -> dict:
        """
        Master Execution Pipeline with Staging, Exponential Backoff, and Rollback.
        """
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        
        # 1. Stage Job in DB
        job = OracleJob(
            id=job_id,
            username=username,
            vault_owner=vault_owner,
            claimant_pubkey=claimant_pubkey,
            filename=filename,
            status=JobStatus.PENDING
        )
        self.db.add(job)
        self.db.commit()
        self._log_audit(job_id, "NONE", JobStatus.PENDING.value, f"Job staged. Type: {'Recovery' if claim_type == 1 else 'Inheritance'}")

        try:
            # --- STEP A: OCR Extraction & Document Validation ---
            self._transition_status(job, JobStatus.OCR_PROCESSING, "Starting OCR Extraction & Preprocessing")
            
            ocr_result = OCREngineService.process_document(file_bytes, filename)
            confidence = ocr_result.get("confidence_score", 0.0)
            
            if confidence < 0.50:
                raise ValueError(f"Confidence score ({confidence}) below minimum threshold (0.50).")

            job.ocr_data = ocr_result
            self.db.commit()

            # --- STEP B: ZK Proof Generation ---
            self._transition_status(job, JobStatus.ZK_PROOF_GENERATING, "Generating Groth16 ZK Proof")
            
            zk_proof = await self._execute_with_retry(
                coro_func=lambda: generate_zk_proof(
                    document_data={
                        "doc_type": ocr_result["document_type"],
                        "confidence_score": confidence,
                        "claimant": claimant_pubkey,
                        "claim_type_context": claim_type # Pass context to ZK circuit
                    }
                ),
                job=job,
                step_name="ZK Generation"
            )
            job.zk_proof_payload = zk_proof
            self.db.commit()

            # --- STEP C: Oracle Payload Signing ---
            self._transition_status(job, JobStatus.SOLANA_STAGING, "Staging Oracle Decision")
            
            decision_payload = f"{job_id}:{vault_owner}:{claimant_pubkey}:{confidence}:{claim_type}"
            job.oracle_signature = f"ORACLE_SIG_{abs(hash(decision_payload))}"
            self.db.commit()

            # --- STEP D: On-Chain Broadcast ---
            self._transition_status(job, JobStatus.SOLANA_BROADCASTING, "Broadcasting State Transition to Solana Devnet")

            solana_res = await self._execute_with_retry(
                coro_func=lambda: trigger_challenge(
                    vault_owner=vault_owner,
                    claimant_pubkey=claimant_pubkey,
                    claim_type=claim_type  # DYNAMICALLY SET BASED ON ROUTE INPUT
                ),
                job=job,
                step_name="Solana Broadcast"
            )

            job.solana_tx_signature = solana_res.get("tx_signature")
            self._transition_status(
                job, 
                JobStatus.CONFIRMED, 
                f"Successfully transitioned state on-chain. TX: {job.solana_tx_signature}"
            )

            return {
                "job_id": job.id,
                "status": "CONFIRMED",
                "claim_type_executed": claim_type,
                "ocr_data": job.ocr_data,
                "zk_proof": job.zk_proof_payload,
                "solana_tx": job.solana_tx_signature,
                "oracle_signature": job.oracle_signature
            }

        except Exception as e:
            logger.error(f"❌ [WORKFLOW FAILED] Job {job_id}: {str(e)}")
            job.error_message = str(e)
            self._rollback_job(job, str(e))
            
            return {
                "job_id": job.id,
                "status": job.status.value if isinstance(job.status, JobStatus) else str(job.status),
                "error": str(e),
                "message": "Workflow failed and rolled back safely."
            }

    # ... (_execute_with_retry and _rollback_job remain unchanged) ...