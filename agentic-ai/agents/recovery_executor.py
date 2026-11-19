"""
recovery_executor.py — Execute validated actions (or record them).

Backends
--------
``DryRunExecutor``
    Never changes anything.  Status ``dry_run`` for actions that *could* be
    executed with ns-3, ``not_executable`` / ``skipped`` otherwise.

``Ns3RerunExecutor``
    Really runs the ns-3 scenario again with the action applied
    (``--protocol`` or ``--range``) and every other parameter copied from the
    baseline run's ``experiment_meta.txt``.  Output goes to
    ``runs/<action-key>/MetricsOutput`` and is cached, so the same action is
    simulated only once.

    This is a **counterfactual** execution: the change applies for the whole
    run (ns-3 cannot be modified mid-run from outside), with the same mobility
    trace, and the verification agent compares the window after the decision
    time against the baseline run.  It is reported as such — the system does
    not claim to have changed a live network.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Protocol

from config import Settings
from schemas import ExecutionResult

logger = logging.getLogger(__name__)


class Executor(Protocol):
    backend: str

    def ready(self) -> bool: ...

    def execute(self, action: dict, validation: dict, state: dict) -> ExecutionResult: ...


def _not_run(action: dict, validation: dict, backend: str) -> ExecutionResult | None:
    t = action.get("action_type")
    if t in {"no_action", "wait_for_reconnection"}:
        return ExecutionResult(status="skipped", backend=backend, details=f"{t}: nothing to execute.")
    if not validation.get("valid"):
        return ExecutionResult(
            status="skipped", backend=backend,
            details="Action failed validation: " + "; ".join(validation.get("errors", [])),
        )
    return None


class DryRunExecutor:
    backend = "dry_run"

    def ready(self) -> bool:
        return True

    def execute(self, action: dict, validation: dict, state: dict) -> ExecutionResult:
        skipped = _not_run(action, validation, self.backend)
        if skipped:
            return skipped
        if action["action_type"] in {"switch_protocol", "increase_tx_range"}:
            return ExecutionResult(status="dry_run", backend=self.backend,
                                   details="Recorded only (dry_run). Use the ns3_rerun backend to execute.")
        return ExecutionResult(status="not_executable", backend=self.backend,
                               details=f"{action['action_type']} is not supported by the ns-3 scenario.")


def parse_meta_args(meta: dict[str, str]) -> dict[str, str]:
    """Turn experiment_meta.txt into scenario command-line arguments."""
    args: dict[str, str] = {}
    if "nodes" in meta:
        args["nodes"] = meta["nodes"]
    if "area" in meta and "x" in meta["area"]:
        ax, ay = meta["area"].split("x", 1)
        args["area-x"], args["area-y"] = ax.strip(), ay.strip()
    m = re.match(r"\s*([\d.]+)\s*to\s*([\d.]+)", meta.get("speed", ""))
    if m:
        args["speed"], args["speed-max"] = m.group(1), m.group(2)
    for key in ("pause", "range", "protocol", "duration", "flows"):
        if key in meta:
            args[key] = meta[key]
    return args


class Ns3RerunExecutor:
    backend = "ns3_rerun"

    def __init__(self, cfg: Settings):
        self.cfg = cfg

    def binary(self) -> Path | None:
        matches = sorted((self.cfg.NS3_DIR / "build" / "scratch").glob(f"*{self.cfg.NS3_SCENARIO}"))
        return matches[0] if matches else None

    def ready(self) -> bool:
        return self.binary() is not None

    def build_args(self, action: dict, meta: dict[str, str]) -> dict[str, str]:
        args = parse_meta_args(meta)
        params = action.get("parameters", {})
        if action["action_type"] == "switch_protocol":
            args["protocol"] = str(params["protocol"])
        elif action["action_type"] == "increase_tx_range":
            args["range"] = f"{float(params['range_m']):g}"
        return args

    def run_key(self, args: dict[str, str]) -> str:
        return "_".join(f"{k}-{v}" for k, v in sorted(args.items())).replace("/", "-")

    def execute(self, action: dict, validation: dict, state: dict) -> ExecutionResult:
        skipped = _not_run(action, validation, self.backend)
        if skipped:
            return skipped
        if not validation.get("executable"):
            return ExecutionResult(status="not_executable", backend=self.backend,
                                   details=f"{action['action_type']} cannot be executed by ns-3 re-run.")
        binary = self.binary()
        if binary is None:
            return ExecutionResult(status="failed", backend=self.backend,
                                   details=f"ns-3 binary for {self.cfg.NS3_SCENARIO} not found; build ns-3 first.")

        from data_loader import read_meta

        meta = read_meta(Path(state["data_dir"]) / "experiment_meta.txt")
        if not meta:
            return ExecutionResult(status="failed", backend=self.backend,
                                   details="experiment_meta.txt missing: cannot reproduce the baseline run.")
        args = self.build_args(action, meta)
        args["exp-id"] = "AURA_" + action["action_type"].upper()
        run_dir = self.cfg.NS3_RUNS_DIR / self.run_key({k: v for k, v in args.items() if k != "exp-id"})
        out_dir = run_dir / "MetricsOutput"
        cmd = [str(binary)] + [f"--{k}={v}" for k, v in args.items()]

        done = out_dir / "packets.csv"
        if done.exists() and (out_dir / "network_metrics.csv").exists():
            return ExecutionResult(status="executed", executed=True, backend=self.backend,
                                   output_dir=str(out_dir), command=cmd,
                                   details="Counterfactual ns-3 run (cached result reused).")
        run_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Running ns-3: %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, cwd=run_dir, capture_output=True, text=True,
                                  timeout=self.cfg.NS3_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return ExecutionResult(status="failed", backend=self.backend, command=cmd,
                                   details=f"ns-3 timed out after {self.cfg.NS3_TIMEOUT_S:g}s")
        if proc.returncode != 0 or not done.exists():
            tail = (proc.stderr or proc.stdout or "")[-800:]
            return ExecutionResult(status="failed", backend=self.backend, command=cmd,
                                   details=f"ns-3 exited with {proc.returncode}: {tail}")
        return ExecutionResult(
            status="executed", executed=True, backend=self.backend, output_dir=str(out_dir), command=cmd,
            details=("Counterfactual ns-3 run with the action applied for the whole simulation "
                     "(same mobility trace). " + proc.stdout.strip().splitlines()[-1]),
        )


def make_executor(backend: str, cfg: Settings) -> Executor:
    if backend == "ns3_rerun":
        return Ns3RerunExecutor(cfg)
    return DryRunExecutor()
