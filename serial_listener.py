"""
serial_listener.py — Helmlock 4S Hardware Bridge
=================================================
Runs on the Raspberry Pi. Listens to the Arduino via USB serial port
and bridges hardware events (coins, NFC taps) to the Flask backend.
Also handles sending unlock signals back to the Arduino.

Hardware team responsibilities:
  - Wire relay board, coin acceptor, and NFC reader to Arduino
  - Upload matching Arduino sketch (see Serial Protocol below)
  - Set SERIAL_PORT to the correct port (run `ls /dev/tty*` to find it)
  - Run this script alongside the Flask app: python serial_listener.py

Serial Protocol (Arduino ↔ Pi):
  Pi → Arduino:
    UNLOCK:3        → open solenoid for Locker 3
    LOCK:3          → close solenoid for Locker 3 (if auto-close not wired)
    LED:3:GREEN     → set Locker 3 LED to green
    LED:3:RED       → set Locker 3 LED to red
    PING            → check connection

  Arduino → Pi:
    COIN:1000       → ₱10 coin inserted (amount in centavos)
    COIN:500        → ₱5 coin inserted
    COIN:100        → ₱1 coin inserted
    COIN:2000       → ₱20 coin inserted
    NFC:A1B2C3D4    → NFC card tapped with UID
    OK              → command acknowledged
    READY           → Arduino booted and ready

Coin amounts (centavos):
    100  = ₱1
    500  = ₱5
    1000 = ₱10
    2000 = ₱20

Usage:
    python serial_listener.py

Requirements:
    pip install pyserial requests
"""

import serial
import requests
import threading
import time
import sys

# ── Configuration ─────────────────────────────────────────────────────────────
FLASK_URL    = "http://localhost:5000"   # Flask app URL
SERIAL_PORT  = "/dev/ttyUSB0"           # Check with: ls /dev/tty* or dmesg | tail
BAUD_RATE    = 9600
RECONNECT_DELAY = 5                     # seconds between reconnect attempts

# ── State ──────────────────────────────────────────────────────────────────────
# Set these from the kiosk before hardware events happen.
# The kiosk JS calls /api/hardware/* endpoints which update these via Flask.
# Alternatively, the Pi script can maintain its own state.

current_cash_session_id = None   # set when user starts coin payment
current_nfc_mode        = "retrieve"  # "payment" or "retrieve"
current_nfc_locker      = None   # set when user selects locker for NFC payment

state_lock = threading.Lock()


# ── Serial Connection ──────────────────────────────────────────────────────────

def connect_serial():
    """Connect to Arduino via serial. Retries on failure."""
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            print(f"[Serial] Connected to {SERIAL_PORT} at {BAUD_RATE} baud")
            return ser
        except serial.SerialException as e:
            print(f"[Serial] Connection failed: {e}")
            print(f"[Serial] Retrying in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)


# ── Send Commands to Arduino ───────────────────────────────────────────────────

def send_command(ser, command: str) -> str:
    """Send a command to Arduino and wait for response."""
    try:
        ser.write(f"{command}\n".encode())
        response = ser.readline().decode().strip()
        print(f"[Serial] Sent: {command} → Got: {response}")
        return response
    except Exception as e:
        print(f"[Serial] Send error: {e}")
        return ""


def unlock_locker(ser, locker_number: int):
    """Trigger solenoid to open a locker."""
    response = send_command(ser, f"UNLOCK:{locker_number}")
    if response != "OK":
        print(f"[Serial] WARNING: unexpected response for UNLOCK:{locker_number} → {response}")


def set_led(ser, locker_number: int, color: str):
    """Set LED color for a locker. color = 'GREEN' or 'RED'"""
    send_command(ser, f"LED:{locker_number}:{color}")


# ── Flask API Calls ────────────────────────────────────────────────────────────

def api_coin_inserted(session_id: str, amount: int) -> dict:
    """Notify Flask that a coin was inserted."""
    try:
        res = requests.post(f"{FLASK_URL}/api/hardware/coin-inserted", json={
            "session_id": session_id,
            "amount":     amount,
        }, timeout=5)
        return res.json()
    except Exception as e:
        print(f"[API] coin-inserted error: {e}")
        return {}


def api_nfc_tapped(card_uid: str, mode: str, locker_number: int = 0) -> dict:
    """Notify Flask that an NFC card was tapped."""
    try:
        res = requests.post(f"{FLASK_URL}/api/hardware/nfc-tapped", json={
            "card_uid":      card_uid,
            "mode":          mode,
            "locker_number": locker_number,
        }, timeout=5)
        return res.json()
    except Exception as e:
        print(f"[API] nfc-tapped error: {e}")
        return {}


def api_get_locker_statuses() -> list:
    """Get all locker statuses to sync LEDs."""
    try:
        res = requests.get(f"{FLASK_URL}/api/hardware/locker-status", timeout=5)
        return res.json().get("lockers", [])
    except Exception as e:
        print(f"[API] locker-status error: {e}")
        return []


# ── LED Sync ──────────────────────────────────────────────────────────────────

def sync_leds(ser):
    """Sync LED states with Supabase locker statuses."""
    lockers = api_get_locker_statuses()
    for locker in lockers:
        color = "GREEN" if locker["status"] == "available" else "RED"
        set_led(ser, locker["locker_number"], color)


# ── Main Serial Listener ───────────────────────────────────────────────────────

def listen(ser):
    """
    Main loop — reads serial lines from Arduino and handles events.
    """
    global current_cash_session_id, current_nfc_mode, current_nfc_locker

    print("[Serial] Listening for hardware events...")

    # Sync LEDs on startup
    sync_leds(ser)

    while True:
        try:
            line = ser.readline().decode("utf-8").strip()

            if not line:
                continue

            print(f"[Serial] Received: {line}")

            # ── Arduino booted ─────────────────────────────────────────────
            if line == "READY":
                print("[Serial] Arduino is ready")
                sync_leds(ser)

            # ── Coin inserted ──────────────────────────────────────────────
            elif line.startswith("COIN:"):
                amount_str = line.split(":")[1]
                amount = int(amount_str)

                with state_lock:
                    session_id = current_cash_session_id

                if not session_id:
                    print("[Serial] WARNING: coin inserted but no active session")
                    continue

                data = api_coin_inserted(session_id, amount)
                print(f"[Serial] Coin ₱{amount // 100} → {data}")

                if data.get("complete") and data.get("locker"):
                    # Payment complete — unlock locker
                    locker_num = data["locker"]
                    unlock_locker(ser, locker_num)
                    set_led(ser, locker_num, "RED")
                    print(f"[Serial] Coin payment complete — Locker #{locker_num} unlocked")
                    with state_lock:
                        current_cash_session_id = None

                elif data.get("overpaid"):
                    print("[Serial] Overpayment — hardware should reject or return coin")
                    # TODO: signal coin return mechanism if available

            # ── NFC card tapped ────────────────────────────────────────────
            elif line.startswith("NFC:"):
                card_uid = line.split(":")[1].upper()

                with state_lock:
                    mode        = current_nfc_mode
                    locker_num  = current_nfc_locker or 0

                data = api_nfc_tapped(card_uid, mode, locker_num)
                print(f"[Serial] NFC UID={card_uid} mode={mode} → {data}")

                if data.get("ok"):
                    # Unlock the locker
                    locker = data["locker"]
                    unlock_locker(ser, locker)
                    set_led(ser, locker, "GREEN" if mode == "retrieve" else "RED")
                    print(f"[Serial] NFC {mode} — Locker #{locker} unlocked")

                elif data.get("is_overtime"):
                    # Overtime detected — kiosk will show overtime screen
                    # No solenoid action needed here
                    print(f"[Serial] NFC overtime detected — Locker #{data.get('locker')}")

                else:
                    print(f"[Serial] NFC error: {data.get('error', 'unknown')}")

            else:
                print(f"[Serial] Unknown command: {line}")

        except serial.SerialException as e:
            print(f"[Serial] Connection lost: {e}")
            break
        except ValueError as e:
            print(f"[Serial] Parse error: {e} | line={line}")
        except Exception as e:
            print(f"[Serial] Unexpected error: {e}")


# ── State Update Server ────────────────────────────────────────────────────────
# The kiosk needs a way to tell this script the current session_id and NFC mode.
# Option 1: Poll Flask for state (recommended — no extra server needed)
# Option 2: Simple HTTP server here (more complex)
#
# Recommended: Add a /api/hardware/set-state endpoint to Flask payment.py:
#   POST { "cash_session_id": "...", "nfc_mode": "payment", "nfc_locker": 3 }
# Then poll it here every second.

def poll_state():
    """
    Poll Flask for current hardware state (session_id, NFC mode).
    Runs in a background thread.
    """
    global current_cash_session_id, current_nfc_mode, current_nfc_locker

    while True:
        try:
            res = requests.get(f"{FLASK_URL}/api/hardware/state", timeout=3)
            if res.status_code == 200:
                state = res.json()
                with state_lock:
                    current_cash_session_id = state.get("cash_session_id")
                    current_nfc_mode        = state.get("nfc_mode", "retrieve")
                    current_nfc_locker      = state.get("nfc_locker")
        except Exception:
            pass  # Flask might not be up yet
        time.sleep(1)


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  Helmlock 4S — Serial Listener")
    print(f"  Port: {SERIAL_PORT} | Baud: {BAUD_RATE}")
    print(f"  Flask: {FLASK_URL}")
    print("=" * 50)

    # Start state polling thread
    state_thread = threading.Thread(target=poll_state, daemon=True)
    state_thread.start()

    # Connect and listen (auto-reconnects on disconnect)
    while True:
        ser = connect_serial()
        try:
            listen(ser)
        except Exception as e:
            print(f"[Serial] Fatal error: {e}")
        finally:
            ser.close()
        print(f"[Serial] Reconnecting in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)