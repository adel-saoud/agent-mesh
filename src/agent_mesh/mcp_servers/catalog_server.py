"""Catalog MCP server: read-only product search over the synthetic catalog.

Runs over stdio as a subprocess of the catalog agent, so only that agent can reach it.
Product reviews are third-party text: each one goes through the injection screen and flagged
reviews are withheld before the model ever sees them (indirect prompt injection).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from agent_mesh.config import get_settings
from agent_mesh.domain.catalog import Product, load_catalog
from agent_mesh.domain.search import CatalogIndex
from agent_mesh.security.screening import WITHHELD_REVIEW, screen_for_injection

MAX_SEARCH_RESULTS = 5
"""Caps what the model can ask for, so one search cannot pull the whole catalog into context."""


def product_view(product: Product) -> dict[str, Any]:
    """What the model sees about a product. Flagged reviews are replaced, never passed through."""
    reviews = [
        {
            "rating": r.rating,
            "text": WITHHELD_REVIEW if screen_for_injection(r.text).flagged else r.text,
        }
        for r in product.reviews
    ]
    return {
        "sku": product.sku,
        "name_en": product.name_en,
        "name_fr": product.name_fr,
        "description_en": product.description_en,
        "description_fr": product.description_fr,
        "price_eur": product.price_eur,
        "price_aed": product.price_aed,
        "attributes": product.attributes,
        "reviews": reviews,
    }


def create_catalog_server(index: CatalogIndex) -> MCPServer:
    """Build the server over an index; tests pass a prebuilt one to skip the subprocess."""
    server = MCPServer(
        "kettlewick-catalog",
        instructions="Read-only search over the Kettlewick product catalog.",
    )

    @server.tool()
    def search_products(query: str, limit: int = 3) -> list[dict[str, Any]]:
        """Search products by keywords (English or French). Returns an empty list if none match."""
        return [
            product_view(hit.product)
            for hit in index.search(query, limit=min(limit, MAX_SEARCH_RESULTS))
        ]

    @server.tool()
    def get_product(sku: str) -> dict[str, Any]:
        """Get one product by SKU, for example KW-KET-002. Returns {"found": false} if unknown."""
        product = index.get_by_sku(sku)
        return {"found": False} if product is None else {"found": True, **product_view(product)}

    return server


def main() -> None:
    """Stdio entry point, started by the catalog agent as a subprocess."""
    create_catalog_server(CatalogIndex(load_catalog(get_settings().catalog_path))).run("stdio")


if __name__ == "__main__":
    main()
