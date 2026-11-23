"""
llm.py — Thin wrapper around the Groq chat model with structured output.

Agents depend on the small :class:`StructuredLLM` protocol, not on LangChain
directly, so tests can inject a fake model and the pipeline still runs (in
rule-based mode) when no API key is configured.
"""

from __future__ import annotations

import logging
import time
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
        self.last_call: dict = {}
        self._chat = ChatGroq(
            model=cfg.LLM_MODEL,
            api_key=api_key,
            temperature=cfg.LLM_TEMPERATURE,
            timeout=cfg.LLM_TIMEOUT_S,
            max_retries=2,
        )

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        """Call Groq; ``self.last_call`` records proof of the call (model, tokens, time)."""
        runnable = self._chat.with_structured_output(schema, include_raw=True)
        start = time.perf_counter()
        out = runnable.invoke([("system", system), ("human", user)])
        raw = out.get("raw")
        meta = getattr(raw, "response_metadata", {}) or {}
        usage = meta.get("token_usage") or {}
        self.last_call = {
            "provider": "groq",
            "model": meta.get("model_name"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "latency_s": round(time.perf_counter() - start, 2),
        }
        logger.info("LLM call: %s", self.last_call)
        if out.get("parsing_error") is not None:
            raise ValueError(f"LLM output did not match the schema: {out['parsing_error']}")
        result = out.get("parsed")
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
