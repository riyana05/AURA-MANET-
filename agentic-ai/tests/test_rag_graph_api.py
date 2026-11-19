"""RAG store, recovery executor, LangGraph pipeline and FastAPI."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agents.recovery_executor import Ns3RerunExecutor, parse_meta_args
from conftest import REAL_DATA
from pipeline import AuraPipeline
from rag.embeddings import HashingEmbedder
from rag.store import Retriever, append_fault_case, chunk_text


@pytest.fixture
def kb(tmp_path):
    d = tmp_path / "kb"
    d.mkdir()
    (d / "a.md").write_text(
        "# Partition\n\nA network partition splits nodes into components; no route exists.\n\n"
        "# Black hole\n\nA black hole node drops every packet it should forward.\n"
    )
    return d


def test_chunking_respects_size():
    text = "# H\n\n" + "\n\n".join("word " * 60 for _ in range(10))
    chunks = chunk_text(text, 400, 50)
    assert len(chunks) > 1 and all(len(c) <= 460 for _, c in chunks)


def test_retriever_builds_and_finds(kb, tmp_path):
    r = Retriever(kb, tmp_path / "idx", HashingEmbedder())
    top = r.retrieve("black hole drops packets", 1)[0]
    assert "Black hole" in top.text
    assert (tmp_path / "idx" / "index.faiss").exists()
    # Changing the knowledge base makes the index stale -> rebuilt automatically
    (kb / "b.md").write_text("# Energy\n\nBattery depletion makes nodes fail.\n")
    r2 = Retriever(kb, tmp_path / "idx", HashingEmbedder())
    assert "Battery" in r2.retrieve("battery depletion energy", 1)[0].text


def test_fault_case_only_recorded_when_executed(kb):
    state = {"simulation_id": "x", "current_time": 5.0, "anomalies": [{"type": "low_pdr", "severity": "high"}],
             "diagnosis": {"root_cause": "partition"},
             "recovery_action": {"action_type": "increase_tx_range", "parameters": {"range_m": 125}},
             "execution_result": {"executed": False},
             "verification_result": {"status": "not_executed", "mode": "observation_only"}}
    assert append_fault_case(kb, state) is None
    state["execution_result"] = {"executed": True}
    state["verification_result"] = {"status": "success", "mode": "counterfactual_rerun", "change_pct": {"pdr": 20.0}}
    assert append_fault_case(kb, state)["case_id"]
    append_fault_case(kb, state)  # duplicate ignored
    assert len((kb / "fault_cases.jsonl").read_text().splitlines()) == 1


def test_meta_to_args():
    args = parse_meta_args({"nodes": "20", "area": "500x400", "speed": "5 to 10 m/s",
                            "range": "100", "protocol": "AODV", "duration": "300"})
    assert args == {"nodes": "20", "area-x": "500", "area-y": "400", "speed": "5",
                    "speed-max": "10", "range": "100", "protocol": "AODV", "duration": "300"}


def _pipeline(kb, tmp_path, cfg, llm=None):
    return AuraPipeline(cfg, llm=llm, retriever=Retriever(kb, tmp_path / "idx", HashingEmbedder()),
                        record_cases=False)


def test_full_graph_dry_run(partition_run, kb, tmp_path, test_settings):
    p = _pipeline(kb, tmp_path, test_settings)
    r = p.analyze(2.0, partition_run)
    agents = [e["agent"] for e in r["trace"]]
    for name in ["monitoring", "diagnosis", "security", "routing", "orchestrator",
                 "validation", "execution", "verification", "baseline"]:
        assert name in agents
    assert r["execution_result"]["executed"] is False
    assert r["verification_result"]["mode"] == "observation_only"
    assert r["rag_context"], "retrieval should return context"
    json.dumps(r, default=str)
    assert (test_settings.RESULTS_DIR / "latest.json").exists()


def test_no_anomaly_shortcut(partition_run, kb, tmp_path, test_settings):
    r = _pipeline(kb, tmp_path, test_settings).analyze(0.0, partition_run, save=False)
    assert r["anomalies"] == []
    assert r["recovery_action"]["action_type"] == "no_action"
    assert "diagnosis" not in [e["agent"] for e in r["trace"]]


@pytest.mark.skipif(not (REAL_DATA.exists() and Ns3RerunExecutor.__init__), reason="needs data")
def test_ns3_rerun_execution(kb, tmp_path, test_settings):
    if not Ns3RerunExecutor(test_settings).ready():
        pytest.skip("ns-3 scenario binary not built")
    p = _pipeline(kb, tmp_path, test_settings)
    r = p.analyze(80.0, REAL_DATA, backend="ns3_rerun", save=False)
    assert r["recovery_action"]["action_type"] == "increase_tx_range"
    assert r["execution_result"]["executed"] is True
    v = r["verification_result"]
    assert v["mode"] == "counterfactual_rerun"
    assert v["status"] in {"success", "no_improvement", "degraded"}


def test_api_endpoints(partition_run, kb, tmp_path, test_settings):
    from api.server import create_app

    client = TestClient(create_app(_pipeline(kb, tmp_path, test_settings)))
    d = str(partition_run)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/run", params={"data_dir": d}).json()["nodes"] == [0, 1, 2, 3]
    assert client.get("/topology", params={"time": 2, "data_dir": d}).json()["isolated_nodes"] == [3]
    types = {a["type"] for a in client.get("/anomalies", params={"time": 2, "data_dir": d}).json()["anomalies"]}
    assert "network_partition" in types
    assert len(client.get("/anomalies/timeline", params={"data_dir": d}).json()["timeline"]) == 9
    r = client.post("/analyze", json={"time": 8, "data_dir": d, "save": False}).json()
    assert r["recovery_action"]["action_type"] in {"increase_tx_range", "wait_for_reconnection"}
    assert client.post("/rag/query", json={"query": "partition", "k": 1}).json()["results"]
    assert client.get("/metrics", params={"time": 999, "data_dir": d}).status_code == 422
    assert client.get("/run", params={"data_dir": str(tmp_path / "nope")}).status_code == 404
    cmp = client.post("/compare", json={"step": 1, "data_dir": d}).json()
    assert cmp["summary"]["samples"] == 9
