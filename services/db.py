import os
from supabase import create_client, Client

# ── Client ───────────────────────────────────────────
_supabase: Client = None

def get_client() -> Client | None:
    global _supabase
    if _supabase:
        return _supabase
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if url and key:
        _supabase = create_client(url, key)
    return _supabase


# ── Lockers ──────────────────────────────────────────

def db_get_all_lockers() -> dict:
    """Returns {locker_number: status} for all lockers."""
    sb = get_client()
    if not sb:
        return {}
    try:
        res = sb.table("lockers").select("locker_number, status").execute()
        return {row["locker_number"]: row["status"] for row in res.data}
    except Exception as e:
        print(f"[DB] db_get_all_lockers error: {e}")
        return {}


def db_get_locker_status(locker_number: int) -> str | None:
    """Returns status string for a single locker, or None on error."""
    sb = get_client()
    if not sb:
        return None
    try:
        res = sb.table("lockers") \
            .select("status") \
            .eq("locker_number", locker_number) \
            .single() \
            .execute()
        return res.data["status"]
    except Exception as e:
        print(f"[DB] db_get_locker_status error: {e}")
        return None


def db_set_locker(locker_number: int, status: str) -> bool:
    """
    Update a locker's status by locker_number.
    Uses update+eq — NOT upsert (upsert targets id PK, not locker_number).
    Returns True if at least one row was updated.
    """
    from services.locker_service import now_utc
    sb = get_client()
    if not sb:
        return False
    try:
        res = sb.table("lockers") \
        .update({"status": status}) \
        .eq("locker_number", int(locker_number)) \
        .execute() 
        rows = len(res.data) if res.data else 0
        print(f"[DB] Locker #{locker_number} → {status} | rows_updated={rows}")
        if rows == 0:
            print(f"[DB] WARNING: no rows updated for locker #{locker_number} — run schema.sql first")
        return rows > 0
    except Exception as e:
        print(f"[DB] db_set_locker error: {e}")
        return False


# ── Transactions ─────────────────────────────────────

def db_insert_transaction(data: dict) -> bool:
    """Insert a new transaction row. Returns True on success."""
    sb = get_client()
    if not sb:
        return False
    try:
        sb.table("transactions").insert(data).execute()
        return True
    except Exception as e:
        print(f"[DB] db_insert_transaction error: {e}")
        return False


def db_get_transaction_by_pin(pin: str) -> dict | None:
    """Return the transaction matching pin (active or overdue), or None."""
    sb = get_client()
    if not sb:
        return None
    try:
        res = sb.table("transactions") \
            .select("*") \
            .eq("pin", pin) \
            .in_("status", ["active", "expired"]) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if not res.data:
            return None
        row = res.data[0]
        # Normalize NULL values from Supabase
        row["overtime_paid"] = bool(row.get("overtime_paid") or False)
        row["overtime_amount"] = row.get("overtime_amount") or 0
        return row
    except Exception as e:
        print(f"[DB] db_get_transaction_by_pin error: {e}")
        return None


def db_update_transaction(tx_id: str, fields: dict) -> bool:
    """Update fields on a transaction by id. Returns True on success."""
    sb = get_client()
    if not sb:
        return False
    try:
        sb.table("transactions").update(fields).eq("id", tx_id).execute()
        return True
    except Exception as e:
        print(f"[DB] db_update_transaction error: {e}")
        return False


def db_get_overdue_transactions() -> list:
    """Return all active transactions past their expires_at."""
    from services.locker_service import now_utc
    sb = get_client()
    if not sb:
        return []
    try:
        res = sb.table("transactions") \
            .select("id, locker_number, expires_at") \
            .eq("status", "active") \
            .lt("expires_at", now_utc().isoformat()) \
            .execute()
        return res.data or []
    except Exception as e:
        print(f"[DB] db_get_overdue_transactions error: {e}")
        return []
