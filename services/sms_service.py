import os
import requests

MYSMSGATE_URL = "https://api.mysmsgate.com/api/v1/message"
MYSMSGATE_API_KEY = os.environ.get("MYSMSGATE_API_KEY")

def send_pin_sms(phone_number: str, pin: str, locker: int, expires_at: str):
    message = (
        f"Your locker PIN is {pin}.\n"
        f"Locker #{locker} | Expires: {expires_at}\n"
        f"Keep this PIN to retrieve your items."
    )

    try:
        response = requests.post(
            MYSMSGATE_URL,
            headers={
                "Authorization": f"Bearer {MYSMSGATE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "message": message,
                "phones": [phone_number],
            }
        )
        response.raise_for_status()
        print(f"[SMS] PIN sent to {phone_number} for Locker #{locker}")
    except Exception as e:
        print(f"[SMS] Failed to send PIN: {e}")