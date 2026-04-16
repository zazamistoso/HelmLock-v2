from flask import Flask
from routes.lockers import lockers_bp
from routes.payment import payment_bp
from routes.nfc import nfc_bp
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)

app.register_blueprint(lockers_bp)
app.register_blueprint(payment_bp)
app.register_blueprint(nfc_bp)

if __name__ == "__main__":
    app.run(debug=True, port=5000, host="0.0.0.0")