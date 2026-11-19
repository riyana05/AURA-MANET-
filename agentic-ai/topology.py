"""
topology.py — Network graph at one moment, built from node_mobility.csv.

Two nodes are linked when ``distance <= comm_range`` — the same rule the ns-3
scenario enforces with ``RangePropagationLossModel``.

For each link we also compute:

* **margin** = ``1 - distance / range`` (0 = at the edge of range, 1 = co-located).
* **predicted lifetime** (link expiration time, Su & Gerla 2001): how long until
  the two nodes drift out of range if they keep their current velocity.
  Velocities come from the last two mobility samples.  Random-waypoint nodes
  turn at waypoints, so this is a *prediction*, labelled as such, and capped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx


def link_lifetime(
    dx: float, dy: float, dvx: float, dvy: float, comm_range: float, cap: float
) -> float:
    """Time until two nodes are farther apart than *comm_range*.

    Args:
        dx, dy:   Relative position (B - A) in metres.
        dvx, dvy: Relative velocity (B - A) in m/s.
        comm_range: Radio range in metres.
        cap:      Upper bound returned for links that are not separating.

    Returns:
        Seconds (0 if already out of range, *cap* if not separating).
    """
    c = dx * dx + dy * dy - comm_range * comm_range
    if c > 0:
        return 0.0
    a = dvx * dvx + dvy * dvy
    if a < 1e-12:
        return cap
    b = 2.0 * (dx * dvx + dy * dvy)
    disc = b * b - 4.0 * a * c  # c <= 0 so disc >= 0
    t = (-b + math.sqrt(max(disc, 0.0))) / (2.0 * a)
    return max(0.0, min(t, cap))


@dataclass
class Topology:
    """Graph + per-node positions at ``time``."""

    time: float
    comm_range: float
    graph: nx.Graph
    positions: dict[int, tuple[float, float]]
    velocities: dict[int, tuple[float, float] | None]
    broken_links: list[tuple[int, int]] = field(default_factory=list)
    new_links: list[tuple[int, int]] = field(default_factory=list)

    # ------------------------------------------------------------------ #

    @property
    def node_ids(self) -> list[int]:
        return sorted(self.graph.nodes)

    def components(self) -> list[list[int]]:
        return sorted(
            (sorted(c) for c in nx.connected_components(self.graph)),
            key=lambda c: (-len(c), c[0]),
        )

    def isolated(self) -> list[int]:
        return sorted(n for n in self.graph.nodes if self.graph.degree(n) == 0)

    def connected(self, a: int, b: int) -> bool:
        return a in self.graph and b in self.graph and nx.has_path(self.graph, a, b)

    def to_dict(self) -> dict:
        """JSON-friendly representation (stored in the state / API)."""
        return {
            "time": self.time,
            "comm_range_m": self.comm_range,
            "nodes": [
                {
                    "id": n,
                    "x": self.positions[n][0],
                    "y": self.positions[n][1],
                    "degree": self.graph.degree(n),
                    "velocity_known": self.velocities.get(n) is not None,
                }
                for n in self.node_ids
            ],
            "links": [
                {
                    "a": a,
                    "b": b,
                    "distance_m": round(d["distance"], 3),
                    "margin": round(d["margin"], 4),
                    "lifetime_s": None if d["lifetime"] is None else round(d["lifetime"], 3),
                }
                for a, b, d in sorted(self.graph.edges(data=True))
            ],
            "components": self.components(),
            "isolated_nodes": self.isolated(),
            "broken_links": [list(e) for e in self.broken_links],
            "new_links": [list(e) for e in self.new_links],
        }


def _positions(rows: list[dict]) -> dict[int, tuple[float, float]]:
    return {int(r["node_id"]): (float(r["x"]), float(r["y"])) for r in rows}


def _links(pos: dict[int, tuple[float, float]], comm_range: float) -> set[tuple[int, int]]:
    ids = sorted(pos)
    r2 = comm_range * comm_range
    out: set[tuple[int, int]] = set()
    for i, a in enumerate(ids):
        ax, ay = pos[a]
        for b in ids[i + 1 :]:
            bx, by = pos[b]
            if (ax - bx) ** 2 + (ay - by) ** 2 <= r2:
                out.add((a, b))
    return out


def build_topology(
    mobility_snapshot: list[dict],
    mobility_history: list[dict],
    comm_range: float,
    lifetime_cap: float = 60.0,
) -> Topology:
    """Build the topology for the newest sample in *mobility_snapshot*.

    Args:
        mobility_snapshot: One row per node at the current sample.
        mobility_history:  Earlier rows (used for velocities and link breaks).
        comm_range:        Radio range in metres.
        lifetime_cap:      Cap for predicted link lifetimes (s).
    """
    if not mobility_snapshot:
        return Topology(0.0, comm_range, nx.Graph(), {}, {})

    now = max(float(r["time"]) for r in mobility_snapshot)
    current = _positions([r for r in mobility_snapshot if float(r["time"]) == now])

    earlier_times = sorted({float(r["time"]) for r in mobility_history if float(r["time"]) < now})
    previous: dict[int, tuple[float, float]] = {}
    prev_time = None
    if earlier_times:
        prev_time = earlier_times[-1]
        previous = _positions([r for r in mobility_history if float(r["time"]) == prev_time])

    velocities: dict[int, tuple[float, float] | None] = {}
    for n, (x, y) in current.items():
        if prev_time is not None and n in previous and now > prev_time:
            px, py = previous[n]
            dt = now - prev_time
            velocities[n] = ((x - px) / dt, (y - py) / dt)
        else:
            velocities[n] = None

    graph = nx.Graph()
    graph.add_nodes_from(current)
    links_now = _links(current, comm_range)
    for a, b in links_now:
        ax, ay = current[a]
        bx, by = current[b]
        dist = math.hypot(bx - ax, by - ay)
        va, vb = velocities.get(a), velocities.get(b)
        lifetime = None
        if va is not None and vb is not None:
            lifetime = link_lifetime(
                bx - ax, by - ay, vb[0] - va[0], vb[1] - va[1], comm_range, lifetime_cap
            )
        graph.add_edge(
            a,
            b,
            distance=dist,
            margin=max(0.0, 1.0 - dist / comm_range),
            lifetime=lifetime,
        )

    broken: list[tuple[int, int]] = []
    new: list[tuple[int, int]] = []
    if previous:
        links_before = _links(previous, comm_range)
        broken = sorted(links_before - links_now)
        new = sorted(links_now - links_before)

    return Topology(now, comm_range, graph, current, velocities, broken, new)


def topology_from_state(state: dict, lifetime_cap: float = 60.0) -> Topology:
    """Convenience wrapper used by the agents."""
    return build_topology(
        state.get("mobility_snapshot") or [],
        state.get("mobility_history") or [],
        float(state.get("comm_range_m") or 100.0),
        lifetime_cap,
    )
