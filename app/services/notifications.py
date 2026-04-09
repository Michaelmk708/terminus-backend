import os
from dotenv import load_dotenv

load_dotenv()

def send_email(to_email: str, subject: str, body: str):
    """
    In production, this uses smtplib or SendGrid API to dispatch the email.
    """
    print(f"\n📧 [EMAIL DISPATCHED]")
    print(f"   To: {to_email}")
    print(f"   Subject: {subject}")
    print(f"   Body: {body}\n")
    
    # Example production logic:
    # msg = MIMEText(body)
    # msg['Subject'] = subject
    # msg['From'] = os.getenv("SMTP_EMAIL")
    # msg['To'] = to_email
    # server = smtplib.SMTP(os.getenv("SMTP_SERVER"), int(os.getenv("SMTP_PORT")))
    # server.starttls()
    # server.login(os.getenv("SMTP_EMAIL"), os.getenv("SMTP_PASSWORD"))
    # server.send_message(msg)
    # server.quit()

def send_sms(to_phone: str, body: str):
    """
    In production, this uses the Twilio Python SDK to send a text message.
    """
    print(f"\n📱 [SMS DISPATCHED]")
    print(f"   To: {to_phone}")
    print(f"   Message: {body}\n")
    
    # Example production logic:
    # from twilio.rest import Client
    # client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    # message = client.messages.create(
    #     body=body,
    #     from_=os.getenv("TWILIO_PHONE_NUMBER"),
    #     to=to_phone
    # )