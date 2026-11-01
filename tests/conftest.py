"""Shared fixtures: the whole mesh assembled in one process.

Every service is the real ASGI app or MCP server; only the transports are swapped: httpx routes
by host name to in-process ASGI apps, and MCP toolsets talk to in-process servers instead of
stdio subprocesses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_mesh.config import CATALOG_AGENT, ORDERS_AGENT, Settings
from agent_mesh.coordinator.clients import (
    A2ASpecialistClient,
    InProcessSpecialistClient,
)
from agent_mesh.coordinator.graph import Coordinator
from agent_mesh.domain.catalog import load_catalog
from agent_mesh.domain.search import CatalogIndex
from agent_mesh.mcp_servers.catalog_server import create_catalog_server
from agent_mesh.mcp_servers.orders_server import create_orders_server
from agent_mesh.orders_api import OrdersApiClient, OrderStore, create_orders_app
from agent_mesh.security.keys import KeyUse, TrustStore, generate_keys, load_signing_key
from agent_mesh.services import (
    ALL_PRINCIPALS,
    build_catalog_specialist,
    build_coordinator,
    build_orders_specialist,
    coordinator_app,
    specialist_app,
)
from agent_mesh.specialists.agents import OrdersSpecialist
from agent_mesh.specialists.base import PydanticAISpecialist


class HostRouterTransport(httpx.AsyncBaseTransport):
    """Send each request to the in-process ASGI app registered for its host."""

    def __init__(self, apps: dict[str, Any]) -> None:
        self._transports = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transports[request.url.host].handle_async_request(request)


@pytest.fixture(scope="session")
def keys_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("keys")
    generate_keys(path, ALL_PRINCIPALS)
    return path


@pytest.fixture
def settings(tmp_path: Path, keys_dir: Path) -> Settings:
    return Settings(
        keys_dir=keys_dir,
        kill_switch_path=tmp_path / "kill_switch.json",
        orders_api_url="http://orders-api",
        catalog_agent_url="http://catalog-agent",
        orders_agent_url="http://orders-agent",
        coordinator_url="http://coordinator",
    )


@pytest.fixture
def trust(keys_dir: Path) -> TrustStore:
    return TrustStore.from_dir(keys_dir)


@pytest.fixture
def order_store() -> OrderStore:
    return OrderStore()


@pytest.fixture
async def orders_api(order_store: OrderStore) -> AsyncIterator[OrdersApiClient]:
    transport = httpx.ASGITransport(app=create_orders_app(order_store))
    async with httpx.AsyncClient(transport=transport, base_url="http://orders-api") as http:
        yield OrdersApiClient(http)


@pytest.fixture
def catalog_index(settings: Settings) -> CatalogIndex:
    return CatalogIndex(load_catalog(settings.catalog_path))


@pytest.fixture
def catalog_specialist(settings: Settings, catalog_index: CatalogIndex) -> PydanticAISpecialist:
    return build_catalog_specialist(settings, create_catalog_server(catalog_index))


@pytest.fixture
def orders_specialist(
    settings: Settings, orders_api: OrdersApiClient, trust: TrustStore
) -> OrdersSpecialist:
    return build_orders_specialist(settings, create_orders_server(orders_api, trust))


@pytest.fixture
def in_process_coordinator(
    settings: Settings,
    catalog_specialist: PydanticAISpecialist,
    orders_specialist: OrdersSpecialist,
) -> Coordinator:
    client = InProcessSpecialistClient(
        {CATALOG_AGENT: catalog_specialist, ORDERS_AGENT: orders_specialist}
    )
    return build_coordinator(settings, client)


@pytest.fixture
def specialist_apps(
    settings: Settings,
    catalog_specialist: PydanticAISpecialist,
    orders_specialist: OrdersSpecialist,
) -> dict[str, Any]:
    return {
        "catalog-agent": specialist_app(settings, catalog_specialist),
        "orders-agent": specialist_app(settings, orders_specialist),
    }


@pytest.fixture
def a2a_coordinator(
    settings: Settings, trust: TrustStore, specialist_apps: dict[str, Any]
) -> Coordinator:
    client = A2ASpecialistClient(
        token_key=load_signing_key(settings.keys_dir, "coordinator", KeyUse.TOKEN),
        trust=trust,
        agent_urls={
            CATALOG_AGENT: settings.catalog_agent_url,
            ORDERS_AGENT: settings.orders_agent_url,
        },
        token_ttl_seconds=60,
        transport=HostRouterTransport(specialist_apps),
    )
    return build_coordinator(settings, client)


@pytest.fixture
def mesh_transport(
    settings: Settings, a2a_coordinator: Coordinator, specialist_apps: dict[str, Any]
) -> HostRouterTransport:
    """Every service of the mesh, reachable by host name, coordinator included."""
    return HostRouterTransport(
        {**specialist_apps, "coordinator": coordinator_app(settings, a2a_coordinator)}
    )
