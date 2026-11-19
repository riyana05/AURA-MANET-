"""
action_validator.py — Validate a recovery action before anything executes it.

Checks that the action is well-formed and consistent with the *current*
network (nodes exist, route links exist, parameters are in bounds, no unsafe
relays) and decides whether the configured executor can really run it.

Executable actions per backend:

=====================  ==========  =========================================
action                 dry_run     ns3_rerun
=====================  ==========  =========================================
switch_protocol        no          yes (re-runs ns-3 with --protocol)
increase_tx_range      no          yes (re-runs ns-3 with --range)
reroute_flow           no          no  (scenario has no per-flow routing control)
isolate_node           no          no  (scenario cannot disable a node)
no_action / wait       nothing to execute
=====================  ==========  =========================================
"""

from __future__ import annotations

from config import Settings
from schemas import RecoveryAction, ValidationResult

NS3_EXECUTABLE = {"switch_protocol", "increase_tx_range"}


def validate_action(
    action: dict, state: dict, cfg: Settings, backend: str, backend_ready: bool = True
) -> ValidationResult:
    """Validate *action* (a ``RecoveryAction`` dict) against *state*."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        act = RecoveryAction.model_validate(action)
    except Exception as exc:  # noqa: BLE001
        return ValidationResult(valid=False, errors=[f"malformed action: {exc}"])

    topo = state.get("topology") or {}
    nodes = {n["id"] for n in topo.get("nodes", [])}
    links = {tuple(sorted((link["a"], link["b"]))) for link in topo.get("links", [])}
    security = state.get("security_report") or {}
    availability = state.get("data_availability") or {}

    unknown = [n for n in act.target_nodes if n not in nodes]
    if unknown:
        errors.append(f"target nodes do not exist: {unknown}")

    t = act.action_type
    if t == "reroute_flow":
        path = act.parameters.get("path") or []
        flows = {f["flow_id"]: f for f in state.get("flow_stats") or []}
        flow = flows.get(act.flow_id)
        if flow is None:
            errors.append(f"unknown flow_id {act.flow_id}")
        elif not path or path[0] != flow["source"] or path[-1] != flow["destination"]:
            errors.append(f"path {path} does not connect flow endpoints {flow['source']}->{flow['destination']}")
        if len(set(path)) != len(path):
            errors.append("path contains a loop")
        missing = [(a, b) for a, b in zip(path, path[1:]) if tuple(sorted((a, b))) not in links]
        if missing:
            errors.append(f"path uses links that do not exist now: {missing}")
        bad_relays = set(path[1:-1]) & set(security.get("suspicious_nodes", []) + security.get("low_energy_nodes", []))
        if bad_relays:
            errors.append(f"path relays through unsafe nodes {sorted(bad_relays)}")
        warnings.append("reroute_flow is advisory: the ns-3 scenario has no per-flow routing control.")
    elif t == "increase_tx_range":
        current = float(state.get("comm_range_m") or 0)
        new = act.parameters.get("range_m")
        if not isinstance(new, (int, float)):
            errors.append("increase_tx_range needs numeric parameters.range_m")
        elif new <= current:
            errors.append(f"new range {new} m is not larger than current {current} m")
        elif new > cfg.MAX_TX_RANGE_M:
            errors.append(f"new range {new} m exceeds MAX_TX_RANGE_M {cfg.MAX_TX_RANGE_M} m")
    elif t == "switch_protocol":
        proto = act.parameters.get("protocol")
        if proto not in cfg.ALLOWED_PROTOCOLS:
            errors.append(f"protocol {proto!r} not in {cfg.ALLOWED_PROTOCOLS}")
        elif proto == state.get("protocol"):
            errors.append(f"network already runs {proto}")
    elif t == "isolate_node":
        if not availability.get("forwarding"):
            errors.append("isolate_node requires forwarding evidence, which is not available")
        not_suspicious = [n for n in act.target_nodes if n not in security.get("suspicious_nodes", [])]
        if not_suspicious:
            errors.append(f"nodes {not_suspicious} are not flagged suspicious")
        if not act.target_nodes:
            errors.append("isolate_node needs target nodes")
        warnings.append("isolate_node cannot be applied by the current ns-3 scenario.")

    valid = not errors
    executable = valid and backend == "ns3_rerun" and t in NS3_EXECUTABLE and backend_ready
    if valid and t in NS3_EXECUTABLE and backend == "ns3_rerun" and not backend_ready:
        warnings.append("ns-3 binary not found; action cannot be executed.")
    if valid and t in NS3_EXECUTABLE and backend == "dry_run":
        warnings.append("dry_run backend: the action is recorded, not executed.")
    return ValidationResult(
        valid=valid,
        executable=executable,
        backend=backend if executable else None,
        errors=errors,
        warnings=warnings,
    )
