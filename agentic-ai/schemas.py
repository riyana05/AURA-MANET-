"""
schemas.py — Pydantic models for every structured output in the pipeline.

Agents build these models (which validates them) and store ``model_dump()``
dicts in the LangGraph state, so the state always stays JSON-serialisable and
can be returned unchanged by the FastAPI endpoints.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]
SEVERITY_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# Every action the system may recommend.  Ordered from least to most disruptive.
ActionType = Literal[
    "no_action",
    "wait_for_reconnection",
    "reroute_flow",
    "increase_tx_range",
    "switch_protocol",
    "isolate_node",
]
ACTION_DISRUPTION: dict[str, int] = {
    "no_action": 0,
    "wait_for_reconnection": 0,
    "reroute_flow": 1,
    "increase_tx_range": 2,
    "switch_protocol": 3,
    "isolate_node": 3,
}

RoutingProtocol = Literal["AODV", "OLSR", "DSDV", "DSR"]


# --------------------------------------------------------------------------- #
# Monitoring                                                                   #
# --------------------------------------------------------------------------- #


class Anomaly(BaseModel):
    """One anomaly detected by the MonitoringAgent (deterministic)."""

    type: str
    severity: Severity
    value: float | None = None
    threshold: float | None = None
    description: str
    timestamp: float
    affected_nodes: list[int] = Field(default_factory=list)
    flow_id: int | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# RAG                                                                          #
# --------------------------------------------------------------------------- #


class RetrievedChunk(BaseModel):
    """A knowledge-base passage returned by the retriever."""

    chunk_id: str
    source: str
    text: str
    score: float


# --------------------------------------------------------------------------- #
# Diagnosis                                                                    #
# --------------------------------------------------------------------------- #


class DiagnosisFinding(BaseModel):
    """One probable fault with its supporting evidence."""

    fault: str = Field(description="Short fault label, e.g. 'topology_partition'.")
    confidence: float = Field(ge=0.0, le=1.0)
    affected_nodes: list[int] = Field(default_factory=list)
    anomaly_types: list[str] = Field(
        default_factory=list, description="Anomaly types this finding explains."
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Observations from the supplied data that support the finding.",
    )


class LLMDiagnosisOutput(BaseModel):
    """Schema the LLM must fill (validated before it enters the state)."""

    root_cause: str = Field(description="One-sentence most probable root cause.")
    findings: list[DiagnosisFinding]
    recommended_action: ActionType = Field(
        description="Single recommended action type from the allowed list."
    )
    reasoning: str = Field(description="Short explanation linking evidence to cause.")
    cited_chunk_ids: list[str] = Field(
        default_factory=list,
        description="chunk_id values of the knowledge passages that were used.",
    )


class Diagnosis(BaseModel):
    """Diagnosis stored in the state (LLM or rule-based)."""

    root_cause: str
    findings: list[DiagnosisFinding] = Field(default_factory=list)
    recommended_action: ActionType = "no_action"
    reasoning: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    method: Literal["llm_rag", "rule_based", "none"]
    sources: list[RetrievedChunk] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Security & energy                                                            #
# --------------------------------------------------------------------------- #


class NodeAssessment(BaseModel):
    """Trust / energy assessment of one node.  ``None`` = data not available."""

    node_id: int
    trust: float | None = None
    trust_evidence: str = "no forwarding data"
    energy_j: float | None = None
    degree: int = 0
    suspicious: bool = False
    low_energy: bool = False
    reasons: list[str] = Field(default_factory=list)


class SecurityReport(BaseModel):
    """Output of the Security & Energy agent."""

    nodes: list[NodeAssessment] = Field(default_factory=list)
    suspicious_nodes: list[int] = Field(default_factory=list)
    low_energy_nodes: list[int] = Field(default_factory=list)
    risk_level: Literal["none", "low", "medium", "high", "unknown"] = "unknown"
    data_available: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Routing                                                                      #
# --------------------------------------------------------------------------- #


class RouteCandidate(BaseModel):
    """A candidate path for one flow, with deterministic scores."""

    flow_id: int
    source: int
    destination: int
    path: list[int]
    hop_count: int
    total_distance_m: float
    min_link_margin: float = Field(
        description="Smallest (1 - distance/range) over the path's links (0..1)."
    )
    min_link_lifetime_s: float | None = Field(
        default=None,
        description="Predicted time until the weakest link breaks (capped).",
    )
    min_trust: float | None = None
    min_energy_j: float | None = None
    score: float = 0.0
    score_components: dict[str, float] = Field(default_factory=dict)
    rejected: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)


class FlowRouting(BaseModel):
    """All candidates for one affected flow."""

    flow_id: int
    source: int
    destination: int
    connected: bool
    window_pdr: float | None = None
    candidates: list[RouteCandidate] = Field(default_factory=list)
    best: RouteCandidate | None = None
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Orchestration, validation, execution, verification                           #
# --------------------------------------------------------------------------- #


class RecoveryAction(BaseModel):
    """The action chosen by the orchestrator (or the baseline)."""

    action_type: ActionType
    target_nodes: list[int] = Field(default_factory=list)
    flow_id: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    decided_by: Literal["orchestrator", "baseline"] = "orchestrator"
    alternatives: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """Result of validating an action before execution."""

    valid: bool
    executable: bool = False
    backend: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    """What actually happened when the action was handed to an executor."""

    status: Literal["executed", "dry_run", "not_executable", "skipped", "failed"]
    executed: bool = False
    backend: str = "none"
    output_dir: str | None = None
    command: list[str] = Field(default_factory=list)
    details: str = ""


class WindowMetrics(BaseModel):
    """Metrics measured from packets.csv over one time window."""

    start: float
    end: float
    packets_sent: int
    packets_delivered: int
    pdr: float | None = None
    throughput_kbps: float | None = None
    mean_delay_ms: float | None = None


class VerificationResult(BaseModel):
    """Before/after comparison produced by the verification agent."""

    status: Literal[
        "success",
        "no_improvement",
        "degraded",
        "not_executed",
        "not_comparable",
        "insufficient_data",
    ]
    mode: Literal["counterfactual_rerun", "observation_only", "none"] = "none"
    before: WindowMetrics | None = None
    after: WindowMetrics | None = None
    change_pct: dict[str, float | None] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
