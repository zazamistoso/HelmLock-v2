from flask import Flask
from routes.lockers import lockers_bp
from routes.payment import payment_bp
from routes.nfc import nfc_bp
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
import serial.tools.list_ports

load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)

app.register_blueprint(lockers_bp)
app.register_blueprint(payment_bp)
app.register_blueprint(nfc_bp)


# ======================================================
# SERIAL PORT CHECKER
# ======================================================
def check_com_port(port_name="COM9"):
    ports = serial.tools.list_ports.comports()

    found = False

    print("\n========== SERIAL PORT CHECK ==========")

    for port in ports:
        print(f"Detected: {port.device} | {port.description}")

        if port.device == port_name:
            found = True

    if found:
        print(f"[OK] {port_name} detected and available.\n")
    else:
        print(f"[WARNING] {port_name} not detected.\n")


# ======================================================
# MAIN
# ======================================================
if __name__ == "__main__":
    check_com_port("COM9")   # Change if needed

    app.run(
        debug=True,
        use_reloader=False,   # VERY IMPORTANT: prevent double Flask process
        port=5000,
        host="0.0.0.0"
    )