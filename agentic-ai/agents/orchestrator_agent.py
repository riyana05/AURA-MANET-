"""
orchestrator_agent.py — Combine all findings and choose one recovery action.

The decision is a deterministic, explainable policy.  The LLM's
recommendation (from the diagnosis) is an *input*: it is adopted when it is
less disruptive than the policy's choice, applicable, and confident;
otherwise the disagreement is recorded in the rationale.

Policy (first match wins; least disruptive action that addresses the cause):

1. Highest anomaly severity below ``ACTION_MIN_SEVERITY``  -> no_action
2. Suspicious relays with forwarding evidence               -> isolate_node
3. Affected flow is connected and has a safe route          -> reroute_flow
4. Diagnosis asks for switch_protocol on route instability  -> switch_protocol
5. Most affected flows are partitioned                      -> increase_tx_range
6. Some flows partitioned / node isolated / new partition   -> wait_for_reconnection
7. Otherwise                                                -> no_action
"""

from __future__ import annotations

import logging

from config import Settings, get_settings
from schemas import ACTION_DISRUPTION, SEVERITY_RANK, RecoveryAction
from state import AuraState

logger = logging.getLogger(__name__)

PROTOCOL_ALTERNATIVE = {"AODV": "OLSR", "DSR": "OLSR", "DSDV": "OLSR", "OLSR": "AODV"}


def _max_severity(anomalies: list[dict]) -> int:
    return max((SEVERITY_RANK[a["severity"]] for a in anomalies), default=0)


def decide(state: AuraState, cfg: Settings) -> RecoveryAction:
    anomalies = state.get("anomalies") or []
    diagnosis = state.get("diagnosis") or {}
    security = state.get("security_report") or {}
    routes = state.get("candidate_routes") or []
    protocol = state.get("protocol") or "AODV"
    comm_range = float(state.get("comm_range_m") or 100.0)
    availability = state.get("data_availability") or {}

    if not anomalies:
        return RecoveryAction(action_type="no_action", rationale="No anomalies detected.")
    if _max_severity(anomalies) < SEVERITY_RANK[cfg.ACTION_MIN_SEVERITY]:
        return RecoveryAction(
            action_type="no_action",
            rationale=(f"Highest anomaly severity is below the action threshold "
                       f"'{cfg.ACTION_MIN_SEVERITY}'; avoiding an unnecessary network change."),
        )

    candidates: list[RecoveryAction] = []

    # 2. isolate misbehaving relays (only with forwarding evidence)
    suspicious = security.get("suspicious_nodes") or []
    if suspicious and availability.get("forwarding"):
        candidates.append(RecoveryAction(
            action_type="isolate_node",
            target_nodes=sorted(suspicious),
            parameters={"nodes": sorted(suspicious)},
            rationale=f"Nodes {sorted(suspicious)} drop forwarded packets (trust below threshold).",
        ))

    # 3. reroute the worst connected flow over its best safe path
    routable = [r for r in routes if r.get("connected") and r.get("best")]
    if routable:
        r = routable[0]  # routes are ordered worst PDR first
        best = r["best"]
        candidates.append(RecoveryAction(
            action_type="reroute_flow",
            flow_id=r["flow_id"],
            target_nodes=best["path"],
            parameters={"path": best["path"], "score": best["score"],
                        "min_link_lifetime_s": best["min_link_lifetime_s"]},
            rationale=(f"Flow {r['flow_id']} ({r['source']}->{r['destination']}) has window PDR "
                       f"{r['window_pdr']} but a path exists; best safe path {best['path']} "
                       f"(score {best['score']}, {best['hop_count']} hops)."),
        ))

    # 4. protocol switch requested by the diagnosis for route instability
    if diagnosis.get("recommended_action") == "switch_protocol":
        target = PROTOCOL_ALTERNATIVE.get(protocol, "OLSR")
        if target in cfg.ALLOWED_PROTOCOLS:
            candidates.append(RecoveryAction(
                action_type="switch_protocol",
                parameters={"protocol": target, "from": protocol},
                rationale=f"Diagnosis attributes the fault to {protocol} route maintenance; try {target}.",
            ))

    # 5./6. partitions
    disconnected = [r for r in routes if not r.get("connected")]
    total_flows = len(state.get("flow_stats") or []) or 1
    partition_signals = {"network_partition", "isolated_node"} & {a["type"] for a in anomalies}
    if disconnected and (len(disconnected) > total_flows / 2
                         or diagnosis.get("recommended_action") == "increase_tx_range"):
        new_range = round(min(comm_range * cfg.TX_RANGE_STEP, cfg.MAX_TX_RANGE_M), 1)
        if new_range > comm_range:
            candidates.append(RecoveryAction(
                action_type="increase_tx_range",
                parameters={"range_m": new_range, "from_m": comm_range},
                rationale=(f"{len(disconnected)} of {total_flows} flows have no path at all; "
                           f"raising range {comm_range:g} m -> {new_range:g} m to heal partitions."),
            ))
    if disconnected or partition_signals:
        candidates.append(RecoveryAction(
            action_type="wait_for_reconnection",
            target_nodes=sorted({r["source"] for r in disconnected} | {r["destination"] for r in disconnected}),
            rationale=("Affected endpoints are in different components; no route exists, so "
                       "routing changes cannot help. Waiting for mobility to reconnect."),
        ))

    if not candidates:
        return RecoveryAction(
            action_type="no_action",
            rationale="Anomalies present but no applicable, validated action improves on doing nothing.",
        )

    chosen = candidates[0]
    others = [c.action_type for c in candidates[1:]]

    # Consider the diagnosis' recommendation
    rec = diagnosis.get("recommended_action")
    note = ""
    if rec and rec != chosen.action_type:
        rec_candidate = next((c for c in candidates if c.action_type == rec), None)
        if (diagnosis.get("method") == "llm_rag"
                and rec_candidate is not None
                and ACTION_DISRUPTION[rec] < ACTION_DISRUPTION[chosen.action_type]
                and float(diagnosis.get("confidence", 0)) >= 0.7):
            others = [c.action_type for c in candidates if c is not rec_candidate]
            chosen = rec_candidate
            note = f" Adopted the diagnosis recommendation '{rec}' (less disruptive, confident)."
        else:
            note = f" Diagnosis recommended '{rec}'; policy kept '{chosen.action_type}'."
    chosen.rationale += note
    chosen.alternatives = others
    return chosen


def orchestrator_agent(state: AuraState) -> dict:
    """LangGraph node."""
    if state.get("error"):
        return {}
    action = decide(state, get_settings())
    return {
        "recovery_action": action.model_dump(),
        "trace": [{"agent": "orchestrator", "summary": f"{action.action_type}: {action.rationale}"}],
    }
