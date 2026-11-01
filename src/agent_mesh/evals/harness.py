"""Assemble the whole mesh in one process for evals: same agents, in-process transports."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from agent_mesh.config import CATALOG_AGENT, ORDERS_AGENT, Settings
from agent_mesh.coordinator.clients import InProcessSpecialistClient
from agent_mesh.coordinator.graph import Coordinator
from agent_mesh.domain.catalog import load_catalog
from agent_mesh.domain.search import CatalogIndex
from agent_mesh.mcp_servers.catalog_server import create_catalog_server
from agent_mesh.mcp_servers.orders_server import create_orders_server
from agent_mesh.orders_api import OrdersApiClient, create_orders_app
from agent_mesh.security.keys import TrustStore, generate_keys
from agent_mesh.services import (
    ALL_PRINCIPALS,
    build_catalog_specialist,
    build_coordinator,
    build_orders_specialist,
)
from agent_mesh.specialists.agents import OrdersSpecialist
from agent_mesh.specialists.base import PydanticAISpecialist


@dataclass(frozen=True)
class InProcessMesh:
    """The three agents under test, wired in process."""

    coordinator: Coordinator
    catalog: PydanticAISpecialist
    orders: OrdersSpecialist


@asynccontextmanager
async def in_process_mesh(model: str) -> AsyncGenerator[InProcessMesh]:
    """Fresh keys, fresh order store and an empty kill switch for every run."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(
            model=model, keys_dir=root / "keys", kill_switch_path=root / "kill_switch.json"
        )
        generate_keys(settings.keys_dir, ALL_PRINCIPALS)
        transport = httpx.ASGITransport(app=create_orders_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://orders-api") as http:
            orders_mcp = create_orders_server(
                OrdersApiClient(http), TrustStore.from_dir(settings.keys_dir)
            )
            catalog_mcp = create_catalog_server(CatalogIndex(load_catalog(settings.catalog_path)))
            catalog = build_catalog_specialist(settings, catalog_mcp)
            orders = build_orders_specialist(settings, orders_mcp)
            client = InProcessSpecialistClient({CATALOG_AGENT: catalog, ORDERS_AGENT: orders})
            yield InProcessMesh(build_coordinator(settings, client), catalog, orders)
