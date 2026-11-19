"""
evidence.py — Measurements derived from ``packets.csv``.

Everything here is computed from recorded packets only; nothing is estimated
or invented.  Two rules keep the numbers honest:

* **Monitoring at time t must not look into the future.**  Pass
  ``deliver_by=t`` so a packet only counts as delivered if its
  ``receive_time <= t`` (packets still in flight count as not delivered yet,
  the same convention ns-3's ``network_metrics.csv`` uses).
* **Offline verification may use the final outcome.**  Pass
  ``deliver_by=None`` to count every packet that was eventually received.
"""

from __future__ import annotations

import math

import pandas as pd

from schemas import WindowMetrics


def _sent_in(packets: pd.DataFrame, start: float, end: float) -> pd.DataFrame:
    """Packets whose ``send_time`` lies in ``(start, end]``."""
    if packets.empty:
        return packets
    mask = (packets["send_time"] > start) & (packets["send_time"] <= end)
    return packets[mask]


def _delivered_mask(rows: pd.DataFrame, deliver_by: float | None) -> pd.Series:
    received = rows["receive_time"].notna()
    if deliver_by is not None:
        received &= rows["receive_time"] <= deliver_by
    return received


def window_metrics(
    packets: pd.DataFrame,
    start: float,
    end: float,
    deliver_by: float | None = None,
) -> WindowMetrics:
    """PDR, throughput and delay for packets sent in ``(start, end]``.

    Args:
        packets:    packets.csv frame (``receive_time`` NaN for lost packets).
        start:      Window start (s, exclusive).
        end:        Window end (s, inclusive).
        deliver_by: Count only deliveries up to this time (None = final outcome).

    Returns:
        :class:`WindowMetrics`.  Ratios are ``None`` when nothing was sent.
    """
    sent = _sent_in(packets, start, end)
    n_sent = int(len(sent))
    if n_sent == 0:
        return WindowMetrics(start=start, end=end, packets_sent=0, packets_delivered=0)

    delivered = sent[_delivered_mask(sent, deliver_by)]
    n_del = int(len(delivered))
    duration = max(end - start, 1e-9)

    throughput = None
    mean_delay = None
    if n_del > 0:
        throughput = float(delivered["size_bytes"].sum()) * 8.0 / 1000.0 / duration
        delays = (delivered["receive_time"] - delivered["send_time"]) * 1000.0
        mean_delay = float(delays.mean())
    else:
        throughput = 0.0

    return WindowMetrics(
        start=start,
        end=end,
        packets_sent=n_sent,
        packets_delivered=n_del,
        pdr=n_del / n_sent,
        throughput_kbps=throughput,
        mean_delay_ms=mean_delay,
    )


def flow_table(packets: pd.DataFrame) -> dict[int, tuple[int, int]]:
    """Map ``flow_id -> (source, destination)`` from packets.csv."""
    if packets.empty:
        return {}
    first = packets.drop_duplicates("flow_id")[["flow_id", "source", "destination"]]
    return {int(r.flow_id): (int(r.source), int(r.destination)) for r in first.itertuples()}


def flow_stats(
    packets: pd.DataFrame,
    time: float,
    window_s: float,
) -> list[dict]:
    """Per-flow delivery over ``(time - window_s, time]``, without future knowledge.

    Returns one dict per flow that exists in packets.csv:
    ``{flow_id, source, destination, sent, delivered, pdr, mean_delay_ms}``.
    ``pdr`` / ``mean_delay_ms`` are ``None`` when they cannot be computed.
    """
    if packets.empty:
        return []
    flows = flow_table(packets)
    sent = _sent_in(packets, time - window_s, time)
    out: list[dict] = []
    for flow_id, (src, dst) in sorted(flows.items()):
        rows = sent[sent["flow_id"] == flow_id]
        n_sent = int(len(rows))
        delivered = rows[_delivered_mask(rows, time)] if n_sent else rows
        n_del = int(len(delivered))
        delay = None
        if n_del:
            delay = float(((delivered["receive_time"] - delivered["send_time"]) * 1000.0).mean())
        out.append(
            {
                "flow_id": flow_id,
                "source": src,
                "destination": dst,
                "sent": n_sent,
                "delivered": n_del,
                "pdr": (n_del / n_sent) if n_sent else None,
                "mean_delay_ms": delay,
            }
        )
    return out


def pct_change(before: float | None, after: float | None) -> float | None:
    """Relative change in percent, or ``None`` if it is undefined."""
    if before is None or after is None:
        return None
    if math.isclose(before, 0.0):
        return None
    return 100.0 * (after - before) / abs(before)
