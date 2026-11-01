# LangGraph 1.2 overloads (add_node, compile, ainvoke) carry CachePolicy[Unknown] in their stubs.
# pyright: reportUnknownMemberType=false
"""The coordinator: a LangGraph state machine over the specialists.

    screen -> route -> call_specialists -> merge -> [confirm] -> END
       \\         \\
        DECLINE    DECLINE (out of scope)

- screen: injection screen on the user's message; a hit is DECLINE(prompt_injection).
- route: router agent picks specialists and the reply language.
- call_specialists: kill switch checked here and again at each specialist; calls run in parallel.
- merge: one outcome, by the rule in `merge_outcomes`.
- confirm: when the merged outcome is a CLARIFY carrying a `pending_action`, the graph pauses with
  `interrupt()`. Only `Coordinator.confirm(approved=True)` resumes it; then the coordinator signs a
  confirmation token bound to that exact action and the orders specialist files the return.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from opentelemetry import trace
from pydantic_ai import Agent

from agent_mesh.config import CATALOG_AGENT, COORDINATOR, ORDERS_AGENT
from agent_mesh.contracts import (
    Answer,
    Clarify,
    Decline,
    DeclineReason,
    Language,
    Outcome,
    OutcomeAdapter,
    ReturnProposal,
    UnavailableReason,
)
from agent_mesh.control.kill_switch import KillSwitch
from agent_mesh.coordinator.clients import SpecialistClient
from agent_mesh.coordinator.router import RouteDecision, RouteTarget
from agent_mesh.language import detect_language, localized
from agent_mesh.observability.tracing import INJECTION_EVENT, OUTCOME_ATTRIBUTE, messages_attribute
from agent_mesh.security.keys import SigningKey
from agent_mesh.security.screening import screen_for_injection
from agent_mesh.security.tokens import mint_confirmation
from agent_mesh.specialists.base import SpecialistRequest, unavailable

AGENT_FOR_ROUTE_TARGET: dict[RouteTarget, str] = {"catalog": CATALOG_AGENT, "orders": ORDERS_AGENT}
"""Which specialist serves each router target."""
_KIND_PRECEDENCE = ["CLARIFY", "DOC_GAP", "NOT_FOUND", "DECLINE", "UNAVAILABLE"]
"""Most actionable first when no specialist answered; see `merge_outcomes`."""

tracer = trace.get_tracer("agent_mesh.coordinator")


class MeshState(TypedDict):
    """Graph state. Outcomes are stored as JSON dicts so the checkpointer can serialize them."""

    text: str
    customer_id: str
    language: str
    targets: list[RouteTarget]
    specialist_outcomes: list[dict[str, Any]]
    final_outcome: NotRequired[dict[str, Any]]


StateUpdate = dict[str, Any]
"""Nodes return partial state updates; LangGraph merges them into `MeshState`."""


def _outcome_json(outcome: Outcome) -> dict[str, Any]:
    """JSON-safe dict, the only form the checkpointer can serialize."""
    return outcome.model_dump(mode="json")


def merge_outcomes(outcomes: list[Outcome], language: Language) -> Outcome:
    """Combine specialist outcomes into one.

    - A CLARIFY with a pending action always wins: the user must decide before anything else.
    - Otherwise all ANSWERs are joined, with every citation kept.
    - With no ANSWER, the first outcome by precedence CLARIFY, DOC_GAP, NOT_FOUND, DECLINE,
      UNAVAILABLE is returned, so the user sees the most actionable message.
    """
    for outcome in outcomes:
        if isinstance(outcome, Clarify) and outcome.pending_action is not None:
            return outcome
    answers = [o for o in outcomes if isinstance(o, Answer)]
    if len(answers) == 1:
        return answers[0]
    if answers:
        return Answer(
            language=language,
            message="\n\n".join(a.message for a in answers),
            citations=[c for a in answers for c in a.citations],
        )
    return sorted(outcomes, key=lambda o: _KIND_PRECEDENCE.index(o.kind))[0]


@dataclass(frozen=True)
class CoordinatorConfig:
    """Settings the coordinator needs, kept apart from `Settings` so tests build it directly."""

    model_name: str
    capture_message_content: bool
    confirmation_ttl_seconds: int


class Coordinator:
    """Runs the graph per message and remembers runs paused on a confirmation."""

    def __init__(
        self,
        *,
        specialist_client: SpecialistClient,
        router: Agent[None, RouteDecision],
        kill_switch: KillSwitch,
        confirmation_key: SigningKey,
        config: CoordinatorConfig,
    ) -> None:
        self._specialist_client = specialist_client
        self._router = router
        self._kill_switch = kill_switch
        self._confirmation_key = confirmation_key
        self._config = config
        self._paused_runs: dict[str, tuple[str, str]] = {}
        """conversation id -> (graph thread id, customer id) for runs paused on a confirmation."""
        self._graph = self._build_graph().compile(checkpointer=InMemorySaver())

    def _build_graph(self) -> StateGraph[MeshState]:
        graph = StateGraph(MeshState)
        graph.add_node("screen", self._screen)
        graph.add_node("route", self._route)
        graph.add_node("call_specialists", self._call_specialists)
        graph.add_node("merge", self._merge)
        graph.add_node("confirm", self._confirm)
        graph.add_edge(START, "screen")
        graph.add_conditional_edges("screen", _end_if_final_or("route"))
        graph.add_conditional_edges("route", _end_if_final_or("call_specialists"))
        graph.add_edge("call_specialists", "merge")
        graph.add_conditional_edges("merge", _needs_confirmation)
        graph.add_edge("confirm", END)
        return graph

    async def _screen(self, state: MeshState) -> StateUpdate:
        language = detect_language(state["text"])
        result = screen_for_injection(state["text"])
        if not result.flagged:
            return {"language": language.value}
        trace.get_current_span().add_event(INJECTION_EVENT, {"rules": list(result.rules)})
        return {
            "language": language.value,
            "final_outcome": _outcome_json(
                Decline(
                    language=language,
                    reason=DeclineReason.PROMPT_INJECTION,
                    message=localized(
                        language,
                        "I can't help with that request.",
                        "Je ne peux pas donner suite à cette demande.",
                    ),
                )
            ),
        }

    async def _route(self, state: MeshState) -> StateUpdate:
        result = await self._router.run(state["text"])
        decision = result.output
        if decision.targets:
            return {"targets": list(decision.targets), "language": decision.language.value}
        return {
            "language": decision.language.value,
            "final_outcome": _outcome_json(
                Decline(
                    language=decision.language,
                    reason=DeclineReason.OUT_OF_SCOPE,
                    message=localized(
                        decision.language,
                        "I can help with Kettlewick products, orders and returns.",
                        "Je peux vous aider sur les produits, commandes et retours Kettlewick.",
                    ),
                )
            ),
        }

    async def _call_specialist(self, target: RouteTarget, state: MeshState) -> Outcome:
        agent = AGENT_FOR_ROUTE_TARGET[target]
        language = Language(state["language"])
        if self._kill_switch.is_killed(agent):
            return unavailable(agent, language, UnavailableReason.KILL_SWITCH)
        request = SpecialistRequest(
            text=state["text"], customer_id=state["customer_id"], language=language
        )
        return await self._specialist_client.call(agent, request)

    async def _call_specialists(self, state: MeshState) -> StateUpdate:
        outcomes = await asyncio.gather(
            *(self._call_specialist(t, state) for t in state["targets"])
        )
        return {"specialist_outcomes": [_outcome_json(o) for o in outcomes]}

    async def _merge(self, state: MeshState) -> StateUpdate:
        outcomes = [OutcomeAdapter.validate_python(o) for o in state["specialist_outcomes"]]
        return {
            "final_outcome": _outcome_json(merge_outcomes(outcomes, Language(state["language"])))
        }

    async def _confirm(self, state: MeshState) -> StateUpdate:
        clarify = Clarify.model_validate(state.get("final_outcome"))
        action = clarify.pending_action
        if action is None:  # pragma: no cover - guarded by _needs_confirmation
            return {}
        approved = interrupt({"pending_action": action.model_dump(mode="json")})
        language = clarify.language
        if not approved:
            return {
                "final_outcome": _outcome_json(
                    Clarify(
                        language=language,
                        message=localized(
                            language,
                            "OK, nothing was filed. Anything else?",
                            "D'accord, rien n'a été déposé. Autre chose ?",
                        ),
                    )
                )
            }
        return {
            "final_outcome": _outcome_json(
                await self._file_confirmed_action(action, state["customer_id"], language)
            )
        }

    async def _file_confirmed_action(
        self, action: ReturnProposal, customer_id: str, language: Language
    ) -> Outcome:
        if self._kill_switch.is_killed(ORDERS_AGENT):
            return unavailable(ORDERS_AGENT, language, UnavailableReason.KILL_SWITCH)
        token = mint_confirmation(
            self._confirmation_key,
            subject=customer_id,
            action=action,
            ttl_seconds=self._config.confirmation_ttl_seconds,
        )
        request = SpecialistRequest(
            text=f"confirmed {action.action} {action.order_id} {action.sku}",
            customer_id=customer_id,
            language=language,
            confirmed_action=action,
            confirmation=token,
        )
        return await self._specialist_client.call(ORDERS_AGENT, request)

    async def ask(self, text: str, *, customer_id: str, conversation_id: str) -> Outcome:
        """Answer one user message. A CLARIFY with a pending action means the run is paused."""
        thread_id = uuid.uuid4().hex
        self._paused_runs.pop(conversation_id, None)
        with self._coordinator_span(conversation_id, text) as span:
            state = await self._graph.ainvoke(
                MeshState(
                    text=text,
                    customer_id=customer_id,
                    language="en",
                    targets=[],
                    specialist_outcomes=[],
                ),
                {"configurable": {"thread_id": thread_id}},
            )
            if "__interrupt__" in state:
                self._paused_runs[conversation_id] = (thread_id, customer_id)
            return self._record_outcome(span, state)

    async def confirm(self, *, customer_id: str, conversation_id: str, approved: bool) -> Outcome:
        """Resume a paused run with the user's decision. Only the same customer can resume it."""
        pending = self._paused_runs.get(conversation_id)
        if pending is None or pending[1] != customer_id:
            return Clarify(
                language=Language.EN, message="There is nothing waiting for your confirmation."
            )
        del self._paused_runs[conversation_id]
        thread_id, _ = pending
        with self._coordinator_span(conversation_id, f"confirm={approved}") as span:
            state = await self._graph.ainvoke(
                Command(resume=approved), {"configurable": {"thread_id": thread_id}}
            )
            return self._record_outcome(span, state)

    @contextmanager
    def _coordinator_span(self, conversation_id: str, text: str) -> Generator[trace.Span]:
        """The coordinator span, with the GenAI attributes the trace-to-golden pipeline reads.
        The customer id is deliberately not an attribute."""
        with tracer.start_as_current_span(f"invoke_agent {COORDINATOR}") as span:
            span.set_attribute("gen_ai.operation.name", "invoke_agent")
            span.set_attribute("gen_ai.agent.name", COORDINATOR)
            span.set_attribute("gen_ai.provider.name", self._config.model_name.split(":")[0])
            span.set_attribute("gen_ai.request.model", self._config.model_name)
            span.set_attribute("gen_ai.conversation.id", conversation_id)
            if self._config.capture_message_content:
                span.set_attribute("gen_ai.input.messages", messages_attribute("user", text))
            yield span

    def _record_outcome(self, span: trace.Span, state: dict[str, Any]) -> Outcome:
        outcome = OutcomeAdapter.validate_python(state["final_outcome"])
        span.set_attribute(OUTCOME_ATTRIBUTE, outcome.kind)
        if outcome.kind == "UNAVAILABLE":
            span.set_attribute("error.type", "unavailable")
            span.set_status(trace.StatusCode.ERROR)
        if self._config.capture_message_content:
            span.set_attribute(
                "gen_ai.output.messages", messages_attribute("assistant", outcome.message)
            )
        return outcome


def _end_if_final_or(next_node: str) -> Callable[[MeshState], str]:
    """Edge router: stop when a node already set the final outcome, else go to `next_node`."""

    def decide(state: MeshState) -> str:
        return END if "final_outcome" in state else next_node

    return decide


def _needs_confirmation(state: MeshState) -> str:
    """Edge router: pause on `confirm` only for a CLARIFY that carries a write to approve."""
    final = state.get("final_outcome", {})
    return "confirm" if final.get("kind") == "CLARIFY" and final.get("pending_action") else END
