"""
baseline.py — Rule-based baseline to compare the agentic decisions against.

A conventional threshold system: the first matching rule fires, using only the
anomaly list and the current topology.  Compared with the agentic pipeline it
has **no** diagnosis, RAG, severity gating, route-safety checks or
least-disruption reasoning, and it reroutes over the plain shortest-hop path.

Rules (first match wins):

1. forwarding_drops                          -> isolate_node
2. flow_outage, endpoints connected          -> reroute_flow (shortest hop path)
3. flow_outage, endpoints disconnected       -> increase_tx_range
4. network_partition or isolated_node        -> increase_tx_range
5. low_pdr / high_delay / low_throughput     -> switch_protocol (AODV -> OLSR)
6. otherwise                                 -> no_action
"""

from __future__ import annotations

import networkx as nx

from agents.orchestrator_agent import PROTOCOL_ALTERNATIVE
from agents.routing_agent import graph_from_topology
from config import Settings
from schemas import RecoveryAction


def baseline_decide(state: dict, cfg: Settings) -> RecoveryAction:
    anomalies = state.get("anomalies") or []
    types = {a["type"] for a in anomalies}
    protocol = state.get("protocol") or "AODV"
    comm_range = float(state.get("comm_range_m") or 100.0)
    new_range = round(min(comm_range * cfg.TX_RANGE_STEP, cfg.MAX_TX_RANGE_M), 1)

    def act(t: str, **kw) -> RecoveryAction:
        return RecoveryAction(action_type=t, decided_by="baseline", **kw)

    drops = [a for a in anomalies if a["type"] == "forwarding_drops"]
    if drops:
        nodes = sorted({n for a in drops for n in a["affected_nodes"]})
        return act("isolate_node", target_nodes=nodes, parameters={"nodes": nodes},
                   rationale="rule 1: forwarding drops")

    outages = [a for a in anomalies if a["type"] == "flow_outage"]
    if outages:
        g = graph_from_topology(state.get("topology") or {})
        for a in outages:
            src, dst = a["affected_nodes"][:2]
            if src in g and dst in g and nx.has_path(g, src, dst):
                path = nx.shortest_path(g, src, dst)
                return act("reroute_flow", flow_id=a["flow_id"], target_nodes=path,
                           parameters={"path": path}, rationale="rule 2: outage, shortest-hop reroute")
        return act("increase_tx_range", parameters={"range_m": new_range, "from_m": comm_range},
                   rationale="rule 3: outage with disconnected endpoints")

    if types & {"network_partition", "isolated_node"}:
        return act("increase_tx_range", parameters={"range_m": new_range, "from_m": comm_range},
                   rationale="rule 4: partition / isolation")

    if types & {"low_pdr", "high_delay", "low_throughput"}:
        return act("switch_protocol",
                   parameters={"protocol": PROTOCOL_ALTERNATIVE.get(protocol, "OLSR"), "from": protocol},
                   rationale="rule 5: degraded performance")

    return act("no_action", rationale="rule 6: nothing matched")
