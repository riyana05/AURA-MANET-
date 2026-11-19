"""
state.py — Shared LangGraph state for the AURA-MANET agentic AI pipeline.

Every node receives the full state and returns a *partial* dict with only the
keys it owns.  All values are plain JSON-compatible data (Pydantic models are
stored via ``model_dump()``), so the final state can be returned directly by
the API and written to disk for the visualizer.

Pipeline (see ``graph.py``)::

    ingest ─► monitoring ─┬─► diagnosis ─┬─► routing ─► orchestrator ─► validation
                          └─► security  ─┘                                  │
                                                     verification ◄─ execution
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AuraState(TypedDict, total=False):
    """Shared state passed between every agent node."""

    # ------------------------------------------------------------------ #
    # Run / time                                                           #
    # ------------------------------------------------------------------ #
    simulation_id: str
    current_time: float
    data_dir: str
    protocol: str
    comm_range_m: float
    data_availability: dict[str, bool]
    """Which evidence exists (energy / forwarding are optional ns-3 exports)."""

    # ------------------------------------------------------------------ #
    # Ingestion (filled by data_loader.build_state)                        #
    # ------------------------------------------------------------------ #
    raw_metrics: dict
    """network_metrics.csv row at the latest time <= current_time."""
    previous_metrics: dict
    """The row before ``raw_metrics`` (empty at the first sample)."""
    mobility_snapshot: list[dict]
    """Latest node_mobility row per node."""
    mobility_history: list[dict]
    """node_mobility rows in ``[t - ISOLATION_WINDOW_S, t]`` (several per node)."""
    packets_snapshot: list[dict]
    """Packets sent or received in ``(t - 1, t]``."""
    flow_stats: list[dict]
    """Per-flow delivery in ``(t - FLOW_WINDOW_S, t]`` (no future knowledge)."""
    window_metrics: dict
    """Network-wide WindowMetrics over the same window."""
    energy_snapshot: list[dict]
    forwarding_snapshot: list[dict]

    # ------------------------------------------------------------------ #
    # Agent outputs                                                        #
    # ------------------------------------------------------------------ #
    topology: dict
    """Nodes / links at current_time (``topology.Topology.to_dict``)."""
    anomalies: list[dict]
    """MonitoringAgent -> list of ``schemas.Anomaly``."""
    rag_context: list[dict]
    """Retrieved knowledge chunks (``schemas.RetrievedChunk``)."""
    diagnosis: dict
    """DiagnosisAgent -> ``schemas.Diagnosis``."""
    security_report: dict
    """Security & Energy agent -> ``schemas.SecurityReport``."""
    candidate_routes: list[dict]
    """RoutingAgent -> list of ``schemas.FlowRouting``."""
    recovery_action: dict
    """OrchestratorAgent -> ``schemas.RecoveryAction``."""
    validation: dict
    """Action validator -> ``schemas.ValidationResult``."""
    execution_result: dict
    """Executor -> ``schemas.ExecutionResult``."""
    verification_result: dict
    """RecoveryVerificationAgent -> ``schemas.VerificationResult``."""
    baseline_action: dict
    """Rule-based baseline decision for the same input (for comparison)."""

    # ------------------------------------------------------------------ #
    # Bookkeeping                                                          #
    # ------------------------------------------------------------------ #
    trace: Annotated[list[dict[str, Any]], operator.add]
    """Append-only log of ``{"agent": ..., "summary": ...}`` entries."""
    error: str | None
    """Set by a node on a non-recoverable error; later nodes short-circuit."""


# Backwards-compatible alias (older code referred to the state as MANETState)
MANETState = AuraState
