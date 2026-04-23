import os
import requests

MYSMSGATE_URL = "https://mysmsgate.net/api/v1/send"
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
                "to": phone_number,   # ✅ matches curl
                "message": message,
                "slot": 0             # ✅ required based on curl
            }
        )

        response.raise_for_status()
        print(f"[SMS] PIN sent to {phone_number} for Locker #{locker}")
        print(response.json())  # optional debug

    except requests.exceptions.HTTPError as e:
        print(f"[SMS] HTTP error: {e} | Response: {response.text}")
    except Exception as e:
        print(f"[SMS] Failed to send PIN: {e}")