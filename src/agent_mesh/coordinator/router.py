"""Decides which specialists a question needs, and in which language to answer."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo

from agent_mesh.contracts import Language
from agent_mesh.language import detect_language
from agent_mesh.llm.models import build_model

RouteTarget = Literal["catalog", "orders"]
"""Specialist families the router can pick; the coordinator maps each to an agent."""


class RouteDecision(BaseModel):
    """The router's structured output."""

    targets: list[RouteTarget]
    """Empty means out of scope."""
    language: Language
    """Reply language for every specialist the question goes to."""


ROUTER_INSTRUCTIONS = """\
Route a customer message for Kettlewick, a kitchen and small home appliance store.
targets: "catalog" for product questions (features, prices, availability, comparisons),
"orders" for the customer's orders, deliveries and returns. Use both if the message asks both.
Use an empty list for anything else. language: the language of the message (en or fr).
"""

_ORDERS_KEYWORDS = re.compile(
    r"\b(orders?|commandes?|ord-\d+|deliver\w*|livr\w*|return\w*|retour\w*|refund\w*|"
    r"rembours\w*|track\w*|suivi|colis|parcel)\b",
    re.IGNORECASE,
)
_CATALOG_KEYWORDS = re.compile(
    r"\b(kw-[a-z]{3}-\d{3}|kettle|bouilloire|toaster|grille-pain|blender|mixeur|air fryer|"
    r"friteuse|rice cooker|cuiseur|grinder|moulin|mixer|batteur|fan|ventilateur|humidifier|"
    r"humidificateur|vacuum|aspirateur|price|prix|cost|warranty|garantie|power|puissance|"
    r"capacity|capacit\w*|noise|bruit|quiet|silencieux|products?|produits?|sell|vendez|recommend\w*)\b",
    re.IGNORECASE,
)


def offline_router_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Offline keyword router: same output shape as the LLM router, picked by `build_model`.

    It answers through the agent's own output tool (`info.output_tools`), so the run ends the way
    a real model's structured output would.
    """
    text = ""
    for message in messages:
        for part in message.parts:
            content = getattr(part, "content", None)
            if part.part_kind == "user-prompt" and isinstance(content, str):
                text = content
    targets: list[RouteTarget] = []
    if _CATALOG_KEYWORDS.search(text):
        targets.append("catalog")
    if _ORDERS_KEYWORDS.search(text):
        targets.append("orders")
    decision = RouteDecision(targets=targets, language=detect_language(text))
    return ModelResponse(
        parts=[ToolCallPart(info.output_tools[0].name, decision.model_dump(mode="json"))]
    )


def build_router(model_name: str) -> Agent[None, RouteDecision]:
    """A tool-less agent: routing needs no data, so it gets none."""
    return Agent(
        build_model(model_name, offline_router_model),
        name="router",
        output_type=RouteDecision,
        instructions=ROUTER_INSTRUCTIONS,
    )
