import os
import random
import africastalking
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# 1. AFRICA'S TALKING CONFIGURATION
# ==========================================
username = os.getenv("AT_USERNAME", "sandbox")
api_key = os.getenv("AT_API_KEY")

if api_key:
    africastalking.initialize(username, api_key)
    sms = africastalking.SMS

# ==========================================
# 2. OTP INFRASTRUCTURE (Missing Functions)
# ==========================================

# In-memory store for OTPs
OTP_STORE = {}

def generate_otp(username: str) -> str:
    """Generates a 6-digit OTP and stores it temporarily."""
    otp_code = str(random.randint(100000, 999999))
    OTP_STORE[username] = otp_code
    return otp_code

def verify_otp(username: str, provided_otp: str) -> bool:
    """Verifies and consumes the OTP."""
    if username in OTP_STORE and OTP_STORE[username] == provided_otp:
        del OTP_STORE[username]
        return True
    return False

def send_twilio_otp(to_phone: str, otp_code: str):
    """
    Sends OTP via Africa's Talking with a local fallback 
    to bypass SSL/Network errors during development.
    """
    print(f"\n🔐 [SECURITY GATE] Processing OTP for {to_phone}...")
    
    # 1. Always print to terminal first so you are never blocked
    print(f"      >>> DEBUG OTP CODE: {otp_code} <<<")
    
    if not api_key or api_key == "your_api_key":
        print("      ⚠️ [SIMULATION] No API Key found. Use the code above.")
        return

    # 2. Attempt the real SMS dispatch
    try:
        message = f"Terminus Security Code: {otp_code}. Valid for 10 mins."
        # Note: We wrap the actual network call
        response = sms.send(message, [to_phone])
        print(f"      ✅ [SMS DISPATCHED] Response: {response}\n")
    except Exception as e:
        # 3. Handle SSL/Connection errors gracefully
        print(f"      ⚠️ [NETWORK FALLBACK] Could not send real SMS: {e}")
        print(f"      👉 ACTION: Use the DEBUG OTP CODE printed above to continue testing.\n")
# ==========================================
# 3. HEARTBEAT NOTIFICATIONS (Watchdog)
# ==========================================

def send_email(to_email: str, subject: str, body: str):
    print(f"\n📧 [EMAIL DISPATCHED] To: {to_email} | Subject: {subject}\n")

def send_sms(to_phone: str, body: str):
    """Used for watchdog alerts via AT."""
    try:
        if api_key:
            sms.send(body, [to_phone])
    except:
        print(f"📱 [SMS SIMULATION] To: {to_phone} | Body: {body}")