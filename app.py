from flask import Flask
from routes.lockers import lockers_bp
from routes.payment import payment_bp
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

app.register_blueprint(lockers_bp)
app.register_blueprint(payment_bp)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
