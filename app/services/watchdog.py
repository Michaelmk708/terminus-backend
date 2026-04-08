import asyncio
from datetime import datetime, timedelta, timezone
from app.core.database import SessionLocal, OwnerStatus
from app.services.notifications import send_whatsapp

async def check_heartbeats():
    while True:
        db = SessionLocal()
        try:
            owners = db.query(OwnerStatus).all()
            now = datetime.now(timezone.utc)

            for owner in owners:
                diff = now - owner.last_seen
                
                # 30 Days: First Warning to Owner
                if diff >= timedelta(days=30) and owner.check_in_count == 0:
                    await send_whatsapp(owner.owner_phone, "30-day check-in: Please log in within 14 days.")
                    owner.check_in_count = 1
                    db.commit()

                # 44 Days (30 + 14): Alert Beneficiary
                if diff >= timedelta(days=44) and owner.check_in_count == 1:
                    await send_whatsapp(owner.beneficiary_phone, f"ALERT: {owner.owner_name} is inactive. Verify status.")
                    owner.check_in_count = 2
                    db.commit()
        finally:
            db.close()
        await asyncio.sleep(86400) # Check once a day