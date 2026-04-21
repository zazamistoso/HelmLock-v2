import serial
import time
import threading

# =========================================================
# CONFIG
# =========================================================
SERIAL_PORT = "/dev/ttyACM0"
BAUD_RATE   = 115200
TIMEOUT     = 10  # seconds to wait for a response

# =========================================================
# SERIAL CONNECTION
# =========================================================
ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
time.sleep(2)  # wait for Arduino to reset after USB connect

response_buffer = []
buffer_lock = threading.Lock()

# =========================================================
# BACKGROUND READER THREAD
# reads all Serial lines from Mega continuously
# =========================================================
def serial_reader():
    while True:
        try:
            line = ser.readline().decode("utf-8").strip()
            if line:
                print(f"[MEGA] {line}")
                with buffer_lock:
                    response_buffer.append(line)
        except Exception as e:
            print(f"[READER ERROR] {e}")

reader_thread = threading.Thread(target=serial_reader, daemon=True)
reader_thread.start()

# =========================================================
# SEND COMMAND
# =========================================================
def send(cmd: str):
    print(f"[RPI → MEGA] {cmd}")
    ser.write((cmd + "\n").encode("utf-8"))

# =========================================================
# WAIT FOR RESPONSE
# waits until a line containing keyword appears in buffer
# =========================================================
def wait_for(keyword: str, timeout: int = TIMEOUT) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        with buffer_lock:
            for line in response_buffer:
                if keyword in line:
                    response_buffer.clear()
                    return True
        time.sleep(0.1)
    print(f"[TIMEOUT] Waiting for: {keyword}")
    return False

# =========================================================
# CLEAR BUFFER
# =========================================================
def clear_buffer():
    with buffer_lock:
        response_buffer.clear()

# =========================================================
# HIGH-LEVEL COMMANDS
# =========================================================

def store_helmet(locker: int):
    """Full store flow: unlock → user places helmet → lock → sanitise"""
    clear_buffer()
    send(f"locker:{locker}")
    return wait_for(f"STOREHELMET-DONE-{locker}", timeout=60)

def claim(locker: int):
    """Claim flow: unlock → user takes helmet → lock"""
    clear_buffer()
    send(f"claim:{locker}")
    return wait_for(f"CLAIM-DONE-{locker}", timeout=30)

def sanitise(locker: int):
    """Manual sanitise trigger"""
    clear_buffer()
    send(f"sanitise:{locker}")
    return wait_for(f"SANITISE-DONE-{locker}", timeout=30)

def door_lock(locker: int):
    clear_buffer()
    send(f"doorlock:{locker}")
    return wait_for(f"DOORLOCK-{locker}")

def door_unlock(locker: int):
    clear_buffer()
    send(f"doorunlock:{locker}")
    return wait_for(f"DOORUNLOCK-{locker}")

def door_status(locker: int) -> str:
    """Returns 'OPEN', 'CLOSED', or 'TIMEOUT'"""
    clear_buffer()
    send(f"doorstatus:{locker}")
    start = time.time()
    while time.time() - start < TIMEOUT:
        with buffer_lock:
            for line in response_buffer:
                if f"DOORSTATUS-{locker}" in line:
                    response_buffer.clear()
                    return "OPEN" if "OPEN" in line else "CLOSED"
        time.sleep(0.1)
    return "TIMEOUT"

def nfc_read() -> str:
    """Returns NFC UID string or empty string on timeout"""
    clear_buffer()
    send("nfcread")
    start = time.time()
    while time.time() - start < TIMEOUT:
        with buffer_lock:
            for line in response_buffer:
                if line.startswith("NFCREAD-"):
                    uid = line.split("-", 1)[1]
                    response_buffer.clear()
                    return uid
        time.sleep(0.1)
    return ""

def coin_payment(cost: int) -> bool:
    """Returns True if payment successful"""
    clear_buffer()
    send(f"coinpayment:{cost}")
    return wait_for("COINPAYMENT-SUCCESS", timeout=120)

# =========================================================
# EXAMPLE FULL FLOW
# =========================================================
def full_flow(locker: int, cost: int):
    print(f"\n=== HELMLOCK FLOW | Locker {locker} | Cost ₱{cost} ===\n")

    # Step 1: NFC tap for identity
    print("[1] Waiting for NFC tap...")
    uid = nfc_read()
    if not uid:
        print("[FAIL] No NFC detected")
        return
    print(f"[OK] UID: {uid}")

    # Step 2: Coin payment
    print(f"[2] Waiting for ₱{cost} payment...")
    paid = coin_payment(cost)
    if not paid:
        print("[FAIL] Payment not completed")
        return
    print("[OK] Payment received")

    # Step 3: Store helmet
    print("[3] Store helmet flow starting...")
    stored = store_helmet(locker)
    if not stored:
        print("[FAIL] Store helmet flow failed")
        return
    print("[OK] Helmet stored and sanitised")

    # Step 4: Claim (when user returns)
    input("\n[PRESS ENTER WHEN USER RETURNS TO CLAIM HELMET]\n")
    print("[4] Claim flow starting...")
    claimed = claim(locker)
    if not claimed:
        print("[FAIL] Claim flow failed")
        return
    print("[OK] Helmet claimed. Session complete.\n")

# =========================================================
# CLI TEST MODE
# =========================================================
if __name__ == "__main__":
    print("HelmLock RPi Controller")
    print("Commands: store, claim, sanitise, doorlock, doorunlock, doorstatus, nfc, coin, flow")
    print("Type 'exit' to quit\n")

    while True:
        try:
            cmd = input(">> ").strip().lower()

            if cmd == "exit":
                break

            elif cmd.startswith("store "):
                n = int(cmd.split()[1])
                store_helmet(n)

            elif cmd.startswith("claim "):
                n = int(cmd.split()[1])
                claim(n)

            elif cmd.startswith("sanitise "):
                n = int(cmd.split()[1])
                sanitise(n)

            elif cmd.startswith("doorlock "):
                n = int(cmd.split()[1])
                door_lock(n)

            elif cmd.startswith("doorunlock "):
                n = int(cmd.split()[1])
                door_unlock(n)

            elif cmd.startswith("doorstatus "):
                n = int(cmd.split()[1])
                print(door_status(n))

            elif cmd == "nfc":
                print(nfc_read())

            elif cmd.startswith("coin "):
                cost = int(cmd.split()[1])
                coin_payment(cost)

            elif cmd.startswith("flow "):
                parts = cmd.split()
                locker = int(parts[1])
                cost   = int(parts[2])
                full_flow(locker, cost)

            else:
                print("Unknown command")

        except (KeyboardInterrupt, EOFError):
            break
        except Exception as e:
            print(f"[ERROR] {e}")

    ser.close()
    print("Bye.")