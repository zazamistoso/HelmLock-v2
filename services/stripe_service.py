import os
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

BASE_URL     = os.environ.get("BASE_URL", "http://localhost:5000")
RENTAL_PRICE = 5000   # centavos → ₱50.00


def is_stripe_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", ""))


def create_rental_session(locker_number: int):
    """
    Creates a Stripe Checkout session for a 1-hour rental.
    Returns (url, session_id).
    Passes type=rental in metadata so polling can distinguish from overtime.
    """
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "php",
                "product_data": {
                    "name": f"Helmlock 4S — Locker #{locker_number} (1 hour)"
                },
                "unit_amount": RENTAL_PRICE,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=(
            f"{BASE_URL}/payment-success"
            f"?locker={locker_number}"
            f"&session_id={{CHECKOUT_SESSION_ID}}"
        ),
        cancel_url=f"{BASE_URL}/payment-cancelled",
        metadata={
            "locker_number": str(locker_number),
            "type":          "rental",
        },
    )
    print(f"[Stripe] Rental session created: id={session.id}")
    return session.url, session.id


def create_overtime_session(locker_number: int, pin: str, hours: int, amount: int):
    """
    Creates a Stripe Checkout session for overtime charges.
    Returns (url, session_id).
    Passes type=overtime and pin in metadata so polling can mark overtime paid.
    """
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "php",
                "product_data": {
                    "name": f"Helmlock 4S — Overtime ({hours} hr) Locker #{locker_number}"
                },
                "unit_amount": amount,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=(
            f"{BASE_URL}/overtime-success"
            f"?pin={pin}"
            f"&locker={locker_number}"
            f"&session_id={{CHECKOUT_SESSION_ID}}"
        ),
        cancel_url=f"{BASE_URL}/",
        metadata={
            "pin":          pin,
            "locker_number": str(locker_number),
            "type":          "overtime",
        },
    )
    print(f"[Stripe] Overtime session created: id={session.id} | PIN={pin}")
    return session.url, session.id