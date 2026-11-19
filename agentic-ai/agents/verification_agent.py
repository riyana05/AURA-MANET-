"""
verification_agent.py — Check whether a recovery action helped.

Two modes, always labelled in the result:

``counterfactual_rerun``  (action executed by the ns-3 re-run backend)
    *before* = baseline run, *after* = run with the action, both over the same
    window ``(t, t + VERIFY_WINDOW_S]`` and the same mobility trace.  The
    status is ``success`` if PDR rises by at least ``VERIFY_MIN_IMPROVEMENT``,
    ``degraded`` if it falls by that much, ``no_improvement`` otherwise.

``observation_only``  (nothing executed)
    *before* = ``(t - W, t]``, *after* = ``(t, t + W]`` of the same run, i.e.
    how the network evolved **without** intervention.  Status is
    ``not_executed``; this never counts as a recovery.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config import Settings
from data_loader import DataLoadError, load_dataset
from evidence import pct_change, window_metrics
from schemas import VerificationResult, WindowMetrics

logger = logging.getLogger(__name__)


def _changes(before: WindowMetrics, after: WindowMetrics) -> dict[str, float | None]:
    return {
        "pdr": pct_change(before.pdr, after.pdr),
        "throughput_kbps": pct_change(before.throughput_kbps, after.throughput_kbps),
        "mean_delay_ms": pct_change(before.mean_delay_ms, after.mean_delay_ms),
        "packets_sent": pct_change(float(before.packets_sent), float(after.packets_sent)),
    }


def same_mobility(base, other, start: float, end: float) -> bool:
    cols = ["time", "node_id", "x", "y"]
    a = base.mobility[(base.mobility.time >= start) & (base.mobility.time <= end)][cols].reset_index(drop=True)
    b = other.mobility[(other.mobility.time >= start) & (other.mobility.time <= end)][cols].reset_index(drop=True)
    return a.shape == b.shape and bool(((a - b).abs() < 1e-6).all().all())


def verify(state: dict, cfg: Settings) -> VerificationResult:
    t = float(state["current_time"])
    w = cfg.VERIFY_WINDOW_S
    base = load_dataset(Path(state["data_dir"]))
    end = min(t + w, base.end_time)
    execution = state.get("execution_result") or {}
    notes: list[str] = []

    if end <= t:
        return VerificationResult(status="insufficient_data", notes=["No simulation time left after t."])
    if end < t + w:
        notes.append(f"Window clipped to {end - t:g}s (end of simulation).")

    if execution.get("executed") and execution.get("output_dir"):
        try:
            after_ds = load_dataset(Path(execution["output_dir"]))
        except DataLoadError as exc:
            return VerificationResult(status="not_comparable", mode="counterfactual_rerun",
                                      notes=[f"Could not load re-run output: {exc}"])
        if not same_mobility(base, after_ds, t, end):
            return VerificationResult(status="not_comparable", mode="counterfactual_rerun",
                                      notes=["Mobility differs between runs; comparison would be invalid."])
        before = window_metrics(base.packets, t, end)
        after = window_metrics(after_ds.packets, t, end)
        if before.pdr is None or after.pdr is None:
            return VerificationResult(status="insufficient_data", mode="counterfactual_rerun",
                                      before=before, after=after, notes=notes + ["No packets sent in window."])
        gain = after.pdr - before.pdr
        if gain >= cfg.VERIFY_MIN_IMPROVEMENT:
            status = "success"
        elif gain <= -cfg.VERIFY_MIN_IMPROVEMENT:
            status = "degraded"
        else:
            status = "no_improvement"
        notes.append(f"PDR {before.pdr:.3f} -> {after.pdr:.3f} ({gain:+.3f}) over ({t:g}, {end:g}]s; "
                     "before = baseline run, after = run with the action (same mobility).")
        if before.packets_sent != after.packets_sent:
            notes.append(f"Packets sent differ ({before.packets_sent} vs {after.packets_sent}): "
                         "some protocols refuse to send without a route; read PDR with throughput.")
        return VerificationResult(status=status, mode="counterfactual_rerun", before=before,
                                  after=after, change_pct=_changes(before, after), notes=notes)

    start = max(t - w, base.start_time)
    before = window_metrics(base.packets, start, t)
    after = window_metrics(base.packets, t, end)
    notes.append("Action not executed: showing how the network evolved without intervention.")
    return VerificationResult(status="not_executed", mode="observation_only", before=before,
                              after=after, change_pct=_changes(before, after), notes=notes)
