"""Shared fixtures.  Tests run offline: hashing embedder, no Groq key, temp dirs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REAL_DATA = (ROOT / "../ns-3.48/MetricsOutput").resolve()


@pytest.fixture(autouse=True)
def test_settings(tmp_path, monkeypatch):
    """Isolated settings for every test."""
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("RAG_EMBEDDER", "hashing")
    monkeypatch.setenv("RAG_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("NS3_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("EXECUTOR_BACKEND", "dry_run")
    from config import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def write_run(
    folder: Path,
    positions: dict[float, dict[int, tuple[float, float]]],
    packets: list[tuple] | None = None,
    comm_range: float = 100.0,
    protocol: str = "AODV",
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Write a minimal ns-3 output folder.

    positions: {time: {node: (x, y)}}
    packets:   (packet_id, flow_id, src, dst, send_time, receive_time|None)
    """
    import math

    import networkx as nx

    folder.mkdir(parents=True, exist_ok=True)
    mob = ["time,node_id,x,y,speed,neighbor_count,neighbors"]
    met = ["time,packets_sent,packets_received,PDR,throughput_kbps,average_delay_ms,window_delay_ms,"
           "packet_loss,link_breaks,active_flows,connected_components,network_density,average_node_degree"]
    prev_links: set = set()
    for t in sorted(positions):
        pos = positions[t]
        g = nx.Graph()
        g.add_nodes_from(pos)
        for a in pos:
            for b in pos:
                if a < b and math.dist(pos[a], pos[b]) <= comm_range:
                    g.add_edge(a, b)
        for n, (x, y) in sorted(pos.items()):
            nbrs = ",".join(str(m) for m in sorted(g.neighbors(n)))
            mob.append(f'{t},{n},{x},{y},0,{g.degree(n)},"{nbrs}"')
        links = {tuple(sorted(e)) for e in g.edges}
        breaks = len(prev_links - links) if prev_links else 0
        prev_links = links
        pk = packets or []
        sent = sum(1 for p in pk if p[4] <= t)
        rec = sum(1 for p in pk if p[5] is not None and p[5] <= t)
        pdr = rec / sent if sent else 0
        met.append(f"{t},{sent},{rec},{pdr},0,0,,{1 - pdr if sent else 0},{breaks},1,"
                   f"{nx.number_connected_components(g)},0,0")
    pk_lines = ["packet_id,flow_id,source,destination,send_time,receive_time,delay_ms,size_bytes,status"]
    for pid, fid, s, d, st, rt in packets or []:
        if rt is None:
            pk_lines.append(f"{pid},{fid},{s},{d},{st:.6f},,,1024,lost")
        else:
            pk_lines.append(f"{pid},{fid},{s},{d},{st:.6f},{rt:.6f},{(rt - st) * 1000:.3f},1024,received")
    (folder / "node_mobility.csv").write_text("\n".join(mob) + "\n")
    (folder / "network_metrics.csv").write_text("\n".join(met) + "\n")
    (folder / "packets.csv").write_text("\n".join(pk_lines) + "\n")
    (folder / "experiment_meta.txt").write_text(
        f"EXP_ID=TEST\nnodes={len(next(iter(positions.values())))}\nspeed=5 to 5 m/s\narea=500x500\n"
        f"range={comm_range:g}\nprotocol={protocol}\nduration={max(positions):g}\npause=2\nflows=1\n"
    )
    for name, text in (extra_files or {}).items():
        (folder / name).write_text(text)
    return folder


@pytest.fixture
def partition_run(tmp_path) -> Path:
    """4 nodes in a chain 0-1-2-3.  At t=2 node 3 drifts away (partition).

    Flow 0: 0 -> 3, 20 packets/s; delivered while connected (send <= 1.5 s),
    lost afterwards.  Runs until t=8 so a full 5 s window has no deliveries.
    """
    chain = {0: (0.0, 0.0), 1: (90.0, 0.0), 2: (180.0, 0.0), 3: (270.0, 0.0)}
    away = dict(chain)
    away[3] = (400.0, 0.0)
    positions = {0.0: chain, 1.0: chain, **{float(t): away for t in range(2, 9)}}
    packets = []
    pid = 0
    for i in range(160):  # 20 packets per second from t=0.05 to t=8
        st = 0.05 * (i + 1)
        rt = st + 0.01 if st <= 1.5 else None
        packets.append((pid, 0, 0, 3, st, rt))
        pid += 1
    return write_run(tmp_path / "partition_run", positions, packets)


class FakeLLM:
    """Returns a fixed LLMDiagnosisOutput (or raises) for tests."""

    name = "fake"

    def __init__(self, output=None, error: Exception | None = None):
        self.output = output
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def generate(self, system, user, schema):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return schema.model_validate(self.output)


@pytest.fixture
def fake_llm_cls():
    return FakeLLM
