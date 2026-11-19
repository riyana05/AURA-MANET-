"""
llm.py — Thin wrapper around the Groq chat model with structured output.

Agents depend on the small :class:`StructuredLLM` protocol, not on LangChain
directly, so tests can inject a fake model and the pipeline still runs (in
rule-based mode) when no API key is configured.
"""

from __future__ import annotations

import logging
from typing import Protocol, TypeVar

from pydantic import BaseModel

from config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    """Anything that turns (system, user) prompts into a validated model."""

    name: str

    def generate(self, system: str, user: str, schema: type[T]) -> T: ...


class GroqStructuredLLM:
    """Groq chat model (via ``langchain-groq``) returning Pydantic objects."""

    def __init__(self, cfg: Settings, api_key: str):
        from langchain_groq import ChatGroq

        self.name = f"groq:{cfg.LLM_MODEL}"
        self._chat = ChatGroq(
            model=cfg.LLM_MODEL,
            api_key=api_key,
            temperature=cfg.LLM_TEMPERATURE,
            timeout=cfg.LLM_TIMEOUT_S,
            max_retries=2,
        )

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        runnable = self._chat.with_structured_output(schema)
        result = runnable.invoke([("system", system), ("human", user)])
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)  # dict output -> validate


def get_llm(cfg: Settings) -> StructuredLLM | None:
    """Return the configured LLM, or ``None`` when no Groq key is set."""
    key = cfg.groq_key()
    if key is None:
        logger.info("GROQ_API_KEY not set: Diagnosis agent will use rule-based mode")
        return None
    try:
        return GroqStructuredLLM(cfg, key)
    except Exception as exc:  # noqa: BLE001 - missing package etc.
        logger.warning("Could not initialise Groq LLM (%s); using rule-based mode", exc)
        return None
