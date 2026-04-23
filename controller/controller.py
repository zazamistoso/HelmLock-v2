import serial
import serial.tools.list_ports
import time
import threading
import atexit

# =========================================================
# CONFIG
# =========================================================
SERIAL_PORT = "COM9"      # Preferred port
BAUD_RATE = 115200
TIMEOUT = 10

# =========================================================
# GLOBALS
# =========================================================
_ser = None
_buffer = []
_lock = threading.Lock()
_reader_started = False


# =========================================================
# SERIAL HELPERS
# =========================================================
def find_arduino_port():
    """
    Auto-detect Arduino/Mega/USB Serial device.
    Returns COM port string or fallback SERIAL_PORT.
    """
    ports = serial.tools.list_ports.comports()

    for port in ports:
        desc = port.description.lower()

        if (
            "arduino" in desc
            or "mega" in desc
            or "usb serial" in desc
            or "ch340" in desc
        ):
            print(f"[AUTO DETECT] Found device on {port.device}")
            return port.device

    return SERIAL_PORT


def _get_ser():
    global _ser

    if _ser is None or not _ser.is_open:

        while True:
            try:
                port = find_arduino_port()

                print(f"[SYSTEM] Connecting to {port}...")
                _ser = serial.Serial(port, BAUD_RATE, timeout=1)

                time.sleep(2)  # Arduino reset delay

                print("[SYSTEM] Serial connected.")
                _start_reader()
                break

            except Exception as e:
                print("[ERROR] Serial connection failed:", e)
                print("[SYSTEM] Retrying in 3 seconds...\n")
                time.sleep(3)

    return _ser


def close_serial():
    global _ser

    try:
        if _ser and _ser.is_open:
            _ser.close()
            print("[SYSTEM] Serial closed.")
    except:
        pass


atexit.register(close_serial)


# =========================================================
# READER THREAD
# =========================================================
def _start_reader():
    global _reader_started

    if _reader_started:
        return

    _reader_started = True

    def _reader():
        global _ser

        while True:
            try:
                line = _get_ser().readline().decode(errors="ignore").strip()

                if line:
                    print("[MEGA]", line)

                    with _lock:
                        _buffer.append(line)

            except Exception as e:
                print("[READER ERROR]", e)

                try:
                    if _ser:
                        _ser.close()
                except:
                    pass

                _ser = None
                time.sleep(2)

    threading.Thread(target=_reader, daemon=True).start()


# =========================================================
# CORE HELPERS
# =========================================================
def _send(cmd):
    try:
        print("[RPI → MEGA]", cmd)
        _get_ser().write((cmd + "\n").encode())

    except Exception as e:
        print("[SEND ERROR]", e)


def _clear():
    with _lock:
        _buffer.clear()


def _wait(key, timeout=TIMEOUT):
    start = time.time()

    while time.time() - start < timeout:

        with _lock:
            for msg in _buffer:
                if key in msg:
                    _buffer.clear()
                    return True

        time.sleep(0.1)

    return False


# =========================================================
# NFC
# =========================================================
def nfc_read():
    """
    Reads NFC UID from Mega.
    Returns UID string or "" on timeout.
    """

    _clear()
    _send("nfcread")

    start = time.time()

    while time.time() - start < TIMEOUT:

        with _lock:
            for msg in _buffer:
                if msg.startswith("NFCREAD-"):
                    _buffer.clear()
                    return msg.split("-", 1)[1]

        time.sleep(0.1)

    return ""


# =========================================================
# LOCKER COMMANDS
# =========================================================
def store(locker):
    """
    Open locker for storage.
    """
    _clear()
    _send(f"store:{locker}")
    return _wait(f"STORE-DONE-{locker}")


def claim(locker):
    """
    Open locker for claiming item.
    """
    _clear()
    _send(f"claim:{locker}")
    return _wait(f"CLAIM-DONE-{locker}")


def sanitise(locker):
    """
    Trigger sanitisation.
    """
    _clear()
    _send(f"sanitise:{locker}")
    return _wait(f"SANITISE-DONE-{locker}")


# =========================================================
# PAYMENT
# =========================================================
def payment(cost):
    """
    Wait for coin payment.
    Example:
    payment(5000) = ₱50
    """
    _clear()
    _send(f"coinpayment:{cost}")
    return _wait("COINPAYMENT-SUCCESS", timeout=120)


# =========================================================
# CLI TEST MODE
# =========================================================
if __name__ == "__main__":

    _get_ser()

    print("\nHelmLock Controller Ready")
    print("Commands:")
    print("nfc")
    print("store 1")
    print("claim 1")
    print("sanitise 1")
    print("pay 5000")
    print("flow 1 5000")
    print("exit\n")

    while True:
        try:
            cmd = input(">> ").strip()

            if cmd == "exit":
                break

            elif cmd == "nfc":
                uid = nfc_read()
                print("UID:", uid if uid else "No card")

            elif cmd.startswith("store"):
                _, n = cmd.split()
                print("SUCCESS" if store(int(n)) else "FAIL")

            elif cmd.startswith("claim"):
                _, n = cmd.split()
                print("SUCCESS" if claim(int(n)) else "FAIL")

            elif cmd.startswith("sanitise"):
                _, n = cmd.split()
                print("SUCCESS" if sanitise(int(n)) else "FAIL")

            elif cmd.startswith("pay"):
                _, amt = cmd.split()
                print("PAID" if payment(int(amt)) else "FAILED")

            elif cmd.startswith("flow"):
                _, locker_str, cost_str = cmd.split()

                locker = int(locker_str)
                cost = int(cost_str)

                print("\n--- SESSION START ---")

                uid = nfc_read()

                if not uid:
                    print("No NFC detected")
                    continue

                print("UID:", uid)

                if not payment(cost):
                    print("Payment failed")
                    continue

                print("Payment success")

                if not store(locker):
                    print("Store failed")
                    continue

                print("Stored successfully")

                input("Press ENTER to claim...")

                if not claim(locker):
                    print("Claim failed")
                    continue

                print("Claim successful")

            else:
                print("Unknown command")

        except KeyboardInterrupt:
            break

        except Exception as e:
            print("[CLI ERROR]", e)

    close_serial()