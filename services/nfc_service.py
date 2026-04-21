import os
import uuid
from services.locker_service import generate_pin, create_rental, now_utc, RENTAL_PRICE
from supabase import create_client, Client

try:
    from controller.controller import store as ctrl_store, claim as ctrl_claim
    _HW = True
except Exception as e:
    print(f"[NFC Service] Controller unavailable ({e}); physical locker commands disabled.")
    _HW = False

# ── Supabase Client ───────────────────────────────────
def get_client() -> Client | None:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if url and key:
        return create_client(url, key)
    return None


# ── Accepted coin denominations (centavos) ────────────
ACCEPTED_COINS = {100, 500, 1000, 2000}  # ₱1, ₱5, ₱10, ₱20


# ── NFC Card Queries ──────────────────────────────────

def nfc_get_card(card_uid: str) -> dict | None:
    """Fetch a card record by UID. Returns None if not found."""
    sb = get_client()
    if not sb:
        return None
    try:
        res = sb.table("nfc_cards") \
            .select("*") \
            .eq("card_uid", card_uid) \
            .single() \
            .execute()
        return res.data
    except Exception as e:
        print(f"[NFC] nfc_get_card error: {e}")
        return None


def nfc_update_card(card_uid: str, fields: dict) -> bool:
    """Update fields on a card by UID."""
    sb = get_client()
    if not sb:
        return False
    try:
        sb.table("nfc_cards") \
            .update(fields) \
            .eq("card_uid", card_uid) \
            .execute()
        return True
    except Exception as e:
        print(f"[NFC] nfc_update_card error: {e}")
        return False


def nfc_register_card(card_uid: str) -> dict:
    """Register a new NFC card with zero balance."""
    sb = get_client()
    if not sb:
        return {}
    try:
        res = sb.table("nfc_cards") \
            .insert({"card_uid": card_uid, "balance": 0, "status": "idle"}) \
            .execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        print(f"[NFC] nfc_register_card error: {e}")
        return {}


def nfc_get_active_transaction(card_uid: str) -> dict | None:
    """
    Look up the active transaction linked to this card UID.
    Returns the transaction row or None.
    """
    sb = get_client()
    if not sb:
        return None
    try:
        res = sb.table("transactions") \
            .select("*") \
            .eq("card_uid", card_uid) \
            .eq("status", "active") \
            .execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[NFC] nfc_get_active_transaction error: {e}")
        return None


# ── NFC Payment Logic ─────────────────────────────────

def nfc_process_payment(card_uid: str, locker_number: int) -> dict:
    """
    Process Stored Value Card payment for a locker rental.
    - Checks card exists and has sufficient balance
    - Deducts ₱50 from balance in Supabase
    - Creates transaction linked to card_uid
    - Card UID itself is used for retrieval — no PIN stored on card

    Returns dict with ok, locker, rented_at, expires_at or error.
    """
    card = nfc_get_card(card_uid)

    if not card:
        # Auto-register unknown card with zero balance
        card = nfc_register_card(card_uid)
        if not card:
            return {"ok": False, "error": "Card not recognized. Please register your card."}
        return {"ok": False, "error": "Card registered but has no balance. Please load your card first."}

    if card.get("balance", 0) < RENTAL_PRICE:
        balance_display = f"₱{card.get('balance', 0) // 100}.00"
        return {
            "ok":    False,
            "error": f"Insufficient balance. Current balance: {balance_display}. Please load at least ₱50.00."
        }

    # Check if card already has an active rental
    existing = nfc_get_active_transaction(card_uid)
    if existing:
        return {
            "ok":    False,
            "error": "This card already has an active rental. Please retrieve your helmet first."
        }

    # Deduct balance
    new_balance = card["balance"] - RENTAL_PRICE
    nfc_update_card(card_uid, {
        "balance":    new_balance,
        "status":     "active",
        "updated_at": now_utc().isoformat(),
    })

    # Create transaction linked to card_uid
    # We still generate a PIN internally for the unlock mechanism
    pin = generate_pin()
    rented_at, expires_at = create_rental_with_card(
        locker_number, card_uid, pin
    )

    print(f"[NFC] Payment — Locker #{locker_number} | UID={card_uid} | Balance: ₱{new_balance // 100}.00")

    return {
        "ok":         True,
        "locker":     locker_number,
        "rented_at":  rented_at.strftime("%b %d, %Y %I:%M %p"),
        "expires_at": expires_at.strftime("%b %d, %Y %I:%M %p"),
        "balance":    new_balance,
    }


def create_rental_with_card(locker_number: int, card_uid: str, pin: str):
    """
    Creates a rental transaction with card_uid linked.
    Returns (rented_at, expires_at).
    """
    from services.locker_service import now_utc, SESSION_HOURS
    from datetime import timedelta
    from services.db import db_set_locker

    sb = get_client()
    rented_at  = now_utc()
    expires_at = rented_at + timedelta(hours=SESSION_HOURS)

    if sb:
        try:
            sb.table("transactions").insert({
                "locker_number":   locker_number,
                "payment_method":  "nfc",
                "amount":          RENTAL_PRICE,
                "pin":             pin,
                "card_uid":        card_uid,
                "status":          "active",
                "rented_at":       rented_at.isoformat(),
                "expires_at":      expires_at.isoformat(),
                "retrieved_at":    None,
                "overtime_paid":   False,
                "overtime_amount": 0,
            }).execute()
            db_set_locker(locker_number, "occupied")
            if _HW:
                ctrl_store(locker_number)
        except Exception as e:
            print(f"[NFC] create_rental_with_card error: {e}")

    return rented_at, expires_at


# ── NFC Retrieval Logic ───────────────────────────────

def nfc_process_retrieval(card_uid: str) -> dict:
    """
    Process Stored Value Card tap for helmet retrieval.
    - Finds active transaction by card UID
    - Checks for overtime
    - Unlocks locker and marks transaction retrieved
    - Updates card status back to idle

    The card UID IS the key — no PIN needed.
    """
    from services.locker_service import calc_overtime, now_utc
    from services.db import db_set_locker, db_update_transaction

    transaction = nfc_get_active_transaction(card_uid)

    if not transaction:
        return {"ok": False, "error": "No active rental found for this card."}

    # Check overtime
    is_ot, ot_hours, ot_amount = calc_overtime(transaction["expires_at"])

    if is_ot and not transaction.get("overtime_paid", False):
        return {
            "ok":                     False,
            "is_overtime":            True,
            "overtime_hours":         ot_hours,
            "overtime_amount":        ot_amount,
            "overtime_amount_display": f"₱{ot_amount // 100}.00",
            "error":                  f"Overtime detected — ₱{ot_amount // 100}.00 must be paid before retrieving.",
            "pin":                    transaction.get("pin"),  # for overtime payment
            "locker":                 transaction["locker_number"],
        }

    # Mark transaction as retrieved
    db_update_transaction(transaction["id"], {
        "status":       "retrieved",
        "retrieved_at": now_utc().isoformat(),
    })

    # Free locker
    db_set_locker(transaction["locker_number"], "available")

    # Reset card status
    nfc_update_card(card_uid, {
        "status":     "idle",
        "updated_at": now_utc().isoformat(),
    })

    print(f"[NFC] Retrieval — Locker #{transaction['locker_number']} | UID={card_uid}")
    if _HW:
        ctrl_claim(transaction["locker_number"])

    return {"ok": True, "locker": transaction["locker_number"]}


# ── Cash (Coin Acceptor) Logic ────────────────────────
# Accepted: ₱1 (100), ₱5 (500), ₱10 (1000), ₱20 (2000) centavos
# Exact ₱50 required — no change dispensed.

_cash_sessions: dict = {}


def cash_create_session(locker_number: int) -> dict:
    """
    Starts a coin payment session by delegating to the controller.
    Blocks until the Arduino confirms the full amount collected (up to 120s).
    Returns dict with ok, pin, locker, rented_at, expires_at or error.
    """
    from services.locker_service import generate_pin, create_rental

    print(f"[Cash] Starting coin payment — Locker #{locker_number}")

    if not _HW:
        print("[Cash] HW unavailable — skipping coin payment")
        paid = True
    else:
        from controller.controller import payment as ctrl_payment
        paid = ctrl_payment(RENTAL_PRICE)

    if not paid:
        return {"ok": False, "error": "Payment timed out. Please try again."}

    pin                   = generate_pin()
    rented_at, expires_at = create_rental(locker_number, "cash", RENTAL_PRICE, pin)

    print(f"[Cash] Payment complete — Locker #{locker_number} | PIN={pin}")

    return {
        "ok":         True,
        "pin":        pin,
        "locker":     locker_number,
        "rented_at":  rented_at.strftime("%b %d, %Y %I:%M %p"),
        "expires_at": expires_at.strftime("%b %d, %Y %I:%M %p"),
    }


def cash_insert_coin(session_id: str, amount: int) -> dict:
    """Kept for backwards compatibility — coin counting is now handled by the controller via cash_create_session."""
    return {"ok": False, "error": "Coin counting is handled by the controller. Use cash_create_session instead."}


def cash_get_session(session_id: str) -> dict:
    """Get current cash session status."""
    return _cash_sessions.get(session_id, {})