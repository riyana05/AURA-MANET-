"""
graph.py — LangGraph orchestration of the AURA-MANET agents.

::

    START -> monitoring -+-> diagnosis -+-> routing -> orchestrator -> validation
                         +-> security  -+                                   |
                         |                                                  v
                         +-(no anomalies)-> orchestrator          execution
                                                                            |
                           END <- baseline <- what_if <- verification <--+

* Diagnosis (LLM + RAG) and Security run in parallel; Routing waits for both.
* With no anomalies the expensive branch is skipped.
* Any node that sets ``error`` makes the rest short-circuit to END.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from agents.action_validator import validate_action
from agents.diagnosis_agent import make_diagnosis_agent
from agents.monitoring_agent import monitoring_agent
from agents.orchestrator_agent import build_action, orchestrator_agent
from agents.recovery_executor import Executor
from agents.routing_agent import routing_agent
from agents.security_agent import security_agent
from agents.verification_agent import verify
from baseline import baseline_decide
from config import Settings
from llm import StructuredLLM
from state import AuraState

logger = logging.getLogger(__name__)


def build_graph(
    cfg: Settings,
    llm: StructuredLLM | None,
    retrieve,
    executor: Executor,
    record_case=None,
):
    """Compile the agent graph with its dependencies injected.

    Args:
        cfg:         Settings.
        llm:         Structured LLM or ``None`` (rule-based diagnosis).
        retrieve:    ``retrieve(query, k) -> list[RetrievedChunk]`` or ``None``.
        executor:    Recovery executor (dry run or ns-3 re-run).
        record_case: Optional ``callable(state)`` storing verified fault cases.
    """

    def validation_node(state: AuraState) -> dict:
        if state.get("error"):
            return {}
        v = validate_action(state["recovery_action"], state, cfg, executor.backend, executor.ready())
        summary = f"valid={v.valid} executable={v.executable}" + (f" errors={v.errors}" if v.errors else "")
        return {"validation": v.model_dump(), "trace": [{"agent": "validation", "summary": summary}]}

    def execution_node(state: AuraState) -> dict:
        if state.get("error"):
            return {}
        try:
            res = executor.execute(state["recovery_action"], state["validation"], state)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Executor failed")
            from schemas import ExecutionResult

            res = ExecutionResult(status="failed", backend=executor.backend, details=str(exc))
        return {"execution_result": res.model_dump(),
                "trace": [{"agent": "execution", "summary": f"{res.status}: {res.details[:120]}"}]}

    def verification_node(state: AuraState) -> dict:
        if state.get("error"):
            return {}
        try:
            v = verify(state, cfg)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Verification failed")
            return {"verification_result": {"status": "insufficient_data", "notes": [str(exc)]},
                    "trace": [{"agent": "verification", "summary": f"failed: {exc}"}]}
        out = {"verification_result": v.model_dump(),
               "trace": [{"agent": "verification", "summary": f"{v.status} ({v.mode})"}]}
        if record_case is not None:
            try:
                record_case({**state, **out})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not record fault case: %s", exc)
        return out

    def recommendation_check_node(state: AuraState) -> dict:
        """If the diagnosis recommended something else than the final action, test it too."""
        if state.get("error"):
            return {}
        rec = (state.get("diagnosis") or {}).get("recommended_action")
        final = (state.get("recovery_action") or {}).get("action_type")
        if not rec or rec == final:
            out = {"status": "same_as_final", "recommended": rec}
        elif rec in {"no_action", "wait_for_reconnection"}:
            out = {"status": "nothing_to_execute", "recommended": rec}
        else:
            action = build_action(rec, state, cfg)
            if action is None:
                out = {"status": "not_applicable", "recommended": rec}
            else:
                v = validate_action(action.model_dump(), state, cfg, executor.backend, executor.ready())
                try:
                    ex = executor.execute(action.model_dump(), v.model_dump(), state)
                    ver = verify({**state, "execution_result": ex.model_dump()}, cfg)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("What-if check failed: %s", exc)
                    out = {"status": "failed", "recommended": rec, "error": str(exc)}
                else:
                    out = {"status": "tested", "recommended": rec, "action": action.model_dump(),
                           "validation": v.model_dump(), "execution": ex.model_dump(),
                           "verification": ver.model_dump()}
        summary = out["status"] + (f" ({out['recommended']})" if out.get("recommended") else "")
        if out.get("verification"):
            summary += f" -> {out['verification']['status']}"
        return {"recommendation_check": out,
                "trace": [{"agent": "what_if", "summary": summary}]}

    def baseline_node(state: AuraState) -> dict:
        if state.get("error"):
            return {}
        action = baseline_decide(state, cfg)
        v = validate_action(action.model_dump(), state, cfg, executor.backend, executor.ready())
        return {"baseline_action": {"action": action.model_dump(), "validation": v.model_dump()},
                "trace": [{"agent": "baseline", "summary": f"{action.action_type} (valid={v.valid})"}]}

    def after_monitoring(state: AuraState):
        if state.get("error"):
            return END
        if not state.get("anomalies"):
            return "orchestrator"
        return ["diagnosis", "security"]

    def stop_on_error(next_node: str):
        return lambda state: END if state.get("error") else next_node

    g = StateGraph(AuraState)
    g.add_node("monitoring", monitoring_agent)
    g.add_node("diagnosis", make_diagnosis_agent(llm, retrieve, cfg))
    g.add_node("security", security_agent)
    g.add_node("routing", routing_agent)
    g.add_node("orchestrator", orchestrator_agent)
    g.add_node("validation", validation_node)
    g.add_node("execution", execution_node)
    g.add_node("verification", verification_node)
    g.add_node("what_if", recommendation_check_node)
    g.add_node("baseline", baseline_node)

    g.add_edge(START, "monitoring")
    g.add_conditional_edges("monitoring", after_monitoring, ["diagnosis", "security", "orchestrator", END])
    g.add_edge(["diagnosis", "security"], "routing")
    g.add_conditional_edges("routing", stop_on_error("orchestrator"), ["orchestrator", END])
    g.add_conditional_edges("orchestrator", stop_on_error("validation"), ["validation", END])
    g.add_edge("validation", "execution")
    g.add_edge("execution", "verification")
    g.add_edge("verification", "what_if")
    g.add_edge("what_if", "baseline")
    g.add_edge("baseline", END)
    return g.compile()
