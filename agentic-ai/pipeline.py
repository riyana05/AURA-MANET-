"""
pipeline.py — High-level service used by the CLI and the FastAPI server.

    pipe = AuraPipeline()                      # LLM + RAG from settings
    state = pipe.analyze(120.0)                # one time step, full graph
    pipe.analyze(120.0, backend="ns3_rerun")   # execute + verify in ns-3
    pipe.compare_with_baseline(step=10)        # agentic vs rule-based
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from agents.monitoring_agent import detect_anomalies
from agents.recovery_executor import make_executor
from config import Settings, get_settings
from data_loader import Dataset, build_state, load_dataset
from graph import build_graph
from llm import StructuredLLM, get_llm
from rag.store import Retriever, append_fault_case
from schemas import ACTION_DISRUPTION, SEVERITY_RANK

logger = logging.getLogger(__name__)

_AUTO = "auto"


class AuraPipeline:
    def __init__(
        self,
        cfg: Settings | None = None,
        llm: StructuredLLM | None | str = _AUTO,
        retriever: Retriever | None | str = _AUTO,
        record_cases: bool = True,
    ):
        self.cfg = cfg or get_settings()
        self.llm = get_llm(self.cfg) if llm == _AUTO else llm
        self.retriever = Retriever.from_settings(self.cfg) if retriever == _AUTO else retriever
        self.record_cases = record_cases
        self._graphs: dict[tuple[str, bool], object] = {}

    # ------------------------------------------------------------------ #

    def dataset(self, data_dir: Path | str | None = None) -> Dataset:
        return load_dataset(Path(data_dir) if data_dir else self.cfg.NS3_METRICS_DIR)

    def graph(self, backend: str, use_llm: bool = True):
        key = (backend, use_llm)
        if key not in self._graphs:
            retrieve = self.retriever.retrieve if self.retriever is not None else None
            record = None
            if self.record_cases:
                record = lambda st: append_fault_case(self.cfg.RAG_KB_DIR, st)  # noqa: E731
            reason = None if use_llm else "LLM switched off for this run on purpose (rule-based mode)."
            self._graphs[key] = build_graph(
                self.cfg, self.llm if use_llm else None, retrieve,
                make_executor(backend, self.cfg), record, llm_off_reason=reason,
            )
        return self._graphs[key]

    # ------------------------------------------------------------------ #

    def analyze(
        self,
        time: float,
        data_dir: Path | str | None = None,
        backend: str | None = None,
        use_llm: bool = True,
        save: bool = True,
    ) -> dict:
        """Run the full agent graph for one simulation time."""
        ds = self.dataset(data_dir)
        backend = backend or self.cfg.EXECUTOR_BACKEND
        state = build_state(ds, time, self.cfg)
        result = self.graph(backend, use_llm).invoke(state)
        if save:
            self.save(result)
        return result

    def analyze_both(
        self,
        time: float,
        data_dir: Path | str | None = None,
        backend: str | None = None,
    ) -> tuple[dict, dict]:
        """Analyse the same moment with the LLM and without it (rule-based).

        Returns ``(with_llm, without_llm)``.  If no LLM is configured the first
        result is rule-based too and says so in its diagnosis notes.
        """
        with_llm = self.analyze(time, data_dir, backend, use_llm=True, save=True)
        without_llm = self.analyze(time, data_dir, backend, use_llm=False, save=False)
        return with_llm, without_llm

    def write_report(self, with_llm: dict, without_llm: dict) -> Path:
        """Write the side-by-side HTML report; returns results/report.html."""
        from report import render_comparison

        page = render_comparison(with_llm, without_llm, getattr(self.llm, "name", None))
        self.cfg.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out_dir = self.cfg.RESULTS_DIR / str(with_llm.get("simulation_id", "run"))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"report_t{float(with_llm['current_time']):g}.html").write_text(page)
        latest = self.cfg.RESULTS_DIR / "report.html"
        latest.write_text(page)
        return latest

    def save(self, result: dict) -> Path:
        """Write the result for the visualizer: results/<run>/t<time>.json + latest.json."""
        out_dir = self.cfg.RESULTS_DIR / str(result.get("simulation_id", "run"))
        out_dir.mkdir(parents=True, exist_ok=True)
        slim = {k: v for k, v in result.items()
                if k not in {"mobility_history", "packets_snapshot", "mobility_snapshot"}}
        path = out_dir / f"t{float(result['current_time']):g}.json"
        text = json.dumps(slim, indent=1, default=str)
        path.write_text(text)
        (self.cfg.RESULTS_DIR / "latest.json").write_text(text)
        return path

    # ------------------------------------------------------------------ #

    def monitor_sweep(self, data_dir=None, start=None, end=None, step=1.0) -> list[dict]:
        """Monitoring only (fast, no LLM) for every step: the anomaly timeline."""
        ds = self.dataset(data_dir)
        out = []
        for t in _times(ds, start, end, step):
            anomalies, _ = detect_anomalies(build_state(ds, t, self.cfg), self.cfg)
            out.append({
                "time": t,
                "count": len(anomalies),
                "types": sorted({a.type for a in anomalies}),
                "max_severity": max((a.severity for a in anomalies), key=SEVERITY_RANK.get, default=None),
            })
        return out

    def compare_with_baseline(
        self,
        data_dir=None,
        start=None,
        end=None,
        step: float = 10.0,
        backend: str | None = None,
        use_llm: bool = False,
    ) -> dict:
        """Run agentic pipeline and baseline over many times and summarise.

        With ``backend="ns3_rerun"`` executable actions of the agentic system
        are verified in ns-3; baseline actions are executed and verified the
        same way (re-runs are cached, so identical actions run once).
        """
        ds = self.dataset(data_dir)
        backend = backend or self.cfg.EXECUTOR_BACKEND
        executor = make_executor(backend, self.cfg)
        from agents.action_validator import validate_action
        from agents.verification_agent import verify

        rows = []
        for t in _times(ds, start, end, step):
            res = self.analyze(t, data_dir, backend, use_llm=use_llm, save=False)
            base = res.get("baseline_action", {})
            b_action, b_valid = base.get("action", {}), base.get("validation", {})
            b_ver = {}
            if b_valid.get("executable"):
                b_exec = executor.execute(b_action, b_valid, res)
                b_ver = verify({**res, "execution_result": b_exec.model_dump()}, self.cfg).model_dump()
            rows.append({
                "time": t,
                "agentic": res["recovery_action"]["action_type"],
                "agentic_valid": res["validation"]["valid"],
                "agentic_verification": res["verification_result"].get("status"),
                "agentic_pdr_change": (res["verification_result"].get("change_pct") or {}).get("pdr")
                if res["verification_result"].get("mode") == "counterfactual_rerun" else None,
                "baseline": b_action.get("action_type"),
                "baseline_valid": b_valid.get("valid"),
                "baseline_errors": b_valid.get("errors", []),
                "baseline_verification": b_ver.get("status") if b_ver else
                ("not_executed" if b_action.get("action_type") not in {"no_action", "wait_for_reconnection"} else "skipped"),
                "baseline_pdr_change": (b_ver.get("change_pct") or {}).get("pdr") if b_ver else None,
            })
        return {"backend": backend, "use_llm": use_llm, "rows": rows, "summary": summarise(rows)}


def _times(ds: Dataset, start, end, step) -> list[float]:
    lo = ds.start_time if start is None else float(start)
    hi = ds.end_time if end is None else float(end)
    times, t = [], lo
    while t <= hi + 1e-9:
        times.append(round(t, 6))
        t += step
    return times


def summarise(rows: list[dict]) -> dict:
    def side(prefix: str) -> dict:
        actions = Counter(r[prefix] for r in rows)
        changes = [r for r in rows if r[prefix] not in {"no_action", "wait_for_reconnection"}]
        outcomes = Counter(r[f"{prefix}_verification"] for r in rows)
        pdr = [r[f"{prefix}_pdr_change"] for r in rows if r[f"{prefix}_pdr_change"] is not None]
        return {
            "actions": dict(actions),
            "network_changes": len(changes),
            "disruption_score": sum(ACTION_DISRUPTION.get(r[prefix], 0) for r in rows),
            "invalid_actions": sum(1 for r in rows if not r[f"{prefix}_valid"]),
            "verification": dict(outcomes),
            "mean_verified_pdr_change_pct": round(sum(pdr) / len(pdr), 2) if pdr else None,
        }

    return {
        "samples": len(rows),
        "agreement": round(sum(r["agentic"] == r["baseline"] for r in rows) / max(len(rows), 1), 3),
        "agentic": side("agentic"),
        "baseline": side("baseline"),
    }
