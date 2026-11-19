"""Data layer, topology and monitoring agent."""

from __future__ import annotations

import math

import pytest

from agents.monitoring_agent import detect_anomalies, monitoring_agent
from conftest import REAL_DATA, write_run
from data_loader import DataLoadError, Dataset, build_state, load_dataset
from evidence import flow_stats, window_metrics
from topology import link_lifetime, topology_from_state


def test_link_lifetime_math():
    # 50 m apart, separating at 10 m/s along x, range 100 -> 5 s
    assert math.isclose(link_lifetime(50, 0, 10, 0, 100, 60), 5.0)
    # approaching: reaches 100 m on the far side after (50 + 100) / 10 = 15 s
    assert math.isclose(link_lifetime(50, 0, -10, 0, 100, 60), 15.0)
    assert link_lifetime(50, 0, 0, 0, 100, 60) == 60  # not moving -> cap
    assert link_lifetime(150, 0, 0, 0, 100, 60) == 0.0  # already out of range


def test_missing_required_file(tmp_path):
    with pytest.raises(DataLoadError):
        Dataset.load(tmp_path)


def test_optional_data_reported_unavailable(partition_run):
    ds = load_dataset(partition_run)
    assert ds.availability["energy"] is False
    assert ds.availability["forwarding"] is False
    assert ds.comm_range() == 100.0


def test_windowed_metrics_do_not_use_future(partition_run, test_settings):
    ds = load_dataset(partition_run)
    # packet sent at 1.0 is received at 1.01 -> not yet delivered at t=1.0
    f = flow_stats(ds.packets, 1.0, 1.0)[0]
    assert f["sent"] == 20 and f["delivered"] == 19
    # Offline (final outcome) the same window counts it as delivered
    assert window_metrics(ds.packets, 0.0, 1.0).packets_delivered == 20


def test_partition_isolation_and_outage_detected(partition_run, test_settings):
    ds = load_dataset(partition_run)
    found, topo = detect_anomalies(build_state(ds, 2.0, test_settings), test_settings)
    types = {a.type for a in found}
    assert {"network_partition", "isolated_node"} <= types
    iso = next(a for a in found if a.type == "isolated_node")
    assert iso.affected_nodes == [3]
    assert topo.broken_links == [(2, 3)]

    # One second later the partition is old news: no repeated partition/isolation alert
    found3, _ = detect_anomalies(build_state(ds, 3.0, test_settings), test_settings)
    assert not {"network_partition", "isolated_node"} & {a.type for a in found3}

    # At t=4 the 5 s window still holds pre-partition deliveries: no outage yet
    found4, _ = detect_anomalies(build_state(ds, 4.0, test_settings), test_settings)
    assert "flow_outage" not in {a.type for a in found4}

    # By t=8 the window (3, 8] has 100 packets sent and none delivered
    found4, _ = detect_anomalies(build_state(ds, 8.0, test_settings), test_settings)
    outage = next(a for a in found4 if a.type == "flow_outage")
    assert outage.evidence["endpoints_connected"] is False
    assert outage.affected_nodes == [0, 3]


def test_energy_and_forwarding_rules_need_data(tmp_path, test_settings):
    pos = {0.0: {0: (0, 0), 1: (50, 0)}, 1.0: {0: (0, 0), 1: (50, 0)}}
    run = write_run(tmp_path / "r", pos, extra_files={
        "node_energy.csv": "time,node_id,remaining_energy_j\n1,0,2.0\n1,1,50\n",
        "node_forwarding.csv": "time,node_id,forwarded,dropped\n1,0,5,45\n1,1,50,0\n",
    })
    ds = load_dataset(run)
    found, _ = detect_anomalies(build_state(ds, 1.0, test_settings), test_settings)
    types = {a.type: a for a in found}
    assert types["low_energy"].affected_nodes == [0]
    assert types["forwarding_drops"].affected_nodes == [0]


def test_monitoring_node_output_is_serialisable(partition_run, test_settings):
    import json

    out = monitoring_agent(build_state(load_dataset(partition_run), 2.0, test_settings))
    json.dumps(out)
    assert out["topology"]["isolated_nodes"] == [3]


@pytest.mark.skipif(not REAL_DATA.exists(), reason="ns-3 output not present")
def test_topology_matches_ns3_every_second(test_settings):
    ds = load_dataset(REAL_DATA)
    for t in ds.times:
        st = build_state(ds, t, test_settings)
        topo = topology_from_state(st)
        assert len(topo.components()) == st["raw_metrics"]["connected_components"], t
        if t > ds.start_time:
            assert len(topo.broken_links) == st["raw_metrics"]["link_breaks"], t
