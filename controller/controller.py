import serial
import time
import threading

SERIAL_PORT = "COM9"
BAUD_RATE   = 115200
TIMEOUT     = 10

# ── Serial connection (lazy-init so import never crashes) ──────────────────
_ser: serial.Serial | None = None
_buffer: list[str] = []
_lock = threading.Lock()
_reader_started = False


def _get_ser() -> serial.Serial:
    global _ser
    if _ser is None or not _ser.is_open:
        _ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)
        _start_reader()
    return _ser


def _start_reader():
    global _reader_started
    if _reader_started:
        return
    _reader_started = True

    def _reader():
        while True:
            try:
                line = _get_ser().readline().decode().strip()
                if line:
                    print("[MEGA]", line)
                    with _lock:
                        _buffer.append(line)
            except Exception:
                pass

    threading.Thread(target=_reader, daemon=True).start()


# ── Primitives ─────────────────────────────────────────────────────────────

def _send(cmd: str):
    print("[RPI → MEGA]", cmd)
    _get_ser().write((cmd + "\n").encode())


def _wait(key: str, timeout: int = TIMEOUT) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        with _lock:
            for b in _buffer:
                if key in b:
                    _buffer.clear()
                    return True
        time.sleep(0.1)
    return False


def _clear():
    with _lock:
        _buffer.clear()


# ── NFC ────────────────────────────────────────────────────────────────────

def nfc_read() -> str:
    """Tells the Mega to scan an NFC card. Returns UID string or '' on timeout."""
    _clear()
    _send("nfcread")
    start = time.time()
    while time.time() - start < TIMEOUT:
        with _lock:
            for b in _buffer:
                if b.startswith("NFCREAD-"):
                    _buffer.clear()
                    return b.split("-", 1)[1]
        time.sleep(0.1)
    return ""


# ── Locker Commands ────────────────────────────────────────────────────────

def store(locker: int) -> bool:
    """
    Opens locker for item storage (after payment).
    Returns True when the Mega confirms STORE-DONE-<locker>.
    """
    _clear()
    _send(f"store:{locker}")
    return _wait(f"STORE-DONE-{locker}")


def claim(locker: int) -> bool:
    """
    Opens locker for item retrieval (after PIN/NFC validation).
    Returns True when the Mega confirms CLAIM-DONE-<locker>.
    """
    _clear()
    _send(f"claim:{locker}")
    return _wait(f"CLAIM-DONE-{locker}")


def sanitise(locker: int) -> bool:
    """
    Triggers the sanitisation cycle on a locker.
    Returns True when the Mega confirms SANITISE-DONE-<locker>.
    """
    _clear()
    _send(f"sanitise:{locker}")
    return _wait(f"SANITISE-DONE-{locker}")


# ── Coin Payment ───────────────────────────────────────────────────────────

def payment(cost: int) -> bool:
    """
    Waits for the coin acceptor to collect <cost> centavos.
    Returns True on COINPAYMENT-SUCCESS within 120 s.
    """
    _clear()
    _send(f"coinpayment:{cost}")
    return _wait("COINPAYMENT-SUCCESS", timeout=120)


# ── CLI (only runs when executed directly) ─────────────────────────────────

if __name__ == "__main__":
    _get_ser()   # ensure connection + reader thread

    while True:
        cmd = input(">> ")

        if cmd == "exit":
            break

        elif cmd.startswith("flow"):
            _, locker_str, cost_str = cmd.split()
            lk, co = int(locker_str), int(cost_str)
            print("\n--- SESSION START ---")
            uid = nfc_read()
            if not uid:
                print("No NFC")
                continue
            print("UID:", uid)
            if not payment(co):
                print("Payment failed")
                continue
            print("Paid")
            if not store(lk):
                print("Store failed")
                continue
            print("Stored")
            input("Press ENTER to claim...")
            if not claim(lk):
                print("Claim failed")
                continue
            print("Done")

        elif cmd == "nfc":
            print(nfc_read())

        elif cmd.startswith("store"):
            _, n = cmd.split()
            print("OK" if store(int(n)) else "FAIL")

        elif cmd.startswith("claim"):
            _, n = cmd.split()
            print("OK" if claim(int(n)) else "FAIL")

        elif cmd.startswith("sanitise"):
            _, n = cmd.split()
            print("OK" if sanitise(int(n)) else "FAIL")