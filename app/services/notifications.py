import os
import random
import hashlib
from datetime import datetime, timedelta, timezone
import africastalking
from dotenv import load_dotenv
from app.core.database import SessionLocal, OTPChallenge

load_dotenv()

username = os.getenv("AT_USERNAME", "sandbox")
api_key = os.getenv("AT_API_KEY")
OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))

if api_key:
    africastalking.initialize(username, api_key)
    sms = africastalking.SMS
else:
    sms = None


def _hash_otp(otp_code: str) -> str:
    pepper = os.getenv("OTP_PEPPER", "")
    return hashlib.sha256(f"{otp_code}:{pepper}".encode("utf-8")).hexdigest()


def generate_otp(username: str, phone: str) -> str:
    otp_code = str(random.randint(100000, 999999))
    db = SessionLocal()
    try:
        active = db.query(OTPChallenge).filter(
            OTPChallenge.username == username,
            OTPChallenge.consumed_at.is_(None),
        ).all()
        now = datetime.now(timezone.utc)
        for row in active:
            row.consumed_at = now

        db.add(
            OTPChallenge(
                username=username,
                phone=phone,
                otp_hash=_hash_otp(otp_code),
                expires_at=now + timedelta(minutes=OTP_EXPIRY_MINUTES),
                attempts=0,
            )
        )
        db.commit()
    finally:
        db.close()
    return otp_code


def verify_otp(username: str, provided_otp: str) -> bool:
    db = SessionLocal()
    try:
        challenge = db.query(OTPChallenge).filter(
            OTPChallenge.username == username,
            OTPChallenge.consumed_at.is_(None),
        ).order_by(OTPChallenge.created_at.desc()).first()

        if challenge is None:
            return False

        now = datetime.now(timezone.utc)
        expires_at = challenge.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            challenge.consumed_at = now
            db.commit()
            return False

        challenge.attempts += 1
        if challenge.attempts > OTP_MAX_ATTEMPTS:
            challenge.consumed_at = now
            db.commit()
            return False

        if challenge.otp_hash != _hash_otp(provided_otp):
            db.commit()
            return False

        challenge.consumed_at = now
        db.commit()
        return True
    finally:
        db.close()


def send_twilio_otp(to_phone: str, otp_code: str):
    if sms is None:
        raise RuntimeError("SMS provider is not configured")
    message = f"Terminus Security Code: {otp_code}. Valid for {OTP_EXPIRY_MINUTES} mins."
    sms.send(message, [to_phone])


def send_email(to_email: str, subject: str, body: str):
    print(f"\n📧 [EMAIL DISPATCHED] To: {to_email} | Subject: {subject}\n")


def send_sms(to_phone: str, body: str):
    if sms is None:
        raise RuntimeError("SMS provider is not configured")
    sms.send(body, [to_phone])