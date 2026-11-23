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


def _routes_state(cfg, n_disconnected: int, n_connected: int, diagnosis: dict | None = None) -> dict:
    """Hand-built orchestrator input: some failing flows partitioned, some reroutable."""
    routes = [{"flow_id": i, "source": i, "destination": 50 + i, "connected": False,
               "window_pdr": 0.0, "best": None} for i in range(n_disconnected)]
    routes += [{"flow_id": 100 + i, "source": 100 + i, "destination": 150 + i, "connected": True,
                "window_pdr": 0.0,
                "best": {"path": [100 + i, 7, 150 + i], "score": 0.3, "hop_count": 2,
                         "min_link_lifetime_s": 5.0}} for i in range(n_connected)]
    return {
        "anomalies": [{"type": "flow_outage", "severity": "high"}],
        "candidate_routes": routes,
        "flow_stats": [{"flow_id": r["flow_id"]} for r in routes],
        "diagnosis": diagnosis or {},
        "security_report": {},
        "protocol": "AODV",
        "comm_range_m": 100.0,
        "data_availability": {},
    }


def test_partition_majority_raises_range_before_rerouting(test_settings):
    act = decide(_routes_state(test_settings, n_disconnected=8, n_connected=2), test_settings)
    assert act.action_type == "increase_tx_range"
    assert "reroute_flow" in act.alternatives


def test_majority_partition_beats_protocol_switch(test_settings):
    diag = {"method": "rule_based", "recommended_action": "switch_protocol", "confidence": 0.5}
    act = decide(_routes_state(test_settings, 3, 0, diag), test_settings)
    assert act.action_type == "increase_tx_range" and "switch_protocol" in act.alternatives


def test_minority_partition_still_reroutes(test_settings):
    act = decide(_routes_state(test_settings, n_disconnected=1, n_connected=3), test_settings)
    assert act.action_type == "reroute_flow"


def test_llm_cannot_downgrade_majority_partition_to_waiting(test_settings):
    diag = {"method": "llm_rag", "recommended_action": "wait_for_reconnection", "confidence": 0.95}
    act = decide(_routes_state(test_settings, 8, 0, diag), test_settings)
    assert act.action_type == "increase_tx_range"
    assert "healed first" in act.rationale


def test_llm_fact_check_catches_wrong_connectivity_claims(tmp_path, test_settings, fake_llm_cls):
    st = _square_state(tmp_path, test_settings)  # flow 0 (0->3) is connected
    llm = fake_llm_cls({
        "root_cause": "partition",
        "findings": [{"fault": "topology_partition", "confidence": 0.9, "affected_nodes": [0, 3]}],
        "recommended_action": "increase_tx_range",
        "reasoning": "...",
        "claimed_disconnected_flows": [0],
        "claimed_isolated_nodes": [2],
    })
    d = make_diagnosis_agent(llm, None, test_settings)(st)["diagnosis"]
    fc = d["fact_check"]
    assert fc["passed"] is False
    assert fc["wrongly_claimed_disconnected"] == [0] and fc["wrongly_claimed_isolated"] == [2]
    assert d["confidence"] == 0.6  # 0.9 - 2 x 0.15
    assert any("Fact-check" in n for n in d["notes"])
    assert '"flow_connectivity"' in llm.calls[0][1]


def _llm_route_state(tmp_path, cfg, fake_llm_cls, path):
    st = _square_state(tmp_path, cfg)  # diamond 0-1-3 / 0-2-3, flow 0 (0->3) failing but connected
    llm = fake_llm_cls({
        "root_cause": "route instability", "recommended_action": "reroute_flow", "reasoning": "",
        "findings": [{"fault": "route_instability", "confidence": 0.8, "affected_nodes": [0, 3]}],
        "route_choices": [{"flow_id": 0, "path": path, "reason": "test"}],
    })
    from agents.routing_agent import routing_agent
    from agents.security_agent import security_agent

    st.update(make_diagnosis_agent(llm, None, cfg)(st))
    st.update(security_agent(st))
    st.update(routing_agent(st))
    return st, llm


def test_llm_gets_link_list_and_its_valid_route_is_used(tmp_path, test_settings, fake_llm_cls):
    st, llm = _llm_route_state(tmp_path, test_settings, fake_llm_cls, [0, 2, 3])
    prompt = llm.calls[0][1]
    assert '"flows_needing_routes"' in prompt and '"links"' in prompt
    r = st["candidate_routes"][0]
    assert r["llm_route"]["path"] == [0, 2, 3] and r["llm_route"]["origin"] == "llm"
    act = decide(st, test_settings)
    assert act.action_type == "reroute_flow" and act.parameters["path"] == [0, 2, 3]
    assert act.parameters["route_origin"] == "LLM-chosen"
    assert validate_action(act.model_dump(), st, test_settings, "dry_run").valid


def test_llm_route_with_invented_link_is_rejected(tmp_path, test_settings, fake_llm_cls):
    st, _ = _llm_route_state(tmp_path, test_settings, fake_llm_cls, [0, 3])  # 0-3 are not neighbours
    r = st["candidate_routes"][0]
    assert r["llm_route"] is None and "do not exist" in r["llm_route_errors"][0]
    act = decide(st, test_settings)
    assert act.parameters["route_origin"] == "computed"
    assert "LLM path not used" in act.rationale
