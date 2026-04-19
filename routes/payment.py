import os
import stripe
from flask import Blueprint, jsonify, render_template, request
from services.locker_service import (
    is_locker_available, create_rental, check_pin,
    unlock_locker, mark_overtime_paid, generate_pin,
    calc_overtime, now_utc, NUM_LOCKERS, RENTAL_PRICE
)
from services.stripe_service import (
    is_stripe_configured, create_rental_session, create_overtime_session
)
from services.db import db_get_transaction_by_pin

payment_bp = Blueprint("payment", __name__)

# ── In-memory session store ───────────────────────────
# Stores both rental and overtime sessions while kiosk is polling.
# { session_id: { "status": "pending"|"paid", "type": "rental"|"overtime", ... } }
_session_store: dict = {}


# ── Pages ────────────────────────────────────────────

@payment_bp.route("/")
def index():
    return render_template("index.html")

@payment_bp.route("/payment-cancelled")
def payment_cancelled():
    return render_template("payment_cancelled.html")


# ── Rental Payment ───────────────────────────────────

@payment_bp.route("/api/create-stripe-session", methods=["POST"])
def api_create_stripe_session():
    """
    Validates locker availability then creates a Stripe Checkout session.
    Returns both the URL (for QR) and session_id (for polling).
    In dev mode, immediately saves transaction and returns PIN.
    """
    data          = request.json or {}
    locker_number = int(data.get("locker_number", 0))

    if locker_number < 1 or locker_number > NUM_LOCKERS:
        return jsonify({"error": "Invalid locker number."}), 400

    if not is_locker_available(locker_number):
        return jsonify({"error": "Locker already occupied."}), 400

    if not is_stripe_configured():
        pin                   = generate_pin()
        rented_at, expires_at = create_rental(locker_number, "stripe_dev", RENTAL_PRICE, pin)
        return jsonify({
            "dev_mode":   True,
            "pin":        pin,
            "locker":     locker_number,
            "rented_at":  rented_at.strftime("%b %d, %Y %I:%M %p"),
            "expires_at": expires_at.strftime("%I:%M %p"),
        })

    try:
        url, session_id = create_rental_session(locker_number)
        _session_store[session_id] = {
            "status": "pending",
            "type":   "rental",
            "locker": locker_number,
        }
        return jsonify({"url": url, "session_id": session_id})
    except Exception as e:
        print(f"[Stripe] create_rental_session error: {e}")
        return jsonify({"error": str(e)}), 500


@payment_bp.route("/payment-success")
def payment_success():
    """Stripe redirects here on phone after rental payment."""
    locker_number = request.args.get("locker", "?")
    return render_template("payment_success.html", locker=locker_number)


# ── Stripe Webhook ───────────────────────────────────

@payment_bp.route("/api/stripe-webhook", methods=["POST"])
def stripe_webhook():
    """
    Receives Stripe events. Handles both rental and overtime payments.
    Updates _session_store so kiosk polling picks up the result.
    """
    payload        = request.get_data()
    sig_header     = request.headers.get("Stripe-Signature", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        print("[Webhook] Signature verification failed")
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        print(f"[Webhook] Error: {e}")
        return jsonify({"error": str(e)}), 400

    if event["type"] == "checkout.session.completed":
        session       = event["data"]["object"]
        session_id    = session["id"]
        metadata      = session.get("metadata", {})
        payment_type  = metadata.get("type", "rental")
        locker_number = int(metadata.get("locker_number", 0))
        pin           = metadata.get("pin", "")

        if payment_type == "overtime" and pin:
            # Overtime payment confirmed
            row = db_get_transaction_by_pin(pin)
            if row:
                _, _, ot_amount = calc_overtime(row["expires_at"])
                mark_overtime_paid(pin, ot_amount)
                _session_store[session_id] = {
                    "status": "paid",
                    "type":   "overtime",
                    "pin":    pin,
                    "locker": row["locker_number"],
                }
                print(f"[Webhook] Overtime paid — Locker #{row['locker_number']} | PIN={pin}")

        elif payment_type == "rental" and locker_number:
            # Rental payment confirmed
            new_pin = generate_pin()
            rented_at, expires_at = create_rental(locker_number, "stripe", RENTAL_PRICE, new_pin)
            _session_store[session_id] = {
                "status":     "paid",
                "type":       "rental",
                "pin":        new_pin,
                "locker":     locker_number,
                "rented_at":  rented_at.strftime("%b %d, %Y %I:%M %p"),
                "expires_at": expires_at.strftime("%b %d, %Y %I:%M %p"),
            }
            print(f"[Webhook] Rental payment confirmed — Locker #{locker_number} | PIN={new_pin}")

    return jsonify({"received": True}), 200


# ── Session Status Polling ───────────────────────────

@payment_bp.route("/api/session-status")
def api_session_status():
    """
    Polled by kiosk every 3 seconds.
    Handles both rental and overtime session types.
    Checks Stripe directly as fallback if webhook hasn't fired.
    """
    session_id = request.args.get("session_id", "")
    if not session_id:
        return jsonify({"status": "unknown"}), 400

    # Return from store if already resolved
    stored = _session_store.get(session_id, {})
    if stored.get("status") == "paid":
        return jsonify(stored)

    # Poll Stripe directly
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status == "paid":
            metadata     = session.metadata or {}
            payment_type = metadata.get("type", "rental")
            pin          = metadata.get("pin", "")
            locker_number = int(metadata.get("locker_number", 0))

            if payment_type == "overtime" and pin:
                if session_id not in _session_store or _session_store[session_id].get("status") != "paid":
                    row = db_get_transaction_by_pin(pin)
                    if row:
                        _, _, ot_amount = calc_overtime(row["expires_at"])
                        mark_overtime_paid(pin, ot_amount)
                        _session_store[session_id] = {
                            "status": "paid",
                            "type":   "overtime",
                            "pin":    pin,
                            "locker": row["locker_number"],
                        }
                        print(f"[Polling] Overtime paid — Locker #{row['locker_number']} | PIN={pin}")
                return jsonify(_session_store.get(session_id, {"status": "pending"}))

            elif payment_type == "rental" and locker_number:
                if session_id not in _session_store or _session_store[session_id].get("status") != "paid":
                    new_pin = generate_pin()
                    rented_at, expires_at = create_rental(locker_number, "stripe", RENTAL_PRICE, new_pin)
                    _session_store[session_id] = {
                        "status":     "paid",
                        "type":       "rental",
                        "pin":        new_pin,
                        "locker":     locker_number,
                        "rented_at":  rented_at.strftime("%b %d, %Y %I:%M %p"),
                        "expires_at": expires_at.strftime("%b %d, %Y %I:%M %p"),
                    }
                    print(f"[Polling] Rental confirmed — Locker #{locker_number} | PIN={new_pin}")
                return jsonify(_session_store[session_id])

        return jsonify({"status": "pending"})
    except Exception as e:
        print(f"[Polling] Stripe error: {e}")
        return jsonify({"status": "pending"})


# ── PIN & Unlock ─────────────────────────────────────

@payment_bp.route("/api/check-pin", methods=["POST"])
def api_check_pin():
    pin = (request.json or {}).get("pin", "").strip()
    if not pin.isdigit() or len(pin) != 6:
        return jsonify({"ok": False, "message": "Invalid PIN format."})
    result = check_pin(pin)
    return jsonify(result)


@payment_bp.route("/api/unlock", methods=["POST"])
def api_unlock():
    pin = (request.json or {}).get("pin", "").strip()
    if not pin.isdigit() or len(pin) != 6:
        return jsonify({"ok": False, "message": "Invalid PIN format."})
    result = unlock_locker(pin)

    # ── SERIAL COMM STUB ────────────────────────────────
    # When locker is successfully unlocked, trigger the solenoid via serial port.
    # Hardware team: replace this stub with actual serial communication.
    #
    # Example using pyserial:
    #   import serial
    #   ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
    #   locker_num = result.get("locker")
    #   ser.write(f"UNLOCK:{locker_num}\n".encode())
    #   ser.close()
    #
    if result.get("ok"):
        locker_num = result.get("locker")
        print(f"[SERIAL STUB] Send unlock signal for Locker #{locker_num} via serial port")
        # TODO: serial.Serial('/dev/ttyUSB0', 9600).write(f"UNLOCK:{locker_num}\n".encode())

    return jsonify(result)


# ── Overtime Payment ─────────────────────────────────

@payment_bp.route("/api/create-overtime-session", methods=["POST"])
def api_create_overtime_session():
    """
    Creates a Stripe Checkout session for overtime charges.
    Returns session_id so kiosk can poll for completion.
    """
    data = request.json or {}
    pin  = data.get("pin", "").strip()

    if not pin.isdigit() or len(pin) != 6:
        return jsonify({"error": "Invalid PIN."}), 400

    row = db_get_transaction_by_pin(pin)
    if not row:
        return jsonify({"error": "Transaction not found."}), 404

    is_ot, ot_hours, ot_amount = calc_overtime(row["expires_at"])
    if not is_ot:
        return jsonify({"error": "No overtime detected."}), 400

    locker_number = row["locker_number"]

    if not is_stripe_configured():
        mark_overtime_paid(pin, ot_amount)
        return jsonify({"dev_mode": True, "pin": pin, "locker": locker_number})

    try:
        url, session_id = create_overtime_session(locker_number, pin, ot_hours, ot_amount)
        _session_store[session_id] = {
            "status": "pending",
            "type":   "overtime",
            "pin":    pin,
            "locker": locker_number,
        }
        return jsonify({"url": url, "session_id": session_id})
    except Exception as e:
        print(f"[Stripe] create_overtime_session error: {e}")
        return jsonify({"error": str(e)}), 500


@payment_bp.route("/overtime-success")
def overtime_success():
    """Stripe redirects phone here after overtime payment."""
    pin           = request.args.get("pin", "")
    locker_number = request.args.get("locker", "?")

    if pin:
        row = db_get_transaction_by_pin(pin)
        if row:
            _, _, ot_amount = calc_overtime(row["expires_at"])
            mark_overtime_paid(pin, ot_amount)

    return render_template("overtime_paid.html", locker=locker_number, pin=pin)


# ── Serial Communication Stubs ───────────────────────
# These endpoints are called by the microcontroller (Raspberry Pi / Arduino)
# via serial communication (pyserial). The hardware team should:
# 1. Read these endpoint specs
# 2. Write a script on the Pi that reads serial data and calls these endpoints
# 3. Replace the print stubs in /api/unlock with actual serial.write() calls
#
# Serial communication flow:
#   Kiosk → Flask → serial.write("UNLOCK:3\n") → Arduino reads → triggers relay
#   Arduino reads coin → serial.write("COIN:1000\n") → Pi reads → POST /api/cash-insert-coin
#   Arduino reads NFC → serial.write("NFC:A1B2C3D4\n") → Pi reads → POST /api/nfc-scan-payment

@payment_bp.route("/api/hardware/locker-status", methods=["GET"])
def api_hardware_locker_status():
    """
    Hardware team endpoint — returns which lockers are occupied.
    Microcontroller can poll this to sync LED indicators.

    Serial flow: Pi polls this → updates LED array via GPIO
    """
    from services.db import db_get_all_lockers
    lockers = db_get_all_lockers()
    return jsonify({
        "lockers": [
            {"locker_number": n, "status": s}
            for n, s in lockers.items()
        ]
    })


@payment_bp.route("/api/hardware/trigger-unlock", methods=["POST"])
def api_hardware_trigger_unlock():
    """
    Hardware team endpoint — directly trigger a locker solenoid.
    Called by Pi after Flask confirms a valid unlock.

    Request: { "locker_number": 3 }

    Serial flow:
      Flask calls this → Pi receives via HTTP → Pi writes to Arduino serial
      Pi script example:
        import serial, requests
        ser = serial.Serial('/dev/ttyUSB0', 9600)
        def unlock(locker_num):
            ser.write(f"UNLOCK:{locker_num}\\n".encode())
    """
    data          = request.json or {}
    locker_number = int(data.get("locker_number", 0))

    if locker_number < 1 or locker_number > NUM_LOCKERS:
        return jsonify({"ok": False, "error": "Invalid locker number."}), 400

    # ── SERIAL COMM STUB ────────────────────────────────
    # Hardware team: replace with actual serial write
    # import serial
    # ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
    # ser.write(f"UNLOCK:{locker_number}\n".encode())
    # response = ser.readline().decode().strip()  # wait for "OK" from Arduino
    # ser.close()
    print(f"[SERIAL STUB] Trigger solenoid for Locker #{locker_number}")

    return jsonify({"ok": True, "locker": locker_number, "message": "Unlock signal sent."})


@payment_bp.route("/api/hardware/coin-inserted", methods=["POST"])
def api_hardware_coin_inserted():
    """
    Hardware team endpoint — called by Pi when coin acceptor detects a coin.
    Pi reads coin signal via GPIO/serial then calls this endpoint.

    Request: { "session_id": "...", "amount": 1000 }

    Serial flow:
      Arduino detects coin → serial.write("COIN:1000\\n") → Pi reads
      Pi script:
        line = ser.readline().decode().strip()
        if line.startswith("COIN:"):
            amount = int(line.split(":")[1])
            requests.post("http://localhost:5000/api/hardware/coin-inserted",
                         json={"session_id": session_id, "amount": amount})
    """
    from services.nfc_service import cash_insert_coin
    data       = request.json or {}
    session_id = data.get("session_id", "").strip()
    amount     = int(data.get("amount", 0))

    if not session_id or amount <= 0:
        return jsonify({"ok": False, "error": "Invalid data."}), 400

    result = cash_insert_coin(session_id, amount)
    return jsonify(result)


@payment_bp.route("/api/hardware/nfc-tapped", methods=["POST"])
def api_hardware_nfc_tapped():
    """
    Hardware team endpoint — called by Pi when NFC reader detects a card tap.
    Pi reads card UID via NFC library (nfcpy or mfrc522) then calls this.

    Request: { "card_uid": "A1B2C3D4", "mode": "payment"|"retrieve", "locker_number": 3 }

    Serial/GPIO flow:
      NFC reader (PN532 via SPI/I2C) → Pi reads UID via nfcpy
      Pi script:
        import nfc  # or use mfrc522 library
        uid = read_nfc_uid()  # returns hex string e.g. "A1B2C3D4"
        requests.post("http://localhost:5000/api/hardware/nfc-tapped",
                     json={"card_uid": uid, "mode": "payment", "locker_number": 3})
    """
    from services.nfc_service import nfc_process_payment, nfc_process_retrieval
    from services.locker_service import is_locker_available

    data          = request.json or {}
    card_uid      = data.get("card_uid", "").strip().upper()
    mode          = data.get("mode", "retrieve")
    locker_number = int(data.get("locker_number", 0))

    if not card_uid:
        return jsonify({"ok": False, "error": "No card UID provided."}), 400

    if mode == "payment":
        if locker_number < 1 or locker_number > NUM_LOCKERS:
            return jsonify({"ok": False, "error": "Invalid locker number."}), 400
        if not is_locker_available(locker_number):
            return jsonify({"ok": False, "error": "Locker already occupied."}), 400
        result = nfc_process_payment(card_uid, locker_number)
    else:
        result = nfc_process_retrieval(card_uid)

    # If retrieval succeeded, trigger solenoid
    if result.get("ok") and mode == "retrieve":
        locker_num = result.get("locker")
        print(f"[SERIAL STUB] NFC retrieval — trigger solenoid for Locker #{locker_num}")
        # TODO: serial.Serial('/dev/ttyUSB0', 9600).write(f"UNLOCK:{locker_num}\n".encode())

    return jsonify(result)