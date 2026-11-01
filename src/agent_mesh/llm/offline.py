"""Scripted stand-ins for the LLM, used when `AGENT_MESH_MODEL=offline` (the default).

These are Pydantic AI `FunctionModel` functions: they read the conversation, call the same MCP
tools a real model would, and finish with the same output tools. Everything around them (A2A,
MCP, auth, grounding, tracing, the coordinator graph) runs for real. They exist so that
`docker compose up` works with no API key and no model download, and so CI is deterministic.
They are not a model and make no claim to answer quality; the live eval runs a real LLM.
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import date
from typing import Any

from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo

from agent_mesh.contracts import (
    Answer,
    Citation,
    Clarify,
    DocGap,
    Language,
    NotFound,
    ReturnProposal,
)
from agent_mesh.language import detect_language, localized

_LANGUAGE_PREFIX_PATTERN = re.compile(r"^\[reply language: (en|fr)\]\n")
_SKU_PATTERN = re.compile(r"\bKW-[A-Z]{3}-\d{3}\b", re.IGNORECASE)
_ORDER_ID_PATTERN = re.compile(r"\bORD-\d{4}\b", re.IGNORECASE)
_QUANTITY_PATTERN = re.compile(
    r"\b(\d{1,2})\s*(x|units?|pieces?|unités?|exemplaires?)\b", re.IGNORECASE
)
_RETURN_INTENT_PATTERN = re.compile(
    r"\b(return|refund|send back|retour\w*|rembours\w*|renvoy\w*)\b", re.I
)
_LIST_INTENT_PATTERN = re.compile(r"\b(my orders|mes commandes|order history|historique)\b", re.I)

ATTRIBUTE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "power_w": ("power", "watt", "puissance"),
    "capacity_l": ("capacity", "litre", "liter", "capacité", "contenance"),
    "noise_db": ("noise", "loud", "quiet", "bruit", "silencieux", "bruyant"),
    "warranty_years": ("warranty", "garantie"),
    "battery_min": ("battery", "batterie", "autonomie"),
    "weight_kg": ("weight", "heavy", "poids", "lourd"),
}


def _user_prompt_and_language(messages: list[ModelMessage]) -> tuple[str, Language]:
    """The user's question without the specialist's `[reply language: xx]` prefix, and that language."""
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    match = _LANGUAGE_PREFIX_PATTERN.match(part.content)
                    text = _LANGUAGE_PREFIX_PATTERN.sub("", part.content)
                    language = Language(match.group(1)) if match else detect_language(text)
                    return text, language
    return "", Language.EN


def _latest_tool_results(messages: list[ModelMessage]) -> dict[str, Any]:
    """Latest result of each tool, decoded from JSON when the transport kept it as text."""
    results: dict[str, Any] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                content = part.content
                if isinstance(content, str):
                    with contextlib.suppress(json.JSONDecodeError):
                        content = json.loads(content)
                results[part.tool_name] = content
    return results


def _tool_call(tool_name: str, args: dict[str, Any]) -> ModelResponse:
    """A model turn that calls one tool, as a real model would."""
    return ModelResponse(parts=[ToolCallPart(tool_name, args)])


def _final_output(info: AgentInfo, outcome: BaseModel) -> ModelResponse:
    """End the run through the output tool Pydantic AI generated for this outcome's type.

    The tool is looked up in `info.output_tools` (one per output type, named after the class)
    rather than hard-coding Pydantic AI's naming scheme.
    """
    type_name = type(outcome).__name__
    tool_name = next(t.name for t in info.output_tools if t.name.endswith(type_name))
    return _tool_call(tool_name, outcome.model_dump(mode="json"))


def asked_attribute(text: str) -> str | None:
    """The catalog attribute a question asks about, from EN and FR keywords."""
    lowered = text.lower()
    for attribute, words in ATTRIBUTE_KEYWORDS.items():
        if any(w in lowered for w in words):
            return attribute
    return None


def catalog_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Scripted catalog agent: look the product up, then answer, DOC_GAP or NOT_FOUND."""
    text, language = _user_prompt_and_language(messages)
    returns = _latest_tool_results(messages)
    sku = _SKU_PATTERN.search(text)
    if "get_product" not in returns and "search_products" not in returns:
        if sku:
            return _tool_call("get_product", {"sku": sku.group(0).upper()})
        return _tool_call("search_products", {"query": text, "limit": 3})
    if "get_product" in returns:
        found = returns["get_product"]
        products = [found] if found.get("found") else []
    else:
        products = list(returns["search_products"])
    if not products:
        return _final_output(
            info,
            NotFound(
                language=language,
                message=localized(
                    language,
                    "I could not find a matching product in our catalog.",
                    "Je n'ai trouvé aucun produit correspondant dans notre catalogue.",
                ),
            ),
        )
    top = products[0]
    attribute = asked_attribute(text)
    name = top["name_fr"] if language is Language.FR else top["name_en"]
    if attribute and attribute not in top["attributes"]:
        return _final_output(
            info,
            DocGap(
                language=language,
                subject_id=top["sku"],
                message=localized(
                    language,
                    f"Our catalog does not list the {attribute} for {name} ({top['sku']}).",
                    f"Notre catalogue n'indique pas {attribute} pour {name} ({top['sku']}).",
                ),
            ),
        )
    lines: list[str] = []
    for product in products[:3]:
        label = product["name_fr"] if language is Language.FR else product["name_en"]
        detail = f"{product['price_eur']} EUR / {product['price_aed']} AED"
        if attribute:
            detail = f"{attribute} {product['attributes'].get(attribute, '?')}, {detail}"
        lines.append(f"{label} ({product['sku']}): {detail}")
    today = date.today()
    return _final_output(
        info,
        Answer(
            language=language,
            message="\n".join(lines),
            citations=[
                Citation(doc_id=p["sku"], source="catalog", retrieved_on=today)
                for p in products[:3]
            ],
        ),
    )


_INELIGIBLE_REASON_TEXT = {
    "sku_not_in_order": (
        "That item is not part of this order.",
        "Cet article ne fait pas partie de cette commande.",
    ),
    "category_excluded": (
        "This product category cannot be returned in your country.",
        "Cette catégorie de produit ne peut pas être retournée dans votre pays.",
    ),
    "not_delivered_yet": (
        "This order has not been delivered yet.",
        "Cette commande n'a pas encore été livrée.",
    ),
    "window_expired": ("The return window has closed.", "Le délai de retour est dépassé."),
    "quantity_exceeds_purchase": (
        "You cannot return more units than you bought.",
        "Vous ne pouvez pas retourner plus d'unités que vous n'en avez achetées.",
    ),
}


def orders_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:  # noqa: PLR0911
    """Scripted orders agent: status, list, or eligibility check then a return proposal."""
    text, language = _user_prompt_and_language(messages)
    returns = _latest_tool_results(messages)
    today = date.today()
    order_match = _ORDER_ID_PATTERN.search(text)
    sku_match = _SKU_PATTERN.search(text)
    wants_return = bool(_RETURN_INTENT_PATTERN.search(text))

    if order_match is None:
        if _LIST_INTENT_PATTERN.search(text) and "list_orders" not in returns:
            return _tool_call("list_orders", {})
        if "list_orders" in returns:
            orders = list(returns["list_orders"])
            lines = [f"{o['order_id']}: {o['status']} ({o['placed_on']})" for o in orders]
            return _final_output(
                info,
                Answer(
                    language=language,
                    message="\n".join(lines)
                    or localized(language, "No orders yet.", "Aucune commande."),
                    citations=[
                        Citation(doc_id=o["order_id"], source="orders", retrieved_on=today)
                        for o in orders
                    ],
                )
                if orders
                else NotFound(
                    language=language,
                    message=localized(
                        language, "You have no orders yet.", "Vous n'avez pas encore de commande."
                    ),
                ),
            )
        return _final_output(
            info,
            Clarify(
                language=language,
                message=localized(
                    language,
                    "Which order is this about? Please give the order number (ORD-....).",
                    "De quelle commande s'agit-il ? Merci d'indiquer le numéro (ORD-....).",
                ),
            ),
        )

    order_id = order_match.group(0).upper()
    if "get_order" not in returns:
        return _tool_call("get_order", {"order_id": order_id})
    order = returns["get_order"]
    if not order.get("found"):
        return _final_output(
            info,
            NotFound(
                language=language,
                message=localized(
                    language,
                    f"I could not find order {order_id} on your account.",
                    f"Je ne trouve pas la commande {order_id} sur votre compte.",
                ),
            ),
        )
    citation = Citation(doc_id=order_id, source="orders", retrieved_on=today)
    if not wants_return:
        delivered = order.get("delivered_on") or localized(language, "not yet", "pas encore")
        return _final_output(
            info,
            Answer(
                language=language,
                message=localized(
                    language,
                    f"Order {order_id} is {order['status']}. Delivered: {delivered}.",
                    f"La commande {order_id} est {order['status']}. Livrée : {delivered}.",
                ),
                citations=[citation],
            ),
        )
    skus = [line["sku"] for line in order["lines"]]
    sku = sku_match.group(0).upper() if sku_match else (skus[0] if len(skus) == 1 else None)
    if sku is None:
        return _final_output(
            info,
            Clarify(
                language=language,
                message=localized(
                    language,
                    f"Which item of {order_id} do you want to return? {', '.join(skus)}",
                    f"Quel article de {order_id} souhaitez-vous retourner ? {', '.join(skus)}",
                ),
            ),
        )
    quantity_match = _QUANTITY_PATTERN.search(text)
    quantity = int(quantity_match.group(1)) if quantity_match else 1
    if "check_return_eligibility" not in returns:
        return _tool_call(
            "check_return_eligibility", {"order_id": order_id, "sku": sku, "quantity": quantity}
        )
    verdict = returns["check_return_eligibility"]
    if not verdict.get("eligible"):
        en, fr = _INELIGIBLE_REASON_TEXT.get(
            verdict.get("reason", ""),
            ("This item cannot be returned.", "Cet article ne peut pas être retourné."),
        )
        return _final_output(
            info,
            Answer(
                language=language,
                message=localized(language, en, fr),
                citations=[
                    citation,
                    Citation(doc_id=order_id, source="return_policy", retrieved_on=today),
                ],
            ),
        )
    proposal = ReturnProposal(order_id=order_id, sku=sku, quantity=quantity, reason=text[:200])
    return _final_output(
        info,
        Clarify(
            language=language,
            pending_action=proposal,
            message=localized(
                language,
                f"{sku} from {order_id} can be returned until {verdict['deadline']}. Shall I file the return for {quantity} unit(s)?",
                f"{sku} de la commande {order_id} peut être retourné jusqu'au {verdict['deadline']}. Je dépose la demande de retour pour {quantity} unité(s) ?",
            ),
        ),
    )
