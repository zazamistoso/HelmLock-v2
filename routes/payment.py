from flask import Blueprint, jsonify, render_template, request, redirect
from services.locker_service import (
    is_locker_available, create_rental, check_pin,
    unlock_locker, mark_overtime_paid, generate_pin,
    calc_overtime, now_utc, NUM_LOCKERS, RENTAL_PRICE
)
from services.stripe_service import (
    is_stripe_configured, create_rental_session, create_overtime_session
)
from services.db import db_get_transaction_by_pin
from datetime import timedelta

payment_bp = Blueprint("payment", __name__)


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
    In dev mode (no Stripe key), immediately saves transaction and returns PIN.
    """
    data          = request.json or {}
    locker_number = int(data.get("locker_number", 0))

    if locker_number < 1 or locker_number > NUM_LOCKERS:
        return jsonify({"error": "Invalid locker number."}), 400

    if not is_locker_available(locker_number):
        return jsonify({"error": "Locker already occupied."}), 400

    # Dev mode — no Stripe key present
    if not is_stripe_configured():
        pin                    = generate_pin()
        rented_at, expires_at  = create_rental(locker_number, "stripe_dev", RENTAL_PRICE, pin)
        return jsonify({
            "dev_mode":   True,
            "pin":        pin,
            "locker":     locker_number,
            "rented_at":  rented_at.strftime("%b %d, %Y %I:%M %p"),
            "expires_at": expires_at.strftime("%I:%M %p"),
        })

    try:
        url = create_rental_session(locker_number)
        return jsonify({"url": url})
    except Exception as e:
        print(f"[Stripe] create_rental_session error: {e}")
        return jsonify({"error": str(e)}), 500


@payment_bp.route("/payment-success")
def payment_success():
    """
    Stripe redirects here after successful rental payment.
    Always saves transaction — payment is already confirmed by Stripe.
    """
    locker_number = int(request.args.get("locker", 0))
    pin           = generate_pin()
    rented_at, expires_at = create_rental(locker_number, "stripe", RENTAL_PRICE, pin)

    return render_template(
        "payment_success.html",
        pin=pin,
        locker=locker_number,
        rented_at=rented_at.strftime("%b %d, %Y %I:%M %p"),
        expires_at=expires_at.strftime("%b %d, %Y %I:%M %p"),
    )


# ── PIN & Unlock ─────────────────────────────────────

@payment_bp.route("/api/check-pin", methods=["POST"])
def api_check_pin():
    """
    Validates PIN and returns status without unlocking.
    Frontend uses this to decide: unlock immediately or show overtime screen.
    """
    pin = (request.json or {}).get("pin", "").strip()

    if not pin.isdigit() or len(pin) != 6:
        return jsonify({"ok": False, "message": "Invalid PIN format."})

    result = check_pin(pin)
    return jsonify(result)


@payment_bp.route("/api/unlock", methods=["POST"])
def api_unlock():
    """
    Unlocks the locker if:
    - PIN is valid and active
    - Rental is within time, OR overtime has been paid
    Marks transaction as retrieved and frees locker in DB.
    """
    pin = (request.json or {}).get("pin", "").strip()

    if not pin.isdigit() or len(pin) != 6:
        return jsonify({"ok": False, "message": "Invalid PIN format."})

    result = unlock_locker(pin)
    return jsonify(result)


# ── Overtime Payment ─────────────────────────────────

@payment_bp.route("/api/create-overtime-session", methods=["POST"])
def api_create_overtime_session():
    """
    Creates a Stripe Checkout session for overtime charges.
    Overtime amount is recalculated server-side — never trust client-side amount.
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

    # Dev mode
    if not is_stripe_configured():
        mark_overtime_paid(pin, ot_amount)
        return jsonify({
            "dev_mode": True,
            "pin":      pin,
            "locker":   locker_number,
        })

    try:
        url = create_overtime_session(locker_number, pin, ot_hours, ot_amount)
        return jsonify({"url": url})
    except Exception as e:
        print(f"[Stripe] create_overtime_session error: {e}")
        return jsonify({"error": str(e)}), 500


@payment_bp.route("/overtime-success")
def overtime_success():
    """
    Stripe redirects here after overtime payment.
    Marks overtime as paid — user can now enter PIN to unlock.
    """
    pin           = request.args.get("pin", "")
    locker_number = request.args.get("locker", "?")

    if pin:
        row = db_get_transaction_by_pin(pin)
        if row:
            _, _, ot_amount = calc_overtime(row["expires_at"])
            mark_overtime_paid(pin, ot_amount)

    return render_template("overtime_paid.html", locker=locker_number, pin=pin)
