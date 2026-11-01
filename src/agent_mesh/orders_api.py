"""Mock order API: the system of record the orders agent reaches through its MCP server.

Every route is scoped by customer in the path, and an order that belongs to someone else
answers 404, never 403, so the API does not confirm that another customer's order exists.
Return creation re-checks eligibility server side: the agent's earlier check is advisory.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import date

import httpx
from pydantic import BaseModel, TypeAdapter
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_mesh.config import HEALTH_PATH
from agent_mesh.domain.orders import (
    Eligibility,
    Order,
    ReturnRequest,
    check_eligibility,
    seed_orders,
)


class ReturnCreate(BaseModel):
    """Body of `POST /customers/{customer_id}/returns`."""

    order_id: str
    sku: str
    quantity: int
    reason: str


class OrderStore:
    """In-memory orders and returns, seeded relative to the clock."""

    def __init__(self, today: Callable[[], date] = date.today) -> None:
        self._today = today
        self._orders = {o.order_id: o for o in seed_orders(today())}
        self._returns: list[ReturnRequest] = []
        self._return_numbers = itertools.count(1)

    def list_orders(self, customer_id: str) -> list[Order]:
        """Every order of `customer_id`."""
        return [o for o in self._orders.values() if o.customer_id == customer_id]

    def get_order(self, customer_id: str, order_id: str) -> Order | None:
        """The order, or None when it does not exist or belongs to someone else."""
        order = self._orders.get(order_id)
        return order if order is not None and order.customer_id == customer_id else None

    def _returned_quantity(self, order_id: str, sku: str) -> int:
        return sum(r.quantity for r in self._returns if r.order_id == order_id and r.sku == sku)

    def check_return_eligibility(self, order: Order, sku: str, quantity: int) -> Eligibility:
        """Eligibility counting the units already returned from this order line."""
        return check_eligibility(
            order, sku, quantity, self._returned_quantity(order.order_id, sku), self._today()
        )

    def create_return(self, order: Order, body: ReturnCreate) -> ReturnRequest | Eligibility:
        """File the return, or hand back the failed verdict; eligibility is re-checked here."""
        verdict = self.check_return_eligibility(order, body.sku, body.quantity)
        if not verdict.eligible:
            return verdict
        request = ReturnRequest(
            return_id=f"RET-{next(self._return_numbers):04d}",
            order_id=order.order_id,
            sku=body.sku,
            quantity=body.quantity,
            reason=body.reason,
            created_on=self._today(),
        )
        self._returns.append(request)
        return request


_ORDER_NOT_FOUND = JSONResponse({"error": "order_not_found"}, status_code=404)
"""Same reply for a missing order and another customer's order, so neither is confirmed."""


def create_orders_app(store: OrderStore | None = None) -> Starlette:
    """The mock order API. Tests pass a store to control the clock."""
    store = store or OrderStore()

    async def list_orders(request: Request) -> JSONResponse:
        orders = store.list_orders(request.path_params["customer_id"])
        return JSONResponse([o.model_dump(mode="json") for o in orders])

    async def get_order(request: Request) -> JSONResponse:
        order = store.get_order(request.path_params["customer_id"], request.path_params["order_id"])
        return _ORDER_NOT_FOUND if order is None else JSONResponse(order.model_dump(mode="json"))

    async def check_return_eligibility(request: Request) -> JSONResponse:
        order = store.get_order(request.path_params["customer_id"], request.path_params["order_id"])
        if order is None:
            return _ORDER_NOT_FOUND
        sku = request.query_params.get("sku", "")
        quantity = int(request.query_params.get("quantity", "1"))
        return JSONResponse(
            store.check_return_eligibility(order, sku, quantity).model_dump(mode="json")
        )

    async def create_return(request: Request) -> JSONResponse:
        body = ReturnCreate.model_validate_json(await request.body())
        order = store.get_order(request.path_params["customer_id"], body.order_id)
        if order is None:
            return _ORDER_NOT_FOUND
        result = store.create_return(order, body)
        status = 201 if isinstance(result, ReturnRequest) else 409
        return JSONResponse(result.model_dump(mode="json"), status_code=status)

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    prefix = "/customers/{customer_id}"
    return Starlette(
        routes=[
            Route(HEALTH_PATH, health),
            Route(f"{prefix}/orders", list_orders),
            Route(f"{prefix}/orders/{{order_id}}", get_order),
            Route(f"{prefix}/orders/{{order_id}}/eligibility", check_return_eligibility),
            Route(f"{prefix}/returns", create_return, methods=["POST"]),
        ]
    )


_OrderListAdapter = TypeAdapter(list[Order])


class OrdersApiClient:
    """Typed client the orders MCP server uses. Returns None for 404 so callers map NOT_FOUND."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def list_orders(self, customer_id: str) -> list[Order]:
        """`GET /customers/{customer_id}/orders`."""
        response = await self._http.get(f"/customers/{customer_id}/orders")
        response.raise_for_status()
        return _OrderListAdapter.validate_json(response.content)

    async def get_order(self, customer_id: str, order_id: str) -> Order | None:
        """`GET /customers/{customer_id}/orders/{order_id}`; None on 404."""
        response = await self._http.get(f"/customers/{customer_id}/orders/{order_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return Order.model_validate_json(response.content)

    async def check_return_eligibility(
        self, customer_id: str, order_id: str, sku: str, quantity: int
    ) -> Eligibility | None:
        """`GET .../orders/{order_id}/eligibility`; None on 404."""
        response = await self._http.get(
            f"/customers/{customer_id}/orders/{order_id}/eligibility",
            params={"sku": sku, "quantity": quantity},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return Eligibility.model_validate_json(response.content)

    async def create_return(
        self, customer_id: str, body: ReturnCreate
    ) -> ReturnRequest | Eligibility | None:
        """`POST .../returns`: the filed return, the refusing verdict on 409, None on 404."""
        response = await self._http.post(
            f"/customers/{customer_id}/returns", content=body.model_dump_json()
        )
        if response.status_code == 404:
            return None
        if response.status_code == 409:
            return Eligibility.model_validate_json(response.content)
        response.raise_for_status()
        return ReturnRequest.model_validate_json(response.content)
