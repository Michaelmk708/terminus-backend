import asyncio
from datetime import datetime, timezone
from app.core.database import SessionLocal, OwnerStatus
from app.services.notifications import send_email, send_sms

async def check_heartbeats():
    print("🐕 [WATCHDOG] Heartbeat monitor started...")
    
    while True:
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            vaults = db.query(OwnerStatus).all()

            for vault in vaults:
                # Calculate how many days since they last clicked the link
                days_offline = (now - vault.last_seen.replace(tzinfo=timezone.utc)).days

                # ---------------------------------------------------------
                # 30-DAY WARNING: Alert the Owner
                # ---------------------------------------------------------
                if days_offline >= 30 and vault.check_in_count == 0:
                    subject = "Action Required: Terminus Vault Check-in"
                    body = f"Hello {vault.owner_name}, it has been 30 days since your last check-in. Please log in to your Terminus dashboard to confirm you are okay."
                    
                    # Fetch owner email from the User table relationship
                    owner_email = vault.user.email 
                    
                    send_email(owner_email, subject, body)
                    if vault.owner_phone:
                        send_sms(vault.owner_phone, body)
                    
                    # Update state so we don't spam them tomorrow
                    vault.check_in_count = 1
                    db.commit()

                # ---------------------------------------------------------
                # 44-DAY ALERT: Notify the Beneficiary
                # ---------------------------------------------------------
                elif days_offline >= 44 and vault.check_in_count == 1:
                    subject = "Terminus Vault: Beneficiary Claim Authorized"
                    body = f"Alert: {vault.owner_name} has missed their 44-day proof of life check-in. You are now authorized to initiate a claim at terminus.app/claim."
                    
                    if vault.beneficiary_email:
                        send_email(vault.beneficiary_email, subject, body)
                    if vault.beneficiary_phone:
                        send_sms(vault.beneficiary_phone, body)
                    
                    # Update state so we wait for the claim process
                    vault.check_in_count = 2
                    db.commit()

        except Exception as e:
            print(f"❌ [WATCHDOG ERROR] {e}")
        finally:
            db.close()

        # In production, check once a day: await asyncio.sleep(86400)
        # For testing, check every 10 seconds:
        await asyncio.sleep(10)