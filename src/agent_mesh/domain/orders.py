"""Synthetic customers, orders and per-country return rules, plus the pure eligibility logic.

Dates are stored as day offsets from "today" and materialized with an injectable clock, so the
demo always has recent orders and tests can freeze time.

Return rules live in config keyed by country code. Adding a country means adding a rule entry,
not code. The windows are a fictional store policy for the demo, not legal advice.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Country(StrEnum):
    """Markets the store ships to; each has its own return rule and currency."""

    FR = "FR"
    AE = "AE"


class OrderStatus(StrEnum):
    """Fulfilment stage. Only DELIVERED orders start a return window."""

    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"


class ReturnRule(BaseModel):
    """A market's return policy, as data so a new market is one entry, not new code."""

    model_config = ConfigDict(frozen=True)

    window_days: int = Field(ge=0, description="Days after delivery a return can be filed.")
    excluded_categories: frozenset[str] = frozenset()


RETURN_RULES: dict[Country, ReturnRule] = {
    Country.FR: ReturnRule(window_days=30),
    Country.AE: ReturnRule(window_days=15, excluded_categories=frozenset({"humidifier"})),
}
"""Return policy per market; `check_eligibility` reads it, the LLM never does."""


class Customer(BaseModel):
    """A synthetic customer. Email and phone exist so redaction has realistic PII to catch."""

    model_config = ConfigDict(frozen=True)

    customer_id: str
    name: str
    email: str
    phone: str
    country: Country


class OrderLine(BaseModel):
    """One SKU of an order. `category` is copied in so return exclusions need no catalog lookup."""

    model_config = ConfigDict(frozen=True)

    sku: str
    category: str
    quantity: int = Field(ge=1)
    unit_price: float


class Order(BaseModel):
    """An order as the mock order API returns it."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    customer_id: str
    country: Country
    currency: str
    status: OrderStatus
    placed_on: date
    delivered_on: date | None
    lines: list[OrderLine]


class ReturnRequest(BaseModel):
    """A filed return."""

    model_config = ConfigDict(frozen=True)

    return_id: str
    order_id: str
    sku: str
    quantity: int
    reason: str
    created_on: date


class Eligibility(BaseModel):
    """The verdict of `check_eligibility`. `reason` is a stable code the agent explains."""

    model_config = ConfigDict(frozen=True)

    eligible: bool
    reason: str
    deadline: date | None = None
    returnable_quantity: int = 0


def check_eligibility(
    order: Order, sku: str, quantity: int, already_returned: int, today: date
) -> Eligibility:
    """Decide whether `quantity` units of `sku` from `order` can be returned on `today`.

    Deterministic on purpose: the LLM never decides eligibility, it only reports this result.
    """
    line = next((item for item in order.lines if item.sku == sku), None)
    if line is None:
        return Eligibility(eligible=False, reason="sku_not_in_order")
    rule = RETURN_RULES[order.country]
    if line.category in rule.excluded_categories:
        return Eligibility(eligible=False, reason="category_excluded")
    if order.status is not OrderStatus.DELIVERED or order.delivered_on is None:
        return Eligibility(eligible=False, reason="not_delivered_yet")
    deadline = order.delivered_on + timedelta(days=rule.window_days)
    if today > deadline:
        return Eligibility(eligible=False, reason="window_expired", deadline=deadline)
    remaining = line.quantity - already_returned
    if quantity > remaining:
        return Eligibility(
            eligible=False,
            reason="quantity_exceeds_purchase",
            deadline=deadline,
            returnable_quantity=remaining,
        )
    return Eligibility(eligible=True, reason="ok", deadline=deadline, returnable_quantity=remaining)


CUSTOMERS = [
    Customer(
        customer_id="cus-fr-001",
        name="Camille Martin",
        email="camille.martin@example.com",
        phone="+33 6 12 34 56 78",
        country=Country.FR,
    ),
    Customer(
        customer_id="cus-fr-002",
        name="Louis Bernard",
        email="louis.bernard@example.com",
        phone="+33 7 98 76 54 32",
        country=Country.FR,
    ),
    Customer(
        customer_id="cus-ae-001",
        name="Layla Haddad",
        email="layla.haddad@example.com",
        phone="+971 50 123 4567",
        country=Country.AE,
    ),
]
"""Fictional customers. Names, emails (example.com) and phone numbers are invented."""

_SEED_ORDERS: list[
    tuple[str, str, OrderStatus, int, int | None, list[tuple[str, str, int, float]]]
] = [
    # order_id, customer_id, status, placed_days_ago, delivered_days_ago, lines
    (
        "ORD-1001",
        "cus-fr-001",
        OrderStatus.DELIVERED,
        12,
        8,
        [("KW-KET-002", "kettle", 1, 29.5), ("KW-TOA-001", "toaster", 1, 27.9)],
    ),
    (
        "ORD-1002",
        "cus-fr-001",
        OrderStatus.DELIVERED,
        60,
        55,
        [("KW-AFR-003", "air_fryer", 1, 132.0)],
    ),
    (
        "ORD-1003",
        "cus-fr-001",
        OrderStatus.SHIPPED,
        2,
        None,
        [("KW-GRD-002", "coffee_grinder", 2, 38.4)],
    ),
    ("ORD-2001", "cus-fr-002", OrderStatus.DELIVERED, 20, 16, [("KW-BLD-003", "blender", 1, 88.0)]),
    (
        "ORD-3001",
        "cus-ae-001",
        OrderStatus.DELIVERED,
        9,
        6,
        [("KW-HUM-002", "humidifier", 1, 220.0), ("KW-FAN-001", "desk_fan", 2, 134.0)],
    ),
    (
        "ORD-3002",
        "cus-ae-001",
        OrderStatus.DELIVERED,
        30,
        25,
        [("KW-RCK-004", "rice_cooker", 1, 180.0)],
    ),
]


def seed_orders(today: date) -> list[Order]:
    """Materialize the seed orders relative to `today`."""
    currency = {Country.FR: "EUR", Country.AE: "AED"}
    country_of = {c.customer_id: c.country for c in CUSTOMERS}
    orders: list[Order] = []
    for order_id, customer_id, status, placed_ago, delivered_ago, lines in _SEED_ORDERS:
        country = country_of[customer_id]
        orders.append(
            Order(
                order_id=order_id,
                customer_id=customer_id,
                country=country,
                currency=currency[country],
                status=status,
                placed_on=today - timedelta(days=placed_ago),
                delivered_on=None
                if delivered_ago is None
                else today - timedelta(days=delivered_ago),
                lines=[
                    OrderLine(sku=sku, category=category, quantity=qty, unit_price=price)
                    for sku, category, qty, price in lines
                ],
            )
        )
    return orders
