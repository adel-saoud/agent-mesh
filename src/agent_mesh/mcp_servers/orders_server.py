"""Orders MCP server: the orders agent's only path to the mock order API.

The customer is never a tool argument. It arrives in the MCP request `_meta` under
`CUSTOMER_META_KEY`, set by the agent from the verified A2A request token, so the model cannot
ask for another customer's orders whatever the prompt says.

`create_return_request` is the one write. It additionally requires a confirmation token in
`_meta` that a human approved for this exact action (see `security.tokens`), and it is hidden
from the model's tool list: the agent calls it directly once the user confirms.
"""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer

from agent_mesh.config import COORDINATOR, get_settings
from agent_mesh.contracts import ReturnProposal
from agent_mesh.orders_api import OrdersApiClient, ReturnCreate
from agent_mesh.security.keys import TrustStore
from agent_mesh.security.tokens import ReplayCache, TokenError, verify_confirmation

CUSTOMER_META_KEY = "agent_mesh/customer_id"
"""MCP request `_meta` key for the verified customer id, prefixed per the spec's key format."""
CONFIRMATION_META_KEY = "agent_mesh/confirmation"
"""MCP request `_meta` key for the human confirmation token of a write."""
CREATE_RETURN_TOOL_NAME = "create_return_request"
"""The one write tool."""
WRITE_TOOL_NAMES = frozenset({CREATE_RETURN_TOOL_NAME})
"""Tools hidden from the model: called only by code, after a human confirmation."""


def _request_meta(ctx: Context) -> dict[str, Any]:
    """The `_meta` the client sent with this request (empty if none)."""
    return dict(ctx.request_context.meta or {})


def _authenticated_customer_id(ctx: Context) -> str:
    """The verified customer id, or a refusal: no tool runs without one."""
    customer = _request_meta(ctx).get(CUSTOMER_META_KEY)
    if not isinstance(customer, str) or not customer:
        raise PermissionError("no authenticated customer on this request")
    return customer


def create_orders_server(
    orders_api: OrdersApiClient, trust: TrustStore, replay_cache: ReplayCache | None = None
) -> MCPServer:
    """Build the server. `replay_cache` spans every call, so a confirmation files one return."""
    replay_cache = replay_cache or ReplayCache()
    server = MCPServer(
        "kettlewick-orders", instructions="Orders and returns for the signed-in customer."
    )

    @server.tool()
    async def list_orders(ctx: Context) -> list[dict[str, Any]]:
        """List the signed-in customer's orders."""
        orders = await orders_api.list_orders(_authenticated_customer_id(ctx))
        return [o.model_dump(mode="json") for o in orders]

    @server.tool()
    async def get_order(order_id: str, ctx: Context) -> dict[str, Any]:
        """Get one of the signed-in customer's orders. Returns {"found": false} if unknown."""
        order = await orders_api.get_order(_authenticated_customer_id(ctx), order_id.upper())
        return (
            {"found": False} if order is None else {"found": True, **order.model_dump(mode="json")}
        )

    @server.tool()
    async def check_return_eligibility(
        order_id: str, sku: str, ctx: Context, quantity: int = 1
    ) -> dict[str, Any]:
        """Check whether an item of an order can be returned, and until when."""
        verdict = await orders_api.check_return_eligibility(
            _authenticated_customer_id(ctx), order_id.upper(), sku.upper(), quantity
        )
        return (
            {"found": False}
            if verdict is None
            else {"found": True, **verdict.model_dump(mode="json")}
        )

    @server.tool(name=CREATE_RETURN_TOOL_NAME)
    async def create_return_request(
        order_id: str, sku: str, quantity: int, reason: str, ctx: Context
    ) -> dict[str, Any]:
        """File a return. Requires a human confirmation token for exactly this request."""
        customer_id = _authenticated_customer_id(ctx)
        proposal = ReturnProposal(order_id=order_id, sku=sku, quantity=quantity, reason=reason)
        token = _request_meta(ctx).get(CONFIRMATION_META_KEY)
        if not isinstance(token, str):
            raise PermissionError("return requests need a human confirmation")
        try:
            verify_confirmation(
                token,
                subject=customer_id,
                action=proposal,
                trust=trust,
                allowed_issuers=frozenset({COORDINATOR}),
                replay_cache=replay_cache,
            )
        except TokenError as exc:
            raise PermissionError(f"confirmation refused: {exc}") from exc
        result = await orders_api.create_return(
            customer_id, ReturnCreate(order_id=order_id, sku=sku, quantity=quantity, reason=reason)
        )
        if result is None:
            return {"found": False}
        return {"found": True, **result.model_dump(mode="json")}

    return server


def main() -> None:
    """Stdio entry point, started by the orders agent as a subprocess."""
    settings = get_settings()
    orders_api = OrdersApiClient(httpx.AsyncClient(base_url=settings.orders_api_url, timeout=10))
    create_orders_server(orders_api, TrustStore.from_dir(settings.keys_dir)).run("stdio")


if __name__ == "__main__":
    main()
