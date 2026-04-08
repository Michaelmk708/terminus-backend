import asyncio

async def send_email(to: str, subject: str, body: str):
    print(f"📧 [EMAIL] To: {to} | Subject: {subject}")
    await asyncio.sleep(0.1)
    return True

async def send_whatsapp(phone: str, message: str):
    print(f"🟢 [WHATSAPP] To: {phone} | Message: {message}")
    await asyncio.sleep(0.1)
    return True

# Updated for the single-beneficiary model
async def notify_beneficiary_of_verification(filename: str, confidence: float, b_email: str):
    print(f"🚨 AI VERIFIED: {filename} ({confidence}%)")
    await send_email(
        b_email, 
        "Terminus Alert: Document Verified", 
        f"The AI has verified the document {filename}. Access to the vault is now pending your confirmation."
    )

async def send_beneficiary_invitation(owner_name: str, b_email: str, b_phone: str):
    msg = f"Hello! {owner_name} has named you their Beneficiary on Terminus. Reply YES to this message to confirm."
    await send_email(b_email, "Terminus Invitation", msg)
    await send_whatsapp(b_phone, msg)