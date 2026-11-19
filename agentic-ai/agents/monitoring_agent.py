"""
monitoring_agent.py — Deterministic anomaly detection (no LLM).

Reads the ingestion fields of the state, builds the topology, and emits a list
of :class:`schemas.Anomaly`.  Every value in an anomaly comes straight from the
ns-3 output; a rule whose input data is missing is skipped, never guessed.

Rules
-----
=================  =========================================================
type               fires when
=================  =========================================================
low_pdr            windowed PDR (last FLOW_WINDOW_S) < PDR_THRESHOLD, once at
                   least MIN_PACKETS_FOR_RATIO packets were sent.  Packet loss
                   is reported inside this anomaly (it is 1 - PDR), not twice.
low_throughput     windowed throughput < THROUGHPUT_THRESHOLD_KBPS while
                   packets are being sent
high_delay         windowed mean delay > DELAY_THRESHOLD_MS
link_breaks        links lost since the previous second > LINK_BREAKS_THRESHOLD
isolated_node      a node lost its last neighbour since the previous second
network_partition  connected components increased since the previous second
flow_outage        a flow sent >= FLOW_OUTAGE_MIN_PACKETS in the window and
                   none were delivered
low_energy         remaining energy < ENERGY_THRESHOLD_J   (needs node_energy.csv)
forwarding_drops   drop ratio > FORWARD_DROP_THRESHOLD     (needs node_forwarding.csv)
=================  =========================================================

Usage as a LangGraph node::

    graph.add_node("monitoring", monitoring_agent)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_AGENTIC_AI_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTIC_AI_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENTIC_AI_DIR))

from config import Settings, get_settings  # noqa: E402
from schemas import Anomaly  # noqa: E402
from state import AuraState  # noqa: E402
from topology import Topology, topology_from_state  # noqa: E402

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Severity helpers                                                             #
# --------------------------------------------------------------------------- #


def _pdr_severity(pdr: float) -> str:
    if pdr < 0.3:
        return "critical"
    if pdr < 0.5:
        return "high"
    return "medium"


def _delay_severity(delay_ms: float, threshold: float) -> str:
    if delay_ms > 4 * threshold:
        return "critical"
    if delay_ms > 2 * threshold:
        return "high"
    return "medium"


def _count_severity(n: int, medium: int, high: int) -> str:
    if n >= high:
        return "high"
    if n >= medium:
        return "medium"
    return "low"


# --------------------------------------------------------------------------- #
# Individual rules                                                             #
# --------------------------------------------------------------------------- #


def check_pdr(window: dict, metrics: dict, cfg: Settings, ts: float) -> Anomaly | None:
    sent = int(window.get("packets_sent") or 0)
    pdr = window.get("pdr")
    if pdr is None or sent < cfg.MIN_PACKETS_FOR_RATIO or pdr >= cfg.PDR_THRESHOLD:
        return None
    return Anomaly(
        type="low_pdr",
        severity=_pdr_severity(pdr),
        value=round(pdr, 4),
        threshold=cfg.PDR_THRESHOLD,
        description=(
            f"PDR over the last {cfg.FLOW_WINDOW_S:g}s is {pdr:.3f} "
            f"({window['packets_delivered']}/{sent} packets delivered by t={ts:g}s), "
            f"below {cfg.PDR_THRESHOLD:.2f}; packet loss {1 - pdr:.3f}."
        ),
        timestamp=ts,
        evidence={
            "window_packets_sent": sent,
            "window_packets_delivered": window["packets_delivered"],
            "window_packet_loss": round(1 - pdr, 4),
            "cumulative_pdr": metrics.get("PDR"),
            "cumulative_packet_loss": metrics.get("packet_loss"),
        },
    )


def check_throughput(window: dict, cfg: Settings, ts: float) -> Anomaly | None:
    sent = int(window.get("packets_sent") or 0)
    thr = window.get("throughput_kbps")
    if sent == 0 or thr is None or thr >= cfg.THROUGHPUT_THRESHOLD_KBPS:
        return None
    return Anomaly(
        type="low_throughput",
        severity="medium",
        value=round(thr, 3),
        threshold=cfg.THROUGHPUT_THRESHOLD_KBPS,
        description=(
            f"Delivered throughput over the last {cfg.FLOW_WINDOW_S:g}s is {thr:.2f} kbps "
            f"while {sent} packets were sent."
        ),
        timestamp=ts,
        evidence={"window_packets_sent": sent},
    )


def check_delay(window: dict, cfg: Settings, ts: float) -> Anomaly | None:
    delay = window.get("mean_delay_ms")
    if delay is None or delay <= cfg.DELAY_THRESHOLD_MS:
        return None
    return Anomaly(
        type="high_delay",
        severity=_delay_severity(delay, cfg.DELAY_THRESHOLD_MS),
        value=round(delay, 3),
        threshold=cfg.DELAY_THRESHOLD_MS,
        description=(
            f"Mean end-to-end delay of packets delivered in the last {cfg.FLOW_WINDOW_S:g}s "
            f"is {delay:.1f} ms (threshold {cfg.DELAY_THRESHOLD_MS:.0f} ms)."
        ),
        timestamp=ts,
        evidence={"window_packets_delivered": window.get("packets_delivered")},
    )


def check_link_breaks(topo: Topology, cfg: Settings, ts: float) -> Anomaly | None:
    n = len(topo.broken_links)
    if n <= cfg.LINK_BREAKS_THRESHOLD:
        return None
    nodes = sorted({x for link in topo.broken_links for x in link})
    return Anomaly(
        type="link_breaks",
        severity=_count_severity(n, cfg.LINK_BREAKS_THRESHOLD + 1, 2 * cfg.LINK_BREAKS_THRESHOLD + 2),
        value=n,
        threshold=cfg.LINK_BREAKS_THRESHOLD,
        description=f"{n} links broke in the last second: {topo.broken_links}.",
        timestamp=ts,
        affected_nodes=nodes,
        evidence={"broken_links": [list(link) for link in topo.broken_links]},
    )


def check_isolation(topo: Topology, ts: float) -> list[Anomaly]:
    out: list[Anomaly] = []
    lost_from = {}
    for a, b in topo.broken_links:
        lost_from.setdefault(a, []).append(b)
        lost_from.setdefault(b, []).append(a)
    for node in topo.isolated():
        if node not in lost_from:
            continue  # was already isolated: not a new event
        out.append(
            Anomaly(
                type="isolated_node",
                severity="high",
                value=0,
                threshold=1,
                description=(
                    f"Node {node} lost its last neighbour(s) {sorted(lost_from[node])} "
                    "in the last second and is now isolated."
                ),
                timestamp=ts,
                affected_nodes=[node],
                evidence={"lost_neighbours": sorted(lost_from[node])},
            )
        )
    return out


def check_partition(metrics: dict, previous: dict, topo: Topology, ts: float) -> Anomaly | None:
    if not previous:
        return None
    try:
        now = int(metrics["connected_components"])
        before = int(previous["connected_components"])
    except (KeyError, TypeError, ValueError):
        return None
    increase = now - before
    if increase <= 0:
        return None
    severity = "critical" if increase >= 3 else "high" if increase == 2 else "medium"
    return Anomaly(
        type="network_partition",
        severity=severity,
        value=now,
        threshold=before,
        description=(
            f"Network split from {before} to {now} connected components (+{increase}) "
            "in the last second."
        ),
        timestamp=ts,
        evidence={"component_sizes": [len(c) for c in topo.components()]},
    )


def check_flow_outages(flows: list[dict], topo: Topology, cfg: Settings, ts: float) -> list[Anomaly]:
    out: list[Anomaly] = []
    for f in flows:
        if f["sent"] < cfg.FLOW_OUTAGE_MIN_PACKETS or f["delivered"] > 0:
            continue
        src, dst = int(f["source"]), int(f["destination"])
        connected = topo.connected(src, dst)
        out.append(
            Anomaly(
                type="flow_outage",
                severity="high",
                value=0.0,
                threshold=None,
                description=(
                    f"Flow {f['flow_id']} ({src}->{dst}) delivered 0 of {f['sent']} packets "
                    f"in the last {cfg.FLOW_WINDOW_S:g}s; source and destination are "
                    f"{'connected' if connected else 'NOT connected'} in the current topology."
                ),
                timestamp=ts,
                affected_nodes=[src, dst],
                flow_id=int(f["flow_id"]),
                evidence={"sent": f["sent"], "endpoints_connected": connected},
            )
        )
    return out


def check_energy(rows: list[dict], cfg: Settings, ts: float) -> list[Anomaly]:
    out: list[Anomaly] = []
    for r in rows:
        e = r.get("remaining_energy_j")
        if e is None or float(e) >= cfg.ENERGY_THRESHOLD_J:
            continue
        out.append(
            Anomaly(
                type="low_energy",
                severity="critical" if float(e) <= 0 else "high",
                value=float(e),
                threshold=cfg.ENERGY_THRESHOLD_J,
                description=f"Node {r['node_id']} has {float(e):.2f} J remaining.",
                timestamp=ts,
                affected_nodes=[int(r["node_id"])],
            )
        )
    return out


def check_forwarding(rows: list[dict], cfg: Settings, ts: float) -> list[Anomaly]:
    out: list[Anomaly] = []
    for r in rows:
        fwd, drop = r.get("forwarded"), r.get("dropped")
        if fwd is None or drop is None or (fwd + drop) < cfg.FLOW_OUTAGE_MIN_PACKETS:
            continue
        ratio = float(drop) / float(fwd + drop)
        if ratio <= cfg.FORWARD_DROP_THRESHOLD:
            continue
        out.append(
            Anomaly(
                type="forwarding_drops",
                severity="critical" if ratio > 0.8 else "high",
                value=round(ratio, 4),
                threshold=cfg.FORWARD_DROP_THRESHOLD,
                description=(
                    f"Node {r['node_id']} dropped {int(drop)} of {int(fwd + drop)} packets "
                    "it should have forwarded."
                ),
                timestamp=ts,
                affected_nodes=[int(r["node_id"])],
                evidence={"forwarded": int(fwd), "dropped": int(drop)},
            )
        )
    return out


# --------------------------------------------------------------------------- #
# LangGraph node                                                               #
# --------------------------------------------------------------------------- #


def detect_anomalies(state: AuraState, cfg: Settings | None = None) -> tuple[list[Anomaly], Topology]:
    """Run every rule.  Returns ``(anomalies, topology)``."""
    cfg = cfg or get_settings()
    ts = float(state.get("current_time", 0.0))
    metrics = state.get("raw_metrics") or {}
    window = state.get("window_metrics") or {}
    topo = topology_from_state(state, cfg.LINK_LIFETIME_CAP_S)

    found: list[Anomaly | None] = [
        check_pdr(window, metrics, cfg, ts),
        check_throughput(window, cfg, ts),
        check_delay(window, cfg, ts),
        check_link_breaks(topo, cfg, ts),
        check_partition(metrics, state.get("previous_metrics") or {}, topo, ts),
    ]
    anomalies = [a for a in found if a is not None]
    anomalies += check_isolation(topo, ts)
    anomalies += check_flow_outages(state.get("flow_stats") or [], topo, cfg, ts)
    anomalies += check_energy(state.get("energy_snapshot") or [], cfg, ts)
    anomalies += check_forwarding(state.get("forwarding_snapshot") or [], cfg, ts)
    return anomalies, topo


def monitoring_agent(state: AuraState) -> dict:
    """LangGraph node: deterministic anomaly detection."""
    if state.get("error"):
        return {}
    if not state.get("raw_metrics"):
        msg = "MonitoringAgent: no network metrics at this time"
        logger.warning(msg)
        return {"anomalies": [], "error": msg, "trace": [{"agent": "monitoring", "summary": msg}]}

    anomalies, topo = detect_anomalies(state)
    summary = (
        f"{len(anomalies)} anomalies: "
        + (", ".join(sorted({a.type for a in anomalies})) or "none")
    )
    logger.info("MonitoringAgent t=%.1f %s", state.get("current_time", 0.0), summary)
    return {
        "anomalies": [a.model_dump() for a in anomalies],
        "topology": topo.to_dict(),
        "trace": [{"agent": "monitoring", "summary": summary}],
    }


if __name__ == "__main__":
    import argparse
    import json

    from data_loader import build_state, load_dataset

    parser = argparse.ArgumentParser(description="Run the monitoring agent on ns-3 output")
    parser.add_argument("--time", type=float, default=120.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    cfg = get_settings()
    st = build_state(load_dataset(cfg.NS3_METRICS_DIR), args.time, cfg)
    print(json.dumps(monitoring_agent(st)["anomalies"], indent=2))
