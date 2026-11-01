"""Pick the model behind each agent from settings: scripted offline functions or a real LLM."""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

ScriptedModelFunction = Callable[[list[ModelMessage], AgentInfo], ModelResponse]
"""The function signature Pydantic AI's `FunctionModel` runs in place of an LLM."""


def build_model(model_name: str, offline_function: ScriptedModelFunction) -> Model | str:
    """`offline` runs `offline_function`, local and deterministic; any other value is passed to
    Pydantic AI as a model name, for example `google:gemini-2.5-flash`."""
    if model_name == "offline":
        return FunctionModel(offline_function, model_name=f"offline:{offline_function.__name__}")
    return model_name
