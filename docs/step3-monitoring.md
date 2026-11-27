# Step 3: Watching the slices live (plain-language guide)

After Step 2 the controller could **change** slices, but it was blind: it
had no idea whether a slice was busy, idle or failing its promise. An agent
can't fix what it can't see. Step 3 gives the system **eyes**.

The thesis loop is **Monitor → Analyze → Plan → Execute**. Step 2 built
*Execute* (the REST API). Step 3 builds **Monitor** (live measurements) and
the first bit of **Analyze** (is each slice meeting its target?). Plan, the
decision-making, is what the AI agent adds later.

---

## 1. What gets measured

| Metric | Meaning | Where it comes from |
|---|---|---|
| **Throughput** | How fast the slice is actually sending (Mbps) | Switch counters, every 2 s |
| **Utilization** | Throughput ÷ allocated bandwidth (100% = slice is full) | Calculated |
| **Drops/s** | Packets the slice's queue threw away because it was full | Switch counters |
| **Latency** | Round-trip time between two hosts of the slice (ms) | Ping, every 2 s |
| **Loss** | % of pings that got no reply | Ping |
| **SLA** | Is latency within the slice's target? `ok` / `violated` / `unknown` / `no-target` | Calculated |

Together these answer the questions the agent will ask: *Is this slice
using what it was given? Is it starving? Is it keeping its promise?*

---

## 2. The picture

```
                       slicectl top / record_metrics.py / (later) AI agent
                                      │  GET /metrics
                                      ▼
 ┌──────────────────────── Ryu controller ─────────────────────────┐
 │  every 2 s: "send me queue counters"      SliceMonitor          │
 │  ────────────────────────────────────►    ├ counters → rates    │
 │  ◄──── counters for queue 0, 1 ────────   ├ latency samples     │
 │                                           └ SLA check           │
 └────────────▲────────────────────────────────────────────────────┘
              │ POST /metrics/latency
 ┌────────────┴───────────── slice_topo.py ─────────────────────────┐
 │  Latency prober: every 2 s, ping inside each slice               │
 │      h1 ──ping──► h3   (eMBB)          h2 ──ping──► h4   (URLLC)  │
 └───────────────────────────────────────────────────────────────────┘
```

Two separate sources, because the switch can count bytes but **cannot**
measure how long a packet waited.

---

## 3. How throughput is measured

The switch keeps a **running total** for every queue: "queue 1 on port
s1-eth3 has sent 3,750,000 bytes so far". It never says "how fast".

Every 2 seconds the controller asks for those totals (an OpenFlow
*queue-stats request*). The monitor compares each total with the previous one:

```
rate = (bytes now − bytes 2 s ago) × 8 bits  ÷  2 s
     = (3,750,000 − 3,000,000) × 8 ÷ 2  =  3,000,000 bit/s  =  3 Mbps
```

Details that matter:

- **Two directions.** The shared link has a port on each switch. `s1-eth3`
  counts traffic leaving s1 toward s2 (h1 → h3), `s2-eth3` the other way.
  A download fills one direction and sends only small acknowledgements back,
  so the slice's headline throughput is the **busier** direction. Both are
  kept in `per_port`.
- **Finding the port.** OpenFlow uses port *numbers*, the config uses port
  *names* (`s1-eth3`). When a switch connects, the controller asks for its
  port list and remembers which number is the shared link.
- **Counters restarting.** If a total goes *down*, the queue was recreated
  and counting restarted at zero. That one interval is skipped instead of
  reporting a nonsense negative rate.
- **"No data" is not zero.** If there's no fresh sample (network not
  running, slice just created), the value is `null` (shown as `-`), never
  `0`. To an agent, "0 Mbps" means "idle"; "unknown" means "don't act on it".

---

## 4. How latency is measured

The **latency prober** runs inside `slice_topo.py` because it needs the
Mininet hosts. Every 2 seconds, for each slice, it picks the first two of
the slice's hosts that exist and runs:

```
h2 ping -c 3 -i 0.2 -W 1 10.0.0.4
```

The pings are ordinary IP packets between two hosts of the **same slice**,
so the controller puts them in **that slice's queue**. If the queue is
full, the pings wait behind the other packets, and the measured latency
rises. That's exactly what we want to catch: *a slice that is overloaded
gets slow*.

It sends each result to the controller with `POST /metrics/latency`. The
reported latency is the average of the last 3 measurements, which smooths
out single spikes.

The prober uses `host.popen` (a separate process) instead of `host.cmd`,
so it never interferes with what you type at the `mininet>` prompt.

---

## 5. SLA: the slice's promise

A slice can now have a **latency target**, `max_latency_ms`. In
`config/slices.json`, URLLC starts with 10 ms:

```json
{"name": "URLLC", "hosts": ["10.0.0.2", "10.0.0.4"], "bandwidth_mbps": 3, "max_latency_ms": 10}
```

| SLA value | Meaning |
|---|---|
| `ok` | Latency is within the target |
| `violated` | Latency is above the target, or every ping was lost |
| `unknown` | There is a target but no recent measurement |
| `no-target` | The slice has no latency target (eMBB by default) |

Change it at runtime: `slicectl sla URLLC 5`, or remove it with
`slicectl sla URLLC none`. In API terms that's
`PUT /slices/URLLC {"max_latency_ms": 5}`.

**Why this matters for the thesis:** an *intent* like "keep URLLC latency
under 10 ms" becomes a number the system can check every 2 seconds.
`violated` is the trigger the agent will react to.

---

## 6. Using it

| Want to… | Command |
|---|---|
| Watch everything live | `python3 scripts/slicectl.py top` |
| One snapshot | `python3 scripts/slicectl.py metrics` |
| Raw recent samples of one slice | `python3 scripts/slicectl.py history URLLC --seconds 60` |
| Set / remove a latency target | `slicectl sla URLLC 10` / `slicectl sla URLLC none` |
| Record an experiment | `python3 scripts/record_metrics.py results/step3/run1.csv` |
| Make a thesis figure from it | `python3 scripts/plot_metrics.py results/step3/run1.csv` |

What `slicectl top` looks like while eMBB is downloading (illustrative
numbers, to show the layout; your own run gives the real ones):

```
SLICE      ALLOC  THROUGHPUT    USE  DROPS/s   LATENCY   LOSS   TARGET  SLA
eMBB           7   6.65 Mbps    95%        3   48.10 ms     0%        -  no-target
URLLC          3   0.00 Mbps     0%        0    0.09 ms     0%    10 ms  ok

Link: 10 of 10 Mbps allocated
  s1-eth3: 6.70 Mbps in use (67% of the link)
  s2-eth3: 0.15 Mbps in use (2% of the link)
```

Read it like this: eMBB is using almost all of its share and its own
latency went up (its queue is full), but **URLLC's latency is unaffected**.
That's slice isolation, now visible in numbers.

### The figure

`plot_metrics.py` draws two panels on the same time axis: throughput (solid)
against allocation (dashed), and latency (solid) against the target
(dashed). A resize shows up as a step in the dashed line, and you can see
the solid line follow it.

---

## 7. API additions

| Method | Address | Body | Does |
|---|---|---|---|
| GET | `/metrics` | | Live metrics for every slice + per-port link use |
| GET | `/metrics/{name}?seconds=60` | | Recent samples for one slice |
| POST | `/metrics/latency` | `{"slice", "rtt_ms", "loss_pct"}` | Report a ping result (the prober does this) |
| POST/PUT | `/slices`, `/slices/{name}` | now also `"max_latency_ms"` | Set the latency target |

The shape of `GET /metrics` (one slice shown; illustrative numbers for an
overloaded URLLC slice):

```json
{
  "time": 1795786824.19,
  "poll_interval_s": 2.0,
  "slices": [
    {
      "name": "URLLC", "queue": 1,
      "allocated_mbps": 3.0, "throughput_mbps": 2.97, "utilization": 0.99,
      "drops_per_s": 41.0, "latency_ms": 38.2, "loss_pct": 0.0,
      "max_latency_ms": 10.0, "sla": "violated",
      "per_port": {"s1-eth3": {"mbps": 2.97, "pps": 260.5, "drops_ps": 41.0},
                   "s2-eth3": {"mbps": 0.0, "pps": 0.0, "drops_ps": 0.0}}
    }
  ],
  "link": {"capacity_mbps": 10.0, "allocated_mbps": 10.0, "free_mbps": 0.0,
           "per_port": {"s1-eth3": {"mbps": 9.6, "utilization": 0.96}}}
}
```

This JSON is what the agent will read every few seconds.

---

## 8. New files

| File | Role |
|---|---|
| `src/slice_monitor.py` | Counters → rates, latency averaging, SLA check, history. Pure Python, unit-tested |
| `src/latency_prober.py` | Pings inside each slice and reports to the controller |
| `scripts/record_metrics.py` | Saves `/metrics` to CSV every 2 s during an experiment |
| `scripts/plot_metrics.py` | Turns a CSV into a two-panel PNG figure |
| `tests/test_slice_monitor.py` | Tests for the monitor and the ping parser |

Changed: `slice_controller.py` (polling + endpoints), `slice_topo.py`
(starts the prober), `slice_manager.py` (latency target),
`slicectl.py` (`top`, `metrics`, `sla`, `history`), `config/slices.json`
(intervals + URLLC target).

---

## 9. Limits worth knowing (and writing in the thesis)

- **Ping latency is round-trip**, through both directions of the slice. It
  is an upper bound on one-way delay, which is fine for checking a target.
- **The prober adds a little traffic**: 3 small pings per slice every 2 s,
  far below 0.01 Mbps.
- **2-second resolution.** A burst shorter than 2 s is averaged out. That's
  the trade-off for not flooding the controller; change `poll_interval_s`
  and `probe_interval_s` in the config if needed.
- **Drops** are what Open vSwitch reports as the queue's transmit errors,
  which for an HTB queue are packets dropped because the queue was full.

---

## 10. Glossary (new terms)

| Term | Meaning |
|---|---|
| **Counter** | A running total kept by the switch (bytes, packets, drops) |
| **Polling** | Asking for data on a fixed schedule, here every 2 s |
| **Queue stats** | OpenFlow message carrying a queue's counters |
| **RTT** | Round-trip time: there and back, measured by ping |
| **SLA** | Service Level Agreement: the slice's promised quality, here a latency target |
| **Utilization** | How much of its allocation a slice is using |
| **MAPE-K** | Monitor, Analyze, Plan, Execute over shared Knowledge: the self-managing loop |
