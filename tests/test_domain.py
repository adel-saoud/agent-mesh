"""Catalog generator, search, return rules and the mock order API."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from agent_mesh.config import Settings
from agent_mesh.contracts import Answer, Language, OutcomeAdapter
from agent_mesh.domain.catalog import (
    INJECTED_REVIEW,
    INJECTED_REVIEW_SKU,
    generate_catalog,
    load_catalog,
    write_catalog,
)
from agent_mesh.domain.orders import (
    Country,
    Eligibility,
    Order,
    OrderLine,
    OrderStatus,
    ReturnRequest,
    check_eligibility,
)
from agent_mesh.domain.search import CatalogIndex, tokenize
from agent_mesh.language import detect_language
from agent_mesh.orders_api import OrdersApiClient, ReturnCreate

TODAY = date(2026, 10, 20)


def test_committed_catalog_matches_generator(settings: Settings, tmp_path: Path) -> None:
    regenerated = tmp_path / "catalog.json"
    write_catalog(regenerated)
    assert regenerated.read_text(encoding="utf-8") == settings.catalog_path.read_text(
        encoding="utf-8"
    )


def test_catalog_has_doc_gaps_and_one_injected_review() -> None:
    products = generate_catalog()
    assert len(products) == 60
    assert len({p.sku for p in products}) == 60
    assert any(len(p.attributes) < 4 for p in products), "some products must lack an attribute"
    injected = [p.sku for p in products for r in p.reviews if r.text == INJECTED_REVIEW]
    assert injected == [INJECTED_REVIEW_SKU]


def test_search_ranks_and_handles_french(catalog_index: CatalogIndex) -> None:
    assert catalog_index.search("air fryer", limit=5)[0].product.category == "air_fryer"
    assert catalog_index.search("bouilloire électrique", limit=5)[0].product.category == "kettle"
    assert catalog_index.search("laptop", limit=5) == []
    assert catalog_index.get_by_sku("kw-ket-001") is not None
    assert tokenize("Le grille-pain ÉLECTRIQUE") == ["grille", "pain", "electrique"]


def test_load_catalog_round_trip(settings: Settings) -> None:
    assert load_catalog(settings.catalog_path)[0].sku == "KW-KET-001"


def _order(
    country: Country, status: OrderStatus = OrderStatus.DELIVERED, delivered_ago: int = 5
) -> Order:
    return Order(
        order_id="ORD-9",
        customer_id="c",
        country=country,
        currency="EUR",
        status=status,
        placed_on=TODAY - timedelta(days=delivered_ago + 3),
        delivered_on=TODAY - timedelta(days=delivered_ago)
        if status is OrderStatus.DELIVERED
        else None,
        lines=[
            OrderLine(sku="KW-KET-001", category="kettle", quantity=2, unit_price=30),
            OrderLine(sku="KW-HUM-001", category="humidifier", quantity=1, unit_price=50),
        ],
    )


@pytest.mark.parametrize(
    ("order", "sku", "quantity", "returned", "reason"),
    [
        (_order(Country.FR), "KW-KET-001", 1, 0, "ok"),
        (_order(Country.FR), "KW-XXX-000", 1, 0, "sku_not_in_order"),
        (_order(Country.AE), "KW-HUM-001", 1, 0, "category_excluded"),
        (_order(Country.FR), "KW-HUM-001", 1, 0, "ok"),
        (_order(Country.FR, OrderStatus.SHIPPED), "KW-KET-001", 1, 0, "not_delivered_yet"),
        (_order(Country.FR, delivered_ago=31), "KW-KET-001", 1, 0, "window_expired"),
        (_order(Country.AE, delivered_ago=16), "KW-KET-001", 1, 0, "window_expired"),
        (_order(Country.FR), "KW-KET-001", 2, 1, "quantity_exceeds_purchase"),
    ],
)
def test_eligibility_rules(
    order: Order, sku: str, quantity: int, returned: int, reason: str
) -> None:
    verdict = check_eligibility(order, sku, quantity, returned, TODAY)
    assert verdict.reason == reason
    assert verdict.eligible is (reason == "ok")


async def test_orders_api_scopes_by_customer(orders_api: OrdersApiClient) -> None:
    assert {o.order_id for o in await orders_api.list_orders("cus-fr-001")} == {
        "ORD-1001",
        "ORD-1002",
        "ORD-1003",
    }
    assert await orders_api.get_order("cus-fr-001", "ORD-2001") is None
    assert (
        await orders_api.check_return_eligibility("cus-fr-001", "ORD-2001", "KW-BLD-003", 1) is None
    )
    body = ReturnCreate(order_id="ORD-2001", sku="KW-BLD-003", quantity=1, reason="x")
    assert await orders_api.create_return("cus-fr-001", body) is None


async def test_orders_api_return_once_then_refuses(orders_api: OrdersApiClient) -> None:
    body = ReturnCreate(order_id="ORD-1001", sku="KW-KET-002", quantity=1, reason="x")
    created = await orders_api.create_return("cus-fr-001", body)
    assert isinstance(created, ReturnRequest)
    again = await orders_api.create_return("cus-fr-001", body)
    assert isinstance(again, Eligibility)
    assert again.reason == "quantity_exceeds_purchase"


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Where is my order?", Language.EN),
        ("Où est ma commande ?", Language.FR),
        ("Quel est le prix", Language.FR),
    ],
)
def test_detect_language(text: str, language: Language) -> None:
    assert detect_language(text) is language


def test_answer_needs_a_citation() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        OutcomeAdapter.validate_python(
            {"kind": "ANSWER", "language": "en", "message": "x", "citations": []}
        )
    parsed = OutcomeAdapter.validate_python(
        {
            "kind": "ANSWER",
            "language": "fr",
            "message": "x",
            "citations": [
                {"doc_id": "KW-KET-001", "source": "catalog", "retrieved_on": "2026-10-20"}
            ],
        }
    )
    assert isinstance(parsed, Answer)
