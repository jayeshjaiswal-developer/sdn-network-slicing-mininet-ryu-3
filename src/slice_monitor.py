"""
Slice monitor: turns raw switch counters into per-slice metrics.

Pure Python with no Ryu or OVS imports, so it can be unit-tested anywhere.

Inputs (fed by the controller and the latency prober):
    update_queue(port, queue, tx_bytes, tx_packets, dropped, now)
        Cumulative counters for one slice queue on one port of the shared
        link, as the switch reports them every poll.
    record_latency(slice, rtt_ms, loss_pct, now)
        One ping measurement between two hosts of a slice.

Outputs:
    snapshot(slices, now)   latest metrics for every slice (for GET /metrics)
    history(...)            recent samples for one slice (for graphs)

How a rate is computed: the switch only gives running totals ("2,000,000
bytes sent so far"). Two polls 2 s apart give the difference, and
difference / time = rate. If a total goes DOWN, the queue was recreated
(counters restarted at zero), so that one interval is skipped.
"""
import collections
import statistics

DEFAULT_HISTORY = 300       # samples kept per series (10 minutes at 2 s)
STALE_AFTER = 6.0           # a counter sample older than this is "no data"
LATENCY_STALE_AFTER = 15.0  # a ping result older than this is "no data"
LATENCY_WINDOW = 3          # latest pings averaged for the reported latency


class SliceMonitor:
    def __init__(self, history=DEFAULT_HISTORY):
        self.history_len = history
        self._last = {}       # (port, queue) -> (now, tx_bytes, tx_packets, dropped)
        self._rates = {}      # (port, queue) -> deque of samples
        self._latency = {}    # slice name -> deque of samples

    # ---------------- inputs ----------------

    def update_queue(self, port, queue, tx_bytes, tx_packets, dropped, now):
        """Feed one poll's cumulative counters. Returns the new rate sample or None."""
        key = (port, queue)
        prev = self._last.get(key)
        self._last[key] = (now, tx_bytes, tx_packets, dropped)
        if prev is None:
            return None                           # first poll: nothing to compare yet
        t0, b0, p0, d0 = prev
        dt = now - t0
        if dt <= 0 or tx_bytes < b0 or tx_packets < p0 or dropped < d0:
            return None                           # clock problem, or counters restarted
        sample = {
            "t": now,
            "mbps": (tx_bytes - b0) * 8 / dt / 1e6,
            "pps": (tx_packets - p0) / dt,
            "drops_ps": (dropped - d0) / dt,
        }
        self._rates.setdefault(key, collections.deque(maxlen=self.history_len)).append(sample)
        return sample

    def record_latency(self, slice_name, rtt_ms, loss_pct, now):
        sample = {"t": now, "rtt_ms": rtt_ms, "loss_pct": loss_pct}
        self._latency.setdefault(slice_name, collections.deque(maxlen=self.history_len)).append(sample)
        return sample

    def forget_queue(self, queue):
        """Drop all data for a queue id (its slice was deleted; the id may be reused)."""
        for key in [k for k in self._last if k[1] == queue]:
            self._last.pop(key, None)
            self._rates.pop(key, None)

    def forget_slice(self, slice_name):
        self._latency.pop(slice_name, None)

    def ports(self):
        return sorted({p for p, _ in self._last})

    # ---------------- outputs ----------------

    def snapshot(self, slices, now):
        """Latest metrics for each slice.

        `slices` is SliceManager.list(). Throughput is per port of the shared
        link (one port per direction); the slice's headline throughput is the
        busier direction, since a download fills one direction and only sends
        small acknowledgements back the other way.
        """
        result = []
        for s in slices:
            per_port = {}
            for (port, queue), series in self._rates.items():
                if queue == s["queue"] and series and now - series[-1]["t"] <= STALE_AFTER:
                    last = series[-1]
                    per_port[port] = {"mbps": round(last["mbps"], 3),
                                      "pps": round(last["pps"], 1),
                                      "drops_ps": round(last["drops_ps"], 1)}
            throughput = max((v["mbps"] for v in per_port.values()), default=None)
            drops = sum(v["drops_ps"] for v in per_port.values()) if per_port else None
            allocated = s["bandwidth_mbps"]
            utilization = (round(throughput / allocated, 3)
                           if throughput is not None and allocated > 0 else None)

            latency, loss = self._recent_latency(s["name"], now)
            target = s.get("max_latency_ms")
            result.append({
                "name": s["name"],
                "queue": s["queue"],
                "allocated_mbps": allocated,
                "throughput_mbps": throughput,
                "utilization": utilization,
                "drops_per_s": None if drops is None else round(drops, 1),
                "latency_ms": latency,
                "loss_pct": loss,
                "max_latency_ms": target,
                "sla": self._sla_status(target, latency, loss),
                "per_port": per_port,
            })
        return result

    def link_snapshot(self, capacity_mbps, now):
        """Total traffic per port of the shared link (all slices together)."""
        totals = {}
        for (port, _q), series in self._rates.items():
            if series and now - series[-1]["t"] <= STALE_AFTER:
                totals[port] = totals.get(port, 0.0) + series[-1]["mbps"]
        return {port: {"mbps": round(v, 3), "utilization": round(v / capacity_mbps, 3)}
                for port, v in sorted(totals.items())}

    def history(self, slice_name, queue, since):
        """Samples newer than `since` for one slice: rates per port + latency."""
        rates = {}
        for (port, q), series in self._rates.items():
            if q == queue:
                rates[port] = [dict(x, mbps=round(x["mbps"], 3)) for x in series if x["t"] >= since]
        latency = [x for x in self._latency.get(slice_name, ()) if x["t"] >= since]
        return {"rates": rates, "latency": latency}

    # ---------------- helpers ----------------

    def _recent_latency(self, slice_name, now):
        samples = [x for x in self._latency.get(slice_name, ())
                   if now - x["t"] <= LATENCY_STALE_AFTER][-LATENCY_WINDOW:]
        if not samples:
            return None, None
        rtts = [x["rtt_ms"] for x in samples if x["rtt_ms"] is not None]
        latency = round(statistics.mean(rtts), 3) if rtts else None
        loss = round(statistics.mean(x["loss_pct"] for x in samples), 1)
        return latency, loss

    @staticmethod
    def _sla_status(target, latency, loss):
        """'ok', 'violated', 'no-target' or 'unknown' (target set, no data yet)."""
        if target is None:
            return "no-target"
        if loss is not None and loss >= 100:
            return "violated"                     # nothing gets through at all
        if latency is None:
            return "unknown"
        return "ok" if latency <= target else "violated"
