"""
routing_agent.py — Deterministic alternative-route computation (NetworkX).

For each affected flow (windowed PDR below threshold or a flow outage) it:

1. removes suspicious / low-energy relays (from the Security report),
2. enumerates up to ``ROUTE_K_PATHS`` loop-free paths (shortest first),
3. scores each path from measured or derived quantities only,
4. rejects unsafe paths via :func:`security_agent.assess_route`,
5. picks the best remaining path.

Score components (each in 0..1, higher is better; a component whose data is
unavailable is dropped and the weights are renormalised — never filled in):

==========  ==========================================================
hops        1 / hop_count  (also the delay proxy: no per-hop delay data)
lifetime    min predicted link lifetime / LINK_LIFETIME_CAP_S
margin      min (1 - distance / range) over the path's links
trust       min relay trust            (needs node_forwarding.csv)
energy      min relay energy / max node energy  (needs node_energy.csv)
==========  ==========================================================

No LLM is involved here; the LLM's opinion is kept separate in the diagnosis.
"""

from __future__ import annotations

import itertools
import logging

import networkx as nx

from agents.security_agent import assess_route
from config import Settings, get_settings
from schemas import FlowRouting, RouteCandidate
from state import AuraState

logger = logging.getLogger(__name__)


def graph_from_topology(topo: dict) -> nx.Graph:
    g = nx.Graph()
    g.add_nodes_from(n["id"] for n in topo.get("nodes", []))
    for link in topo.get("links", []):
        g.add_edge(
            link["a"],
            link["b"],
            distance=link["distance_m"],
            margin=link["margin"],
            lifetime=link["lifetime_s"],
        )
    return g


def affected_flows(state: AuraState, cfg: Settings) -> list[dict]:
    """Flows that need attention, worst first."""
    outage_ids = {
        a["flow_id"] for a in state.get("anomalies", []) if a.get("type") == "flow_outage"
    }
    flows = []
    for f in state.get("flow_stats") or []:
        bad_pdr = f["sent"] > 0 and f["pdr"] is not None and f["pdr"] < cfg.PDR_THRESHOLD
        if f["flow_id"] in outage_ids or bad_pdr:
            flows.append(f)
    flows.sort(key=lambda f: (f["pdr"] if f["pdr"] is not None else 1.0, f["flow_id"]))
    return flows[: cfg.ROUTE_MAX_FLOWS]


def score_path(
    g: nx.Graph,
    path: list[int],
    nodes_info: dict[int, dict],
    max_energy: float | None,
    cfg: Settings,
) -> tuple[float, dict[str, float], dict]:
    edges = list(zip(path, path[1:]))
    margins = [g.edges[e]["margin"] for e in edges]
    lifetimes = [g.edges[e]["lifetime"] for e in edges]
    relays = path[1:-1]

    components: dict[str, float] = {"hops": 1.0 / len(edges)}
    weights = {"hops": cfg.ROUTE_WEIGHT_HOPS}
    components["margin"] = min(margins)
    weights["margin"] = cfg.ROUTE_WEIGHT_MARGIN

    known_life = [lt for lt in lifetimes if lt is not None]
    min_life = min(known_life) if len(known_life) == len(lifetimes) else None
    if min_life is not None:
        components["lifetime"] = min(min_life / cfg.LINK_LIFETIME_CAP_S, 1.0)
        weights["lifetime"] = cfg.ROUTE_WEIGHT_LIFETIME

    trusts = [nodes_info.get(n, {}).get("trust") for n in relays]
    min_trust = None
    if relays and all(t is not None for t in trusts):
        min_trust = min(trusts)
        components["trust"] = min_trust
        weights["trust"] = cfg.ROUTE_WEIGHT_TRUST

    energies = [nodes_info.get(n, {}).get("energy_j") for n in relays]
    min_energy = None
    if relays and max_energy and all(e is not None for e in energies):
        min_energy = min(energies)
        components["energy"] = max(0.0, min(min_energy / max_energy, 1.0))
        weights["energy"] = cfg.ROUTE_WEIGHT_ENERGY

    total_w = sum(weights.values())
    score = sum(components[k] * weights[k] for k in components) / total_w
    extra = {
        "lifetimes": lifetimes,
        "min_life": min_life,
        "min_trust": min_trust,
        "min_energy": min_energy,
        "distance": sum(g.edges[e]["distance"] for e in edges),
        "min_margin": min(margins),
    }
    return round(score, 4), {k: round(v, 4) for k, v in components.items()}, extra


def _candidate(g, path, flow_id, security, nodes_info, max_energy, cfg, origin="computed", reason=""):
    score, comps, extra = score_path(g, path, nodes_info, max_energy, cfg)
    reasons = assess_route(path, security, extra["lifetimes"], cfg)
    return RouteCandidate(
        flow_id=flow_id,
        source=path[0],
        destination=path[-1],
        path=list(path),
        hop_count=len(path) - 1,
        total_distance_m=round(extra["distance"], 2),
        min_link_margin=round(extra["min_margin"], 4),
        min_link_lifetime_s=None if extra["min_life"] is None else round(extra["min_life"], 3),
        min_trust=extra["min_trust"],
        min_energy_j=extra["min_energy"],
        score=score,
        score_components=comps,
        rejected=bool(reasons),
        rejection_reasons=reasons,
        origin=origin,
        reason=reason,
    )


def check_llm_path(g: nx.Graph, path: list[int], src: int, dst: int) -> list[str]:
    """Structural errors in an LLM-chosen path (empty list = structurally valid)."""
    errors: list[str] = []
    if len(path) < 2:
        return ["path has fewer than two nodes"]
    if path[0] != src or path[-1] != dst:
        errors.append(f"path must go from {src} to {dst}, got {path[0]} to {path[-1]}")
    unknown = [n for n in path if n not in g]
    if unknown:
        errors.append(f"unknown nodes {unknown}")
    if len(set(path)) != len(path):
        errors.append("path visits a node twice")
    missing = [(a, b) for a, b in zip(path, path[1:]) if a in g and b in g and not g.has_edge(a, b)]
    if missing:
        errors.append(f"uses links that do not exist: {missing}")
    return errors


def route_flow(
    g: nx.Graph, flow: dict, security: dict, cfg: Settings, llm_choice: dict | None = None
) -> FlowRouting:
    src, dst = int(flow["source"]), int(flow["destination"])
    result = FlowRouting(
        flow_id=int(flow["flow_id"]),
        source=src,
        destination=dst,
        connected=src in g and dst in g and nx.has_path(g, src, dst),
        window_pdr=flow.get("pdr"),
    )
    if not result.connected:
        result.notes.append("No path exists in the current topology (partition).")
        if llm_choice:
            result.llm_route_errors.append("LLM chose a path, but this flow has no path at all (partition).")
        return result

    excluded = (set(security.get("suspicious_nodes", [])) | set(security.get("low_energy_nodes", []))) - {src, dst}
    usable = g.subgraph(n for n in g.nodes if n not in excluded)
    if excluded:
        result.notes.append(f"Excluded relays: {sorted(excluded)}")
    if not nx.has_path(usable, src, dst):
        result.notes.append("Every path uses an excluded relay; no safe route exists.")
        return result

    nodes_info = {n["node_id"]: n for n in security.get("nodes", [])}
    energies = [n["energy_j"] for n in nodes_info.values() if n.get("energy_j") is not None]
    max_energy = max(energies) if energies else None

    # LLM-chosen path: checked against the real topology, then scored like the others
    if llm_choice:
        path = [int(n) for n in llm_choice.get("path", [])]
        errors = check_llm_path(g, path, src, dst)
        if errors:
            result.llm_route_errors = errors
        else:
            result.llm_route = _candidate(g, path, result.flow_id, security, nodes_info, max_energy,
                                          cfg, origin="llm", reason=llm_choice.get("reason", ""))

    paths = itertools.islice(nx.shortest_simple_paths(usable, src, dst), cfg.ROUTE_K_PATHS)
    for path in paths:
        result.candidates.append(_candidate(usable, path, result.flow_id, security, nodes_info, max_energy, cfg))
    result.candidates.sort(key=lambda c: (c.rejected, -c.score, c.hop_count))
    accepted = [c for c in result.candidates if not c.rejected]
    result.best = accepted[0] if accepted else None
    if not accepted:
        result.notes.append("All candidate paths were rejected as unsafe.")
    return result


def routing_agent(state: AuraState) -> dict:
    """LangGraph node."""
    if state.get("error"):
        return {}
    cfg = get_settings()
    g = graph_from_topology(state.get("topology") or {})
    security = state.get("security_report") or {}
    choices = {int(c["flow_id"]): c for c in (state.get("diagnosis") or {}).get("route_choices", [])}
    routes = [route_flow(g, f, security, cfg, choices.get(int(f["flow_id"]))) for f in affected_flows(state, cfg)]
    with_route = sum(1 for r in routes if r.best is not None)
    summary = f"{len(routes)} affected flows, {with_route} with a safe alternative route"
    if choices:
        ok = sum(1 for r in routes if r.llm_route is not None and not r.llm_route.rejected)
        summary += f"; LLM chose {len(choices)} route(s), {ok} valid and safe"
    return {
        "candidate_routes": [r.model_dump() for r in routes],
        "trace": [{"agent": "routing", "summary": summary}],
    }
