"""
data_loader.py — Load the ns-3 CSV outputs and build per-time agent state.

Files read from the metrics directory (``settings.NS3_METRICS_DIR``):

=====================  ========  ==========================================
file                   required  used for
=====================  ========  ==========================================
network_metrics.csv    yes       network-wide metrics per second
node_mobility.csv      yes       positions -> topology, isolation, link breaks
packets.csv            yes       per-flow delivery, windowed PDR, verification
experiment_meta.txt    no        radio range, protocol, scenario parameters
node_energy.csv        no        ``time,node_id,remaining_energy_j``
node_forwarding.csv    no        ``time,node_id,forwarded,dropped``
=====================  ========  ==========================================

``routing_events.csv`` is deliberately **not** used: the current ns-3 scenario
fills it with randomly generated events, not real AODV activity.

When an optional file is missing the corresponding fields are reported as
unavailable — they are never filled with made-up values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

import evidence

logger = logging.getLogger(__name__)

NETWORK_METRICS_COLS = [
    "time", "packets_sent", "packets_received", "PDR", "throughput_kbps",
    "average_delay_ms", "window_delay_ms", "packet_loss", "link_breaks",
    "active_flows", "connected_components", "network_density", "average_node_degree",
]
NODE_MOBILITY_COLS = ["time", "node_id", "x", "y", "speed", "neighbor_count", "neighbors"]
PACKETS_COLS = [
    "packet_id", "flow_id", "source", "destination", "send_time",
    "receive_time", "delay_ms", "size_bytes", "status",
]
ENERGY_COLS = ["time", "node_id", "remaining_energy_j"]
FORWARDING_COLS = ["time", "node_id", "forwarded", "dropped"]


class DataLoadError(RuntimeError):
    """Raised when a required CSV is missing or malformed."""


def _read_csv(path: Path, columns: list[str], required: bool) -> pd.DataFrame | None:
    """Read *path*; validate that *columns* are present.

    Returns ``None`` for a missing optional file.  Raises
    :class:`DataLoadError` for a missing required file or missing columns.
    """
    if not path.exists():
        if required:
            raise DataLoadError(f"Required file not found: {path}")
        logger.info("Optional file not present: %s", path.name)
        return None
    try:
        df = pd.read_csv(path, dtype={"neighbors": str} if "neighbors" in columns else None)
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        raise DataLoadError(f"Could not read {path}: {exc}") from exc
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise DataLoadError(f"{path.name} is missing columns: {missing}")
    logger.debug("Loaded %d rows from %s", len(df), path)
    return df


def read_meta(path: Path) -> dict[str, str]:
    """Parse ``experiment_meta.txt`` (``key=value`` lines)."""
    meta: dict[str, str] = {}
    if not path.exists():
        return meta
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            meta[key.strip()] = value.strip()
    return meta


def to_records(df: pd.DataFrame) -> list[dict]:
    """Convert *df* to plain dicts, turning NaN into ``None``."""
    if df is None or df.empty:
        return []
    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict(orient="records")


@dataclass
class Dataset:
    """All CSV data of one ns-3 run, loaded once."""

    data_dir: Path
    metrics: pd.DataFrame
    mobility: pd.DataFrame
    packets: pd.DataFrame
    meta: dict[str, str] = field(default_factory=dict)
    energy: pd.DataFrame | None = None
    forwarding: pd.DataFrame | None = None

    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, data_dir: Path) -> "Dataset":
        """Load a run directory.  Raises :class:`DataLoadError` on bad input."""
        data_dir = Path(data_dir).resolve()
        if not data_dir.is_dir():
            raise DataLoadError(f"Data directory not found: {data_dir}")
        metrics = _read_csv(data_dir / "network_metrics.csv", NETWORK_METRICS_COLS, True)
        mobility = _read_csv(data_dir / "node_mobility.csv", NODE_MOBILITY_COLS, True)
        packets = _read_csv(data_dir / "packets.csv", PACKETS_COLS, True)
        assert metrics is not None and mobility is not None and packets is not None
        return cls(
            data_dir=data_dir,
            metrics=metrics.sort_values("time", kind="stable").reset_index(drop=True),
            mobility=mobility.sort_values(["time", "node_id"], kind="stable").reset_index(
                drop=True
            ),
            packets=packets.sort_values("send_time", kind="stable").reset_index(drop=True),
            meta=read_meta(data_dir / "experiment_meta.txt"),
            energy=_read_csv(data_dir / "node_energy.csv", ENERGY_COLS, False),
            forwarding=_read_csv(data_dir / "node_forwarding.csv", FORWARDING_COLS, False),
        )

    # ------------------------------------------------------------------ #

    @property
    def times(self) -> list[float]:
        return [float(t) for t in self.metrics["time"].unique()]

    @property
    def start_time(self) -> float:
        return float(self.metrics["time"].min())

    @property
    def end_time(self) -> float:
        return float(self.metrics["time"].max())

    @property
    def node_ids(self) -> list[int]:
        return sorted(int(n) for n in self.mobility["node_id"].unique())

    @property
    def availability(self) -> dict[str, bool]:
        """Which kinds of evidence exist for this run."""
        return {
            "network_metrics": True,
            "mobility": True,
            "packets": True,
            "energy": self.energy is not None and not self.energy.empty,
            "forwarding": self.forwarding is not None and not self.forwarding.empty,
            "routing_events": False,  # synthetic in the current scenario, ignored
        }

    def comm_range(self, override: float = 0.0) -> float:
        """Radio range in metres: override > experiment_meta.txt > 100 m."""
        if override and override > 0:
            return float(override)
        try:
            return float(self.meta.get("range", "100"))
        except ValueError:
            return 100.0

    @property
    def protocol(self) -> str:
        return self.meta.get("protocol", "AODV")

    # ------------------------------------------------------------------ #
    # Slicing                                                              #
    # ------------------------------------------------------------------ #

    def metrics_at(self, time: float) -> tuple[dict, dict]:
        """``(row at latest time <= t, previous row)`` as dicts (may be empty)."""
        upto = self.metrics[self.metrics["time"] <= time]
        if upto.empty:
            return {}, {}
        current = to_records(upto.tail(1))[0]
        previous = to_records(upto.iloc[[-2]])[0] if len(upto) > 1 else {}
        return current, previous

    def mobility_at(self, time: float) -> list[dict]:
        """Rows of the latest mobility sample at or before *time* (one per node)."""
        upto = self.mobility[self.mobility["time"] <= time]
        if upto.empty:
            return []
        latest = upto["time"].max()
        return to_records(upto[upto["time"] == latest])

    def mobility_window(self, start: float, end: float) -> list[dict]:
        mask = (self.mobility["time"] >= start) & (self.mobility["time"] <= end)
        return to_records(self.mobility[mask])

    def _latest_per_node(self, df: pd.DataFrame | None, time: float) -> list[dict]:
        if df is None or df.empty:
            return []
        upto = df[df["time"] <= time]
        if upto.empty:
            return []
        latest = upto.sort_values("time", kind="stable").groupby("node_id").tail(1)
        return to_records(latest.sort_values("node_id"))

    def energy_at(self, time: float) -> list[dict]:
        return self._latest_per_node(self.energy, time)

    def forwarding_at(self, time: float) -> list[dict]:
        return self._latest_per_node(self.forwarding, time)

    def packets_window(self, start: float, end: float) -> list[dict]:
        """Packets sent or received in ``(start, end]``."""
        p = self.packets
        mask = ((p["send_time"] > start) & (p["send_time"] <= end)) | (
            (p["receive_time"] > start) & (p["receive_time"] <= end)
        )
        return to_records(p[mask])


# --------------------------------------------------------------------------- #
# Cached loading (used by the API / CLI)                                       #
# --------------------------------------------------------------------------- #

_CACHE: dict[Path, tuple[float, Dataset]] = {}


def load_dataset(data_dir: Path) -> Dataset:
    """Load *data_dir*, reusing the cached copy while the CSVs are unchanged."""
    data_dir = Path(data_dir).resolve()
    stamp_files = [data_dir / f for f in ("network_metrics.csv", "node_mobility.csv", "packets.csv")]
    stamp = max((f.stat().st_mtime for f in stamp_files if f.exists()), default=0.0)
    cached = _CACHE.get(data_dir)
    if cached and cached[0] == stamp:
        return cached[1]
    dataset = Dataset.load(data_dir)
    _CACHE[data_dir] = (stamp, dataset)
    return dataset


# --------------------------------------------------------------------------- #
# State builder                                                                #
# --------------------------------------------------------------------------- #


def build_state(dataset: Dataset, time: float, settings, simulation_id: str = "") -> dict:
    """Build the initial :class:`state.AuraState` dict for simulation time *time*.

    Only ingestion fields are filled; agent-output fields start empty.
    """
    if time < dataset.start_time or time > dataset.end_time:
        raise ValueError(
            f"time {time} outside the run ({dataset.start_time:g}..{dataset.end_time:g} s)"
        )
    raw, previous = dataset.metrics_at(time)
    window = settings.FLOW_WINDOW_S
    return {
        "simulation_id": simulation_id or dataset.data_dir.name,
        "current_time": float(time),
        "data_dir": str(dataset.data_dir),
        "protocol": dataset.protocol,
        "comm_range_m": dataset.comm_range(settings.COMM_RANGE_M),
        "data_availability": dataset.availability,
        "raw_metrics": raw,
        "previous_metrics": previous,
        "mobility_snapshot": dataset.mobility_at(time),
        "mobility_history": dataset.mobility_window(time - settings.ISOLATION_WINDOW_S, time),
        "packets_snapshot": dataset.packets_window(time - 1.0, time),
        "flow_stats": evidence.flow_stats(dataset.packets, time, window),
        "window_metrics": evidence.window_metrics(
            dataset.packets, time - window, time, deliver_by=time
        ).model_dump(),
        "energy_snapshot": dataset.energy_at(time),
        "forwarding_snapshot": dataset.forwarding_at(time),
        "topology": {},
        "anomalies": [],
        "rag_context": [],
        "diagnosis": {},
        "security_report": {},
        "candidate_routes": [],
        "recovery_action": {},
        "validation": {},
        "execution_result": {},
        "verification_result": {},
        "recommendation_check": {},
        "baseline_action": {},
        "trace": [],
        "error": None,
    }
