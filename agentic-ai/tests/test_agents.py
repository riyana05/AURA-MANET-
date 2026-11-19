"""Security, routing, diagnosis, orchestrator, validator, baseline."""

from __future__ import annotations

from agents.action_validator import validate_action
from agents.diagnosis_agent import make_diagnosis_agent, rule_based_diagnosis
from agents.monitoring_agent import monitoring_agent
from agents.orchestrator_agent import decide
from agents.routing_agent import graph_from_topology, route_flow
from agents.security_agent import assess_nodes
from baseline import baseline_decide
from conftest import write_run
from data_loader import build_state, load_dataset
from schemas import RetrievedChunk


def _square_state(tmp_path, cfg, forwarding: str | None = None):
    """Diamond 0-1-3 / 0-2-3 (both 2 hops) at t=0 and t=1; flow 0 -> 3 failing."""
    pos = {0: (0, 0), 1: (70, 60), 2: (70, -60), 3: (140, 0)}
    packets = [(i, 0, 0, 3, 0.05 * (i + 1), None) for i in range(20)]
    extra = {"node_forwarding.csv": forwarding} if forwarding else None
    run = write_run(tmp_path / "sq", {0.0: pos, 1.0: pos}, packets, extra_files=extra)
    st = build_state(load_dataset(run), 1.0, cfg)
    st.update(monitoring_agent(st))
    return st


def test_security_without_forwarding_is_unknown(tmp_path, test_settings):
    st = _square_state(tmp_path, test_settings)
    rep = assess_nodes(st, test_settings)
    assert rep.risk_level == "unknown"
    assert all(n.trust is None for n in rep.nodes)
    assert rep.suspicious_nodes == []


def test_routing_avoids_suspicious_relay(tmp_path, test_settings):
    fwd = "time,node_id,forwarded,dropped\n1,1,2,40\n1,2,40,0\n"
    st = _square_state(tmp_path, test_settings, fwd)
    sec = assess_nodes(st, test_settings).model_dump()
    assert sec["suspicious_nodes"] == [1]
    flow = st["flow_stats"][0]
    r = route_flow(graph_from_topology(st["topology"]), flow, sec, test_settings)
    assert r.connected and r.best is not None
    assert r.best.path == [0, 2, 3]
    assert all(1 not in c.path for c in r.candidates)


def test_routing_reports_partition(partition_run, test_settings):
    st = build_state(load_dataset(partition_run), 8.0, test_settings)
    st.update(monitoring_agent(st))
    r = route_flow(graph_from_topology(st["topology"]), st["flow_stats"][0], {}, test_settings)
    assert not r.connected and r.best is None


def test_rule_based_diagnosis_partition(partition_run, test_settings):
    st = build_state(load_dataset(partition_run), 8.0, test_settings)
    st.update(monitoring_agent(st))
    d = rule_based_diagnosis(st, [])
    assert d.findings[0].fault == "topology_partition"
    assert d.recommended_action == "increase_tx_range"  # the only flow is partitioned


def _chunk(cid="fault_patterns#1"):
    return RetrievedChunk(chunk_id=cid, source="fault_patterns.md", text="partition text", score=0.9)


def test_llm_output_is_validated(partition_run, test_settings, fake_llm_cls):
    st = build_state(load_dataset(partition_run), 8.0, test_settings)
    st.update(monitoring_agent(st))
    llm = fake_llm_cls({
        "root_cause": "black hole at node 99",
        "findings": [{"fault": "black_hole_attack", "confidence": 0.95,
                      "affected_nodes": [2, 99], "anomaly_types": ["flow_outage"],
                      "evidence": ["flow 0 delivered nothing"]}],
        "recommended_action": "isolate_node",
        "reasoning": "...",
        "cited_chunk_ids": ["fault_patterns#1", "made_up#7"],
    })
    out = make_diagnosis_agent(llm, lambda q, k: [_chunk()], test_settings)(st)
    d = out["diagnosis"]
    assert d["method"] == "llm_rag"
    assert d["findings"][0]["affected_nodes"] == [2]        # 99 removed
    assert d["findings"][0]["confidence"] == 0.3            # no forwarding data
    assert d["recommended_action"] == "no_action"           # isolate downgraded
    assert [s["chunk_id"] for s in d["sources"]] == ["fault_patterns#1"]
    assert any("made_up#7" in n for n in d["notes"])
    # The prompt contains measured facts and the retrieved passage
    assert "fault_patterns#1" in llm.calls[0][1] and '"flows_in_outage"' in llm.calls[0][1]


def test_llm_failure_falls_back(partition_run, test_settings, fake_llm_cls):
    st = build_state(load_dataset(partition_run), 8.0, test_settings)
    st.update(monitoring_agent(st))
    out = make_diagnosis_agent(fake_llm_cls(error=TimeoutError("boom")), None, test_settings)(st)
    assert out["diagnosis"]["method"] == "rule_based"
    assert any("boom" in n for n in out["diagnosis"]["notes"])


def _full_state(st, cfg, llm=None):
    st.update(make_diagnosis_agent(llm, None, cfg)(st))
    from agents.routing_agent import routing_agent
    from agents.security_agent import security_agent

    st.update(security_agent(st))
    st.update(routing_agent(st))
    return st


def test_orchestrator_reroutes_connected_flow(tmp_path, test_settings):
    st = _full_state(_square_state(tmp_path, test_settings), test_settings)
    act = decide(st, test_settings)
    assert act.action_type == "reroute_flow"
    v = validate_action(act.model_dump(), st, test_settings, "dry_run")
    assert v.valid and not v.executable


def test_orchestrator_severity_gate(tmp_path, test_settings):
    st = _full_state(_square_state(tmp_path, test_settings), test_settings)
    assert decide(st, test_settings).action_type != "no_action"
    # Same situation, but every anomaly only 'medium' (gate is 'high'): no change
    st["anomalies"] = [{**a, "severity": "medium"} for a in st["anomalies"]]
    act = decide(st, test_settings)
    assert act.action_type == "no_action" and "unnecessary" in act.rationale


def test_validator_rejects_bad_actions(tmp_path, test_settings):
    st = _full_state(_square_state(tmp_path, test_settings), test_settings)
    bad_path = {"action_type": "reroute_flow", "flow_id": 0, "target_nodes": [0, 3],
                "parameters": {"path": [0, 3]}}
    assert not validate_action(bad_path, st, test_settings, "dry_run").valid  # no 0-3 link
    same_proto = {"action_type": "switch_protocol", "parameters": {"protocol": "AODV"}}
    assert not validate_action(same_proto, st, test_settings, "ns3_rerun").valid
    too_far = {"action_type": "increase_tx_range", "parameters": {"range_m": 10_000}}
    assert not validate_action(too_far, st, test_settings, "ns3_rerun").valid
    isolate = {"action_type": "isolate_node", "target_nodes": [1], "parameters": {"nodes": [1]}}
    assert not validate_action(isolate, st, test_settings, "dry_run").valid
    ok = {"action_type": "increase_tx_range", "parameters": {"range_m": 125}}
    v = validate_action(ok, st, test_settings, "ns3_rerun", backend_ready=True)
    assert v.valid and v.executable


def test_baseline_differs_from_agentic_on_partition(partition_run, test_settings):
    st = build_state(load_dataset(partition_run), 2.0, test_settings)
    st = _full_state({**st, **monitoring_agent(st)}, test_settings)
    assert baseline_decide(st, test_settings).action_type == "increase_tx_range"
