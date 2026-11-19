"""
security_agent.py — Security & Energy agent (deterministic).

Assesses every node's trust and remaining energy, flags suspicious and
low-energy nodes, and provides :func:`assess_route`, which the Routing agent
uses to reject unsafe routes.

Evidence rules (nothing is inferred without data):

* **Trust** = ``forwarded / (forwarded + dropped)`` from ``node_forwarding.csv``.
  Without that file trust is ``None`` ("unknown"), no node is called
  malicious, and the risk level is ``"unknown"``.
* **Energy** comes from ``node_energy.csv``; without it energy is ``None``.
* **Link stability** (always available): a route whose weakest link is
  predicted to break within ``MIN_LINK_LIFETIME_S`` is rejected as unsafe.
"""

from __future__ import annotations

import logging

from config import Settings, get_settings
from schemas import NodeAssessment, SecurityReport
from state import AuraState

logger = logging.getLogger(__name__)


def assess_nodes(state: AuraState, cfg: Settings) -> SecurityReport:
    availability = state.get("data_availability") or {}
    topo = state.get("topology") or {}
    degree = {n["id"]: n["degree"] for n in topo.get("nodes", [])}
    forwarding = {int(r["node_id"]): r for r in state.get("forwarding_snapshot") or []}
    energy = {int(r["node_id"]): r for r in state.get("energy_snapshot") or []}

    nodes: list[NodeAssessment] = []
    for node_id in sorted(degree):
        a = NodeAssessment(node_id=node_id, degree=degree[node_id])
        fwd_row = forwarding.get(node_id)
        if fwd_row is not None:
            fwd, drop = fwd_row.get("forwarded"), fwd_row.get("dropped")
            if fwd is not None and drop is not None and (fwd + drop) >= cfg.FLOW_OUTAGE_MIN_PACKETS:
                a.trust = round(float(fwd) / float(fwd + drop), 4)
                a.trust_evidence = f"forwarded {int(fwd)}, dropped {int(drop)}"
                if a.trust < cfg.TRUST_THRESHOLD:
                    a.suspicious = True
                    a.reasons.append(
                        f"forwarding ratio {a.trust:.2f} < trust threshold {cfg.TRUST_THRESHOLD:.2f}"
                    )
            else:
                a.trust_evidence = "too few forwarding events to judge"
        e_row = energy.get(node_id)
        if e_row is not None and e_row.get("remaining_energy_j") is not None:
            a.energy_j = float(e_row["remaining_energy_j"])
            if a.energy_j < cfg.ENERGY_THRESHOLD_J:
                a.low_energy = True
                a.reasons.append(f"remaining energy {a.energy_j:.2f} J < {cfg.ENERGY_THRESHOLD_J} J")
        nodes.append(a)

    suspicious = [n.node_id for n in nodes if n.suspicious]
    low_energy = [n.node_id for n in nodes if n.low_energy]
    notes: list[str] = []
    has_fwd = bool(availability.get("forwarding"))
    has_energy = bool(availability.get("energy"))
    if not has_fwd:
        notes.append(
            "No per-node forwarding data (node_forwarding.csv): trust cannot be "
            "assessed and packet-dropping attacks cannot be detected or ruled out."
        )
    if not has_energy:
        notes.append("No energy data (node_energy.csv): energy-aware checks skipped.")

    if suspicious:
        risk = "high"
    elif low_energy:
        risk = "medium"
    elif has_fwd:
        risk = "none" if has_energy else "low"
    else:
        risk = "unknown"

    return SecurityReport(
        nodes=nodes,
        suspicious_nodes=suspicious,
        low_energy_nodes=low_energy,
        risk_level=risk,
        data_available={"forwarding": has_fwd, "energy": has_energy},
        notes=notes,
    )


def assess_route(
    path: list[int],
    report: dict,
    link_lifetimes: list[float | None],
    cfg: Settings,
) -> list[str]:
    """Return the reasons a route is unsafe (empty list = acceptable).

    Endpoints are not judged (the flow's source/destination are fixed);
    only relays are.
    """
    reasons: list[str] = []
    relays = set(path[1:-1])
    for n in sorted(relays & set(report.get("suspicious_nodes", []))):
        reasons.append(f"relay {n} is suspicious (low forwarding trust)")
    for n in sorted(relays & set(report.get("low_energy_nodes", []))):
        reasons.append(f"relay {n} has low remaining energy")
    known = [lt for lt in link_lifetimes if lt is not None]
    if known and min(known) < cfg.MIN_LINK_LIFETIME_S:
        reasons.append(
            f"weakest link predicted to break in {min(known):.2f}s "
            f"(< {cfg.MIN_LINK_LIFETIME_S:g}s)"
        )
    return reasons


def security_agent(state: AuraState) -> dict:
    """LangGraph node."""
    if state.get("error"):
        return {}
    report = assess_nodes(state, get_settings())
    summary = (
        f"risk={report.risk_level}, suspicious={report.suspicious_nodes}, "
        f"low_energy={report.low_energy_nodes}"
    )
    return {
        "security_report": report.model_dump(),
        "trace": [{"agent": "security", "summary": summary}],
    }
