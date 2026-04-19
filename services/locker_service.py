import math
import random
import string
from datetime import datetime, timezone, timedelta
from services.db import (
    db_get_all_lockers, db_get_locker_status, db_set_locker,
    db_get_overdue_transactions, db_update_transaction,
    db_get_transaction_by_pin
)

NUM_LOCKERS   = 12
SESSION_HOURS = 1
RENTAL_PRICE  = 5000   # centavos → ₱50.00

# Dev fallback when no Supabase configured
_dev_store: dict = {}


# ── Time Utilities ───────────────────────────────────

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def parse_dt(s) -> datetime:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    import re
    s = str(s).strip()
    # Replace space separator with T
    s = s.replace(' ', 'T', 1)
    # Normalize +00 to +00:00
    if s.endswith('+00'):
        s = s + ':00'
    # Truncate fractional seconds to max 6 digits
    s = re.sub(r'(\.\d{6})\d+', r'\1', s)
    # Truncate to 4 digit microseconds → pad to 6
    s = re.sub(r'\.(\d{1,5})([+-])', lambda m: '.' + m.group(1).ljust(6, '0') + m.group(2), s)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def format_time_left(expires_at) -> str:
    diff = parse_dt(expires_at) - now_utc()
    secs = int(diff.total_seconds())
    if secs <= 0:
        return "Expired"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    if h and m:
        return f"{h} hr {m} min remaining"
    elif h:
        return f"{h} hr remaining"
    return f"{m} min remaining"

def calc_overtime(expires_at) -> tuple[bool, int, int]:
    """
    Returns (is_overtime, hours_over, amount_due_centavos).
    Hours are rounded up to nearest whole hour.
    """
    diff  = now_utc() - parse_dt(expires_at)
    secs  = int(diff.total_seconds())
    if secs <= 0:
        return False, 0, 0
    hours_over = math.ceil(secs / 3600)
    amount_due = hours_over * RENTAL_PRICE
    return True, hours_over, amount_due

def generate_pin(length: int = 6) -> str:
    return ''.join(random.choices(string.digits, k=length))


# ── Locker Status ────────────────────────────────────

def get_all_locker_statuses() -> list[dict]:
    """
    Returns list of {number, status} for all 12 lockers.
    Runs overdue check first so status stays accurate.
    """
    expire_overdue_sessions()

    db_map = db_get_all_lockers()

    if db_map:
        return [
            {"number": i, "status": db_map.get(i, "available")}
            for i in range(1, NUM_LOCKERS + 1)
        ]

    # Dev fallback
    _expire_dev()
    occupied = {v["locker_number"] for v in _dev_store.values() if v["status"] == "active"}
    return [
        {"number": i, "status": "occupied" if i in occupied else "available"}
        for i in range(1, NUM_LOCKERS + 1)
    ]


def is_locker_available(locker_number: int) -> bool:
    status = db_get_locker_status(locker_number)
    if status is not None:
        available = status == "available"
        print(f"[Service] Locker #{locker_number} status={status} available={available}")
        return available


def expire_overdue_sessions():
    """
    Marks overdue transactions as expired.
    NOTE: locker stays OCCUPIED until overtime is paid — do not free it here.
    """
    rows = db_get_overdue_transactions()
    for row in rows:
        print(f"[Service] Locker #{row['locker_number']} is overdue — awaiting overtime payment")
    # Dev fallback
    _expire_dev()


def _expire_dev():
    for t in _dev_store.values():
        if t["status"] == "active":
            exp = parse_dt(t["expires_at"])
            if now_utc() > exp:
                t["status"] = "expired_pending_ot"


# ── Transaction Operations ───────────────────────────

def create_rental(locker_number: int, payment_method: str, amount: int, pin: str) -> tuple[datetime, datetime]:
    """
    Saves transaction and marks locker occupied.
    Returns (rented_at, expires_at).
    """
    locker_number = int(locker_number)
    rented_at     = now_utc()
    expires_at = rented_at + timedelta(minutes=1) # minutes=1 to test the overtime

    from services.db import db_insert_transaction
    ok = db_insert_transaction({
        "locker_number":   locker_number,
        "payment_method":  payment_method,
        "amount":          amount,
        "pin":             pin,
        "status":          "active",
        "rented_at":       rented_at.isoformat(),
        "expires_at":      expires_at.isoformat(),
        "retrieved_at":    None,
        "overtime_paid":   False,
        "overtime_amount": 0,
    })

    if ok:
        db_set_locker(locker_number, "occupied")
        print(f"[Service] Rental created — Locker #{locker_number} | {payment_method} | PIN={pin}")
    else:
        print(f"[Service] ERROR: transaction insert failed for Locker #{locker_number}")

    # Dev fallback
    if not db_get_all_lockers():
        _dev_store[pin] = {
            "locker_number":  locker_number,
            "payment_method": payment_method,
            "amount":         amount,
            "status":         "active",
            "rented_at":      rented_at.isoformat(),
            "expires_at":     expires_at.isoformat(),
            "overtime_paid":  False,
            "overtime_amount": 0,
        }

    return rented_at, expires_at


def check_pin(pin: str) -> dict:
    """
    Validates PIN and returns its status without unlocking.
    Returns dict with: ok, locker, time_left, is_overtime, overtime_hours, overtime_amount
    """
    row = db_get_transaction_by_pin(pin)

    if not row:
        # Dev fallback
        if pin in _dev_store and _dev_store[pin]["status"] in ("active", "expired_pending_ot"):
            row = _dev_store[pin]
            row["id"] = pin  # use pin as id in dev mode
        else:
            return {"ok": False, "message": "Invalid or expired PIN."}

    is_ot, ot_hours, ot_amount = calc_overtime(row["expires_at"])

    return {
    "ok":                      True,
    "locker":                  row["locker_number"],
    "time_left":               format_time_left(row["expires_at"]) if not is_ot else "Expired",
    "is_overtime":             is_ot,
    "overtime_paid":           row.get("overtime_paid", False),  # this line must be here
    "overtime_hours":          ot_hours,
    "overtime_amount":         ot_amount,
    "overtime_amount_display": f"₱{ot_amount // 100}.00",
}


def unlock_locker(pin: str) -> dict:
    """
    Unlocks a locker if PIN is valid and overtime (if any) is paid.
    Frees the locker in DB.
    """
    row = db_get_transaction_by_pin(pin)
    print(f"[DEBUG] unlock row: {row}")

    if not row:
        # Dev fallback
        if pin in _dev_store and _dev_store[pin]["status"] in ("active", "expired_pending_ot"):
            row = _dev_store[pin]
            row["id"] = pin
        else:
            return {"ok": False, "message": "Invalid or expired PIN."}

    is_ot, _, _ = calc_overtime(row["expires_at"])

    if is_ot and not row.get("overtime_paid", False):
        return {"ok": False, "is_overtime": True, "message": "Overtime not yet paid."}

    # Mark retrieved
    db_update_transaction(row["id"], {
        "status":       "retrieved",
        "retrieved_at": now_utc().isoformat(),
    })

    # Free locker
    db_set_locker(row["locker_number"], "available")

    # Dev cleanup
    if pin in _dev_store:
        _dev_store[pin]["status"] = "retrieved"

    print(f"[Service] Locker #{row['locker_number']} unlocked via PIN={pin}")
    return {"ok": True, "locker": row["locker_number"]}


def mark_overtime_paid(pin: str, amount: int) -> dict:
    """Mark overtime as paid on a transaction."""
    row = db_get_transaction_by_pin(pin)

    if not row:
        if pin in _dev_store:
            _dev_store[pin]["overtime_paid"]   = True
            _dev_store[pin]["overtime_amount"] = amount
            return {"ok": True, "locker": _dev_store[pin]["locker_number"]}
        return {"ok": False, "message": "Transaction not found."}

    _, _, ot_amount = calc_overtime(row["expires_at"])

    db_update_transaction(row["id"], {
        "overtime_paid":   True,
        "overtime_amount": ot_amount,
    })

    print(f"[Service] Overtime paid — Locker #{row['locker_number']} | ₱{ot_amount // 100}.00")
    return {"ok": True, "locker": row["locker_number"]}
