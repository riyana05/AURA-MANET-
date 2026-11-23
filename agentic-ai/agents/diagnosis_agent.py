"""
diagnosis_agent.py — Fault diagnosis with GenAI + RAG (rule-based fallback).

Flow
----
1. Build a retrieval query from the anomaly types and retrieve knowledge
   passages (MANET / AODV reference text, fault playbook, verified past cases).
2. If an LLM is configured, send it **only measured facts** (anomalies,
   windowed metrics, topology summary, data availability) plus the passages,
   and ask for a :class:`schemas.LLMDiagnosisOutput`.
3. Validate the answer: unknown node IDs are removed, citations must refer to
   retrieved chunks, the action must be one of the allowed types, and claims
   needing data that does not exist (forwarding / energy) are flagged.
4. Without an LLM (or if the call fails) a transparent rule-based diagnosis
   is produced instead.  ``Diagnosis.method`` records which path was used.

The LLM never computes routes or metrics; it only explains and recommends.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from config import Settings, get_settings
from llm import StructuredLLM
from schemas import (
    SEVERITY_RANK,
    Diagnosis,
    DiagnosisFinding,
    LLMDiagnosisOutput,
    RetrievedChunk,
)
from state import AuraState

logger = logging.getLogger(__name__)

ACTION_HELP = {
    "no_action": "anomalies are minor or transient; changing the network is not justified",
    "wait_for_reconnection": "the fault is a partition/isolation that routing cannot fix; wait for mobility to reconnect",
    "reroute_flow": "a path exists; steer an affected flow onto a more stable path",
    "increase_tx_range": "raise transmit power/range so partitions heal and links last longer",
    "switch_protocol": "change the routing protocol network-wide (disruptive; persistent problems only)",
    "isolate_node": "exclude a node from routing; ONLY with forwarding-drop evidence",
}

SYSTEM_PROMPT = """You are the Diagnosis Agent of AURA-MANET, a self-healing mobile ad hoc network.
You receive anomalies measured from an ns-3 simulation and reference passages.

Rules:
- Use ONLY the numbers given in the input. Never invent or estimate metrics.
- Base every finding on listed observations; put them in `evidence`.
- If a fault would need data marked unavailable (e.g. forwarding counters for
  black-hole attacks, energy data for energy depletion), do not assert it;
  mention it only as unverifiable with low confidence.
- `affected_nodes` must be node IDs from the input.
- `cited_chunk_ids` must be chunk ids from the reference passages you used.
- Fill `claimed_disconnected_flows` and `claimed_isolated_nodes` exactly from
  `flow_connectivity` and `topology.isolated_nodes`; these claims are checked.
- Pick `recommended_action` from the allowed actions, preferring the least
  disruptive action that addresses the root cause.
- ROUTES: for every flow in `flows_needing_routes`, choose ONE path in
  `route_choices` (flow_id, path from source to destination, short reason).
  Use ONLY links listed in `links` (each is [node_a, node_b, predicted
  lifetime in s, range margin 0..1]); every consecutive pair in the path must
  be a listed link. Prefer relays whose links have long predicted lifetime and
  high margin, with few hops. Your paths are checked by code; invalid paths are
  rejected.
Return the structured result only."""


# --------------------------------------------------------------------------- #
# Retrieval                                                                    #
# --------------------------------------------------------------------------- #


def build_query(anomalies: list[dict]) -> str:
    parts = []
    for a in sorted(anomalies, key=lambda a: -SEVERITY_RANK[a["severity"]])[:6]:
        extra = ""
        if a["type"] == "flow_outage":
            connected = a.get("evidence", {}).get("endpoints_connected")
            extra = " endpoints connected" if connected else " endpoints not connected partition"
        parts.append(a["type"].replace("_", " ") + extra)
    return "MANET fault diagnosis: " + "; ".join(parts) + ". probable cause and recovery"


# --------------------------------------------------------------------------- #
# Facts sent to the LLM                                                        #
# --------------------------------------------------------------------------- #


def summarise_facts(state: AuraState) -> dict:
    topo = state.get("topology") or {}
    metrics = state.get("raw_metrics") or {}
    anomalies = state.get("anomalies") or []
    outage_flows = {a.get("flow_id") for a in anomalies if a.get("type") == "flow_outage"}
    return {
        "time_s": state.get("current_time"),
        "routing_protocol": state.get("protocol"),
        "radio_range_m": state.get("comm_range_m"),
        "node_ids": [n["id"] for n in topo.get("nodes", [])],
        "data_available": state.get("data_availability"),
        "window_metrics": state.get("window_metrics"),
        "cumulative": {k: metrics.get(k) for k in ("PDR", "packets_sent", "packets_received", "average_delay_ms")},
        "topology": {
            "links": len(topo.get("links", [])),
            "component_sizes": [len(c) for c in topo.get("components", [])],
            "isolated_nodes": topo.get("isolated_nodes", []),
            "links_broken_last_second": topo.get("broken_links", []),
        },
        "anomalies": [
            {k: a.get(k) for k in ("type", "severity", "value", "threshold", "description", "affected_nodes", "flow_id")}
            for a in anomalies
        ],
        "flows_in_outage": sorted(f for f in outage_flows if f is not None),
        "flow_connectivity": measured_flow_connectivity(state),
        **route_facts(state),
    }


def route_facts(state: AuraState) -> dict:
    """Flows the LLM should route, plus the links it may use (with lifetimes)."""
    from agents.routing_agent import affected_flows

    conn = measured_flow_connectivity(state)
    flows = [
        {"flow_id": f["flow_id"], "source": f["source"], "destination": f["destination"]}
        for f in affected_flows(state, get_settings())
        if conn.get(int(f["flow_id"]))
    ]
    if not flows:
        return {"flows_needing_routes": []}
    links = [
        [link["a"], link["b"], link.get("lifetime_s"), link.get("margin")]
        for link in (state.get("topology") or {}).get("links", [])
    ]
    return {"flows_needing_routes": flows, "links": links}


def measured_flow_connectivity(state: AuraState) -> dict[int, bool]:
    """flow_id -> whether source and destination are connected right now (measured)."""
    import networkx as nx

    topo = state.get("topology") or {}
    g = nx.Graph()
    g.add_nodes_from(n["id"] for n in topo.get("nodes", []))
    g.add_edges_from((link["a"], link["b"]) for link in topo.get("links", []))
    out: dict[int, bool] = {}
    for f in state.get("flow_stats") or []:
        s, d = int(f["source"]), int(f["destination"])
        out[int(f["flow_id"])] = s in g and d in g and nx.has_path(g, s, d)
    return out


def fact_check(out: LLMDiagnosisOutput, state: AuraState) -> dict:
    """Compare the LLM's connectivity claims with the measured topology."""
    conn = measured_flow_connectivity(state)
    measured_disc = sorted(f for f, ok in conn.items() if not ok)
    measured_iso = sorted((state.get("topology") or {}).get("isolated_nodes", []))
    claimed_disc = sorted(set(out.claimed_disconnected_flows))
    claimed_iso = sorted(set(out.claimed_isolated_nodes))
    wrong_disc = sorted(f for f in claimed_disc if conn.get(f, True))
    wrong_iso = sorted(set(claimed_iso) - set(measured_iso))
    errors = len(wrong_disc) + len(wrong_iso)
    return {
        "claimed_disconnected_flows": claimed_disc,
        "measured_disconnected_flows": measured_disc,
        "wrongly_claimed_disconnected": wrong_disc,
        "claimed_isolated_nodes": claimed_iso,
        "measured_isolated_nodes": measured_iso,
        "wrongly_claimed_isolated": wrong_iso,
        "passed": errors == 0,
    }


def build_user_prompt(state: AuraState, chunks: list[RetrievedChunk]) -> str:
    refs = "\n\n".join(f"[{c.chunk_id}] {c.text}" for c in chunks) or "(none)"
    actions = "\n".join(f"- {k}: {v}" for k, v in ACTION_HELP.items())
    return (
        "MEASURED FACTS (JSON):\n"
        + json.dumps(summarise_facts(state), indent=1, default=str)
        + "\n\nALLOWED ACTIONS:\n"
        + actions
        + "\n\nREFERENCE PASSAGES:\n"
        + refs
    )


# --------------------------------------------------------------------------- #
# Validation of LLM output                                                     #
# --------------------------------------------------------------------------- #


def validate_llm_output(
    out: LLMDiagnosisOutput, state: AuraState, chunks: list[RetrievedChunk]
) -> Diagnosis:
    notes: list[str] = []
    valid_nodes = {n["id"] for n in (state.get("topology") or {}).get("nodes", [])}
    availability = state.get("data_availability") or {}
    findings: list[DiagnosisFinding] = []
    for f in out.findings:
        bad = [n for n in f.affected_nodes if n not in valid_nodes]
        if bad:
            notes.append(f"Removed unknown node ids {bad} from finding '{f.fault}'.")
            f.affected_nodes = [n for n in f.affected_nodes if n in valid_nodes]
        label = f.fault.lower()
        needs_fwd = any(k in label for k in ("black", "grey", "gray", "drop", "malicious", "attack"))
        if needs_fwd and not availability.get("forwarding") and f.confidence > 0.3:
            notes.append(
                f"Finding '{f.fault}' needs forwarding data that is not available; "
                "confidence capped at 0.3."
            )
            f.confidence = 0.3
        if "energy" in label and not availability.get("energy") and f.confidence > 0.3:
            notes.append(f"Finding '{f.fault}' needs energy data that is not available; confidence capped at 0.3.")
            f.confidence = 0.3
        findings.append(f)

    by_id = {c.chunk_id: c for c in chunks}
    unknown_cites = [c for c in out.cited_chunk_ids if c not in by_id]
    if unknown_cites:
        notes.append(f"Ignored citations not in the retrieved context: {unknown_cites}")
    cited = [by_id[c] for c in out.cited_chunk_ids if c in by_id] or chunks

    action = out.recommended_action
    if action == "isolate_node" and not availability.get("forwarding"):
        notes.append("LLM recommended isolate_node without forwarding evidence; downgraded to no_action.")
        action = "no_action"

    check = fact_check(out, state)
    if not check["passed"]:
        penalty = 0.15 * (bool(check["wrongly_claimed_disconnected"]) + bool(check["wrongly_claimed_isolated"]))
        for f in findings:
            f.confidence = round(max(0.1, f.confidence - penalty), 2)
        if check["wrongly_claimed_disconnected"]:
            notes.append(
                f"Fact-check: LLM claimed flows {check['wrongly_claimed_disconnected']} are disconnected, "
                f"but measured topology shows they have a path (disconnected: "
                f"{check['measured_disconnected_flows']}). Confidence lowered."
            )
        if check["wrongly_claimed_isolated"]:
            notes.append(
                f"Fact-check: LLM claimed nodes {check['wrongly_claimed_isolated']} are isolated; "
                f"measured isolated nodes: {check['measured_isolated_nodes']}. Confidence lowered."
            )

    findings.sort(key=lambda f: -f.confidence)
    return Diagnosis(
        root_cause=out.root_cause,
        findings=findings,
        recommended_action=action,
        reasoning=out.reasoning,
        confidence=findings[0].confidence if findings else 0.0,
        method="llm_rag",
        sources=cited,
        notes=notes,
        fact_check=check,
        route_choices=out.route_choices,
    )


# --------------------------------------------------------------------------- #
# Rule-based fallback (also documents the reasoning the LLM should match)      #
# --------------------------------------------------------------------------- #


def rule_based_diagnosis(state: AuraState, chunks: list[RetrievedChunk]) -> Diagnosis:
    anomalies = state.get("anomalies") or []
    by_type: dict[str, list[dict]] = {}
    for a in anomalies:
        by_type.setdefault(a["type"], []).append(a)
    flows_total = len(state.get("flow_stats") or []) or 1
    findings: list[DiagnosisFinding] = []

    outages = by_type.get("flow_outage", [])
    disconnected = [a for a in outages if not a.get("evidence", {}).get("endpoints_connected")]
    connected = [a for a in outages if a.get("evidence", {}).get("endpoints_connected")]

    if disconnected or "network_partition" in by_type:
        nodes = sorted({n for a in disconnected for n in a.get("affected_nodes", [])})
        ev = [a["description"] for a in disconnected[:4]] + [
            a["description"] for a in by_type.get("network_partition", [])
        ]
        findings.append(DiagnosisFinding(
            fault="topology_partition",
            confidence=0.85 if disconnected else 0.6,
            affected_nodes=nodes,
            anomaly_types=sorted({"flow_outage"} if disconnected else set()) +
            (["network_partition"] if "network_partition" in by_type else []),
            evidence=ev,
        ))
    if by_type.get("forwarding_drops"):
        fd = by_type["forwarding_drops"]
        findings.append(DiagnosisFinding(
            fault="packet_dropping_node",
            confidence=0.8,
            affected_nodes=sorted({n for a in fd for n in a["affected_nodes"]}),
            anomaly_types=["forwarding_drops"],
            evidence=[a["description"] for a in fd],
        ))
    if connected:
        findings.append(DiagnosisFinding(
            fault="route_instability",
            confidence=0.65 if "link_breaks" in by_type else 0.5,
            affected_nodes=sorted({n for a in connected for n in a["affected_nodes"]}),
            anomaly_types=["flow_outage"] + (["link_breaks"] if "link_breaks" in by_type else []),
            evidence=[a["description"] for a in connected[:4]]
            + [a["description"] for a in by_type.get("link_breaks", [])],
        ))
    if by_type.get("isolated_node"):
        iso = by_type["isolated_node"]
        findings.append(DiagnosisFinding(
            fault="node_out_of_range",
            confidence=0.9,
            affected_nodes=sorted({n for a in iso for n in a["affected_nodes"]}),
            anomaly_types=["isolated_node"],
            evidence=[a["description"] for a in iso],
        ))
    if by_type.get("low_energy"):
        le = by_type["low_energy"]
        findings.append(DiagnosisFinding(
            fault="energy_depletion",
            confidence=0.9,
            affected_nodes=sorted({n for a in le for n in a["affected_nodes"]}),
            anomaly_types=["low_energy"],
            evidence=[a["description"] for a in le],
        ))
    if by_type.get("high_delay"):
        cause = "route_discovery_buffering" if (outages or "link_breaks" in by_type) else "congestion"
        findings.append(DiagnosisFinding(
            fault=cause,
            confidence=0.5 if cause == "route_discovery_buffering" else 0.4,
            anomaly_types=["high_delay"],
            evidence=[a["description"] for a in by_type["high_delay"]],
        ))
    explained = {t for f in findings for t in f.anomaly_types}
    leftovers = [t for t in ("low_pdr", "low_throughput") if t in by_type]
    if leftovers:
        if findings:
            findings[0].anomaly_types = sorted(set(findings[0].anomaly_types) | set(leftovers))
            findings[0].evidence += [by_type[t][0]["description"] for t in leftovers]
        else:
            findings.append(DiagnosisFinding(
                fault="congestion_or_interference",
                confidence=0.4,
                anomaly_types=leftovers,
                evidence=[by_type[t][0]["description"] for t in leftovers],
            ))
    if "link_breaks" in by_type and "link_breaks" not in explained and not connected:
        findings.append(DiagnosisFinding(
            fault="high_mobility",
            confidence=0.5,
            affected_nodes=by_type["link_breaks"][0].get("affected_nodes", []),
            anomaly_types=["link_breaks"],
            evidence=[by_type["link_breaks"][0]["description"]],
        ))

    findings.sort(key=lambda f: -f.confidence)

    if by_type.get("forwarding_drops"):
        action = "isolate_node"
    elif connected:
        action = "reroute_flow"
    elif disconnected and len({a.get("flow_id") for a in disconnected}) > flows_total / 2:
        action = "increase_tx_range"
    elif disconnected or by_type.get("isolated_node") or by_type.get("network_partition"):
        action = "wait_for_reconnection"
    else:
        action = "no_action"

    root = findings[0].fault.replace("_", " ") if findings else "no fault identified"
    return Diagnosis(
        root_cause=root,
        findings=findings,
        recommended_action=action,
        reasoning="Rule-based mapping from anomaly signatures (see rag/knowledge_base/fault_patterns.md).",
        confidence=findings[0].confidence if findings else 0.0,
        method="rule_based",
        sources=chunks,
    )


# --------------------------------------------------------------------------- #
# Node factory                                                                 #
# --------------------------------------------------------------------------- #


def make_diagnosis_agent(
    llm: StructuredLLM | None,
    retrieve: Callable[[str, int], list[RetrievedChunk]] | None,
    cfg: Settings | None = None,
    llm_off_reason: str | None = None,
):
    """Create the LangGraph node with its dependencies injected.

    ``llm_off_reason`` explains why ``llm`` is None (e.g. switched off on
    purpose); when omitted, a missing/unusable API key is assumed.
    """

    def diagnosis_agent(state: AuraState) -> dict:
        if state.get("error"):
            return {}
        settings = cfg or get_settings()
        anomalies = state.get("anomalies") or []
        if not anomalies:
            d = Diagnosis(root_cause="no anomalies", method="none")
            return {"diagnosis": d.model_dump(), "rag_context": [],
                    "trace": [{"agent": "diagnosis", "summary": "skipped (no anomalies)"}]}

        notes: list[str] = []
        chunks: list[RetrievedChunk] = []
        if retrieve is not None:
            try:
                chunks = retrieve(build_query(anomalies), settings.RAG_TOP_K)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"RAG retrieval failed: {exc}")
                logger.warning("RAG retrieval failed: %s", exc)

        diagnosis: Diagnosis | None = None
        if llm is not None:
            try:
                out = llm.generate(SYSTEM_PROMPT, build_user_prompt(state, chunks), LLMDiagnosisOutput)
                diagnosis = validate_llm_output(out, state, chunks)
                diagnosis.llm_call = dict(getattr(llm, "last_call", {}) or {"model": getattr(llm, "name", "?")})
            except Exception as exc:  # noqa: BLE001 - network / validation errors
                notes.append(f"LLM diagnosis failed ({type(exc).__name__}: {exc}); used rule-based fallback.")
                logger.warning("LLM diagnosis failed: %s", exc)
        if diagnosis is None:
            diagnosis = rule_based_diagnosis(state, chunks)
            if llm is None:
                notes.append(llm_off_reason or
                             "No LLM available (GROQ_API_KEY not set, or the Groq client could not "
                             "start): rule-based diagnosis.")
        diagnosis.notes = notes + diagnosis.notes

        summary = (f"[{diagnosis.method}] {diagnosis.root_cause} "
                   f"(conf {diagnosis.confidence:.2f}) -> {diagnosis.recommended_action}")
        return {
            "diagnosis": diagnosis.model_dump(),
            "rag_context": [c.model_dump() for c in chunks],
            "trace": [{"agent": "diagnosis", "summary": summary}],
        }

    return diagnosis_agent
