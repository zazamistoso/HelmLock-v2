from flask import Blueprint, jsonify
from services.locker_service import get_all_locker_statuses, NUM_LOCKERS

lockers_bp = Blueprint("lockers", __name__)


@lockers_bp.route("/api/lockers")
def api_lockers():
    """Returns current status of all 12 lockers."""
    return jsonify(get_all_locker_statuses())
