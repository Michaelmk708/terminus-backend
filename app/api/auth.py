from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from app.services.notifications import generate_otp, send_twilio_otp, verify_otp
from app.services.retry_utils import retry_with_backoff, RPC_RETRY_CONFIG
import asyncio

# 1. THIS LINE FIXES THE ERROR. It must come before the endpoints!
router = APIRouter()

# 2. Your new OTP schemas
class OTPRequest(BaseModel):
    username: str
    phone: str

class OTPVerify(BaseModel):
    username: str
    otp_code: str

# 3. Your new endpoints
@router.post("/request-otp")
async def request_otp(req: OTPRequest):
    otp_code = generate_otp(req.username, req.phone)
    await retry_with_backoff(
        asyncio.to_thread,
        send_twilio_otp,
        req.phone,
        otp_code,
        config=RPC_RETRY_CONFIG,
        operation_name="auth.send_otp_sms",
    )
    return {"message": "OTP sent successfully."}


@router.post("/send-otp")
async def send_otp_compat(req: OTPRequest):
    otp_code = generate_otp(req.username, req.phone)
    await retry_with_backoff(
        asyncio.to_thread,
        send_twilio_otp,
        req.phone,
        otp_code,
        config=RPC_RETRY_CONFIG,
        operation_name="auth.send_otp_sms",
    )
    return {"message": "OTP sent successfully."}

@router.post("/verify-otp")
async def check_otp(req: OTPVerify):
    if verify_otp(req.username, req.otp_code):
        return {"message": "OTP verified successfully."}
    raise HTTPException(status_code=401, detail="Invalid or expired OTP.")

# ... (Any of your other existing auth endpoints go down here) ...