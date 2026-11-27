# SDN Network Slicing with Mininet and Ryu

> **Snapshot after Step 3 of 7:** slicing testbed + runtime REST API + live
> per-slice monitoring. Each step is also tagged (`step-1`, `step-2`, `step-3`).

MTech (Computer Science) thesis project, University of Hyderabad.
Guide: Prof. Satish Narayana Srirama.

An SDN testbed that splits one shared link into isolated network slices
(eMBB and URLLC), each with its own bandwidth, a **REST API to create,
resize and delete slices while the network runs**, and **live monitoring**
of every slice's throughput, latency and latency target. It is the
foundation for the thesis goal: an AI agent that manages slices on its own
(zero-touch).

| Step | What | Status |
|---|---|---|
| 1 | Two isolated slices with fixed bandwidth (Mininet + Ryu + OVS queues) | Done |
| 2 | REST API: create / resize / change hosts / delete slices at runtime | Done |
| 3 | Live per-slice monitoring: throughput, drops, latency, SLA check | **Done** |
| 4 | 5G core with slices (Open5GS + UERANSIM) | Planned |
| 5 | Dynamic resource allocation: LP baseline + DRL (PPO) agent | Planned |
| 6 | Agentic AI orchestrator: LLM intents + DRL, closed loop (ZSM) | Planned |
| 7 | Experiments and evaluation | Planned |

---

## How it works

```
                    slicectl / curl / record_metrics / (later) AI agent
                          │ change slices        ▲ read /metrics
                          ▼   REST API :8080     │
                 Ryu controller (slice table + monitor)
       OpenFlow rules │   queue rates │   ▲ queue counters every 2 s
                      ▼  (ovs-vsctl)  ▼   │
    h1 (eMBB)  \                                            / h3 (eMBB)
                s1 ============ 10 Mbps shared link ====== s2
    h2 (URLLC) /                                            \ h4 (URLLC)
                  └── latency prober: ping inside each slice ──┘
```

| Slice | Hosts | Starting bandwidth | Represents |
|---|---|---|---|
| eMBB | h1, h3 | 7 Mbps | High-speed traffic (video, downloads) |
| URLLC | h2, h4 | 3 Mbps, latency target 10 ms | Low-latency traffic (robots, control) |

- **Slicing:** each slice has its own queue on the shared link.
- **Isolation:** hosts in different slices cannot reach each other.
- **Live control:** the REST API changes slices without restarting anything,
  and refuses changes that would give out more bandwidth than the link has.
- **Live monitoring:** throughput, utilization, drops and latency per slice
  every 2 s, and whether each slice is meeting its latency target (SLA).

Plain-language guides:
- [docs/how-it-works.md](docs/how-it-works.md): SDN, slicing and packet flow (Step 1)
- [docs/step2-rest-api.md](docs/step2-rest-api.md): how live slice changes work (Step 2)
- [docs/step3-monitoring.md](docs/step3-monitoring.md): how slices are measured (Step 3)

---

## Requirements

- Ubuntu 22.04 or newer (a VM is fine; tested on Ubuntu 26.04 ARM in QEMU on
  an Apple Silicon Mac). Mininet needs Linux, so it does not run on macOS directly.
- About 4 GB RAM.

## Setup (once)

```bash
bash setup.sh
```

Installs Mininet, Open vSwitch and iperf, and puts Ryu in its own Python 3.9
environment at `~/ryu-env`. Problems: [docs/troubleshooting.md](docs/troubleshooting.md).

## Run

**First, clean up** any earlier run (do this *before* starting the
controller: `mn -c` also kills it)
```bash
sudo mn -c
```

**Terminal 1: controller** (sudo is needed so it can change switch queues)
```bash
sudo ~/ryu-env/bin/ryu-manager src/slice_controller.py
```

**Terminal 2: network**
```bash
sudo python3 src/slice_topo.py
```
Wait for `QoS queues configured on s1-eth3` and `s2-eth3` and
`Latency prober running`.

**Terminal 3: control the slices**
```bash
python3 scripts/slicectl.py list                         # show slices
python3 scripts/slicectl.py resize eMBB 5                # change bandwidth
python3 scripts/slicectl.py resize URLLC 5
python3 scripts/slicectl.py create mMTC 10.0.0.5 1       # new slice (fails: link full)
python3 scripts/slicectl.py hosts URLLC 10.0.0.2         # change members
python3 scripts/slicectl.py delete URLLC                 # remove a slice
python3 scripts/slicectl.py sla URLLC 10                 # latency target (ms)
```

**Terminal 4: watch the slices live**
```bash
python3 scripts/slicectl.py top
```

**Record an experiment and plot it** (plotting needs `sudo apt install python3-matplotlib`)
```bash
python3 scripts/record_metrics.py results/step3/run1.csv --duration 60
python3 scripts/plot_metrics.py results/step3/run1.csv --title "My experiment"
```

**At the `mininet>` prompt: measure**
```
h3 iperf -s &
h4 iperf -s &
h1 iperf -c 10.0.0.3 -t 10     # eMBB throughput
h2 iperf -c 10.0.0.4 -t 10     # URLLC throughput
h1 ping -c 3 10.0.0.4          # cross-slice: 100% loss
```

Type `exit` to stop. After a crash: `sudo mn -c`.

## REST API

| Method | Address | Body | Does |
|---|---|---|---|
| GET | `/slices` | | List slices + link status |
| GET | `/slices/{name}` | | One slice |
| POST | `/slices` | `{"name", "hosts", "bandwidth_mbps", "max_latency_ms"?}` | Create |
| PUT | `/slices/{name}` | any of `bandwidth_mbps`, `hosts`, `max_latency_ms` | Change |
| DELETE | `/slices/{name}` | | Delete |
| GET | `/link` | | Capacity, allocated, free |
| GET | `/metrics` | | Live metrics: throughput, utilization, drops, latency, SLA |
| GET | `/metrics/{name}?seconds=60` | | Recent samples of one slice |
| POST | `/metrics/latency` | `{"slice", "rtt_ms", "loss_pct"}` | Report a ping result (the prober does this) |

Example: `curl -s -X PUT localhost:8080/slices/URLLC -d '{"bandwidth_mbps": 5}'`

Rules: total bandwidth never exceeds the link (no over-provisioning), a host
is in at most one slice, and a failed change is rolled back. Details in
[docs/step2-rest-api.md](docs/step2-rest-api.md).

## Results

**Step 1 (VM, real network):**

| Test | Expected | Measured |
|---|---|---|
| eMBB throughput (h1 → h3) | ~7 Mbps | 6.68 Mbps |
| URLLC throughput (h2 → h4) | ~3 Mbps | 2.86 Mbps |
| Cross-slice ping (h1 → h4) | 100% loss | 100% loss |

**Step 2:** every API operation, the rules and the rollback were tested
against the real Ryu controller (switch simulated). See
[docs/step2-results.md](docs/step2-results.md).

**Step 3:** tested on a real Mininet network with the real controller (OVS
in userspace mode). The monitor read a saturated queue at 6.991–7.004 Mbps
under a 7 Mbps limit and 2.995–3.004 Mbps after a live resize to 3, and the
URLLC latency target flipped from `ok` to `violated` under load. The VM
experiments (resize under load, isolation, and the manual closed loop) are
in [docs/step3-results.md](docs/step3-results.md).

## Tests

```bash
~/ryu-env/bin/pip install pytest
~/ryu-env/bin/python -m pytest tests
```
37 tests; they use a fake `ovs-vsctl`, so they run without Mininet or root.

---

## Project structure

```
.
├── README.md
├── setup.sh                     one-time installer
├── config/
│   └── slices.json              starting slices, targets, link capacity, intervals
├── src/
│   ├── slice_topo.py            builds the virtual network
│   ├── slice_controller.py      Ryu app: forwarding, slicing, isolation, REST API
│   ├── slice_manager.py         slice table + rules (no networking code)
│   ├── slice_monitor.py         counters -> rates, latency, SLA check
│   ├── latency_prober.py        pings inside each slice, reports to controller
│   └── ovs_qos.py               creates / resizes / deletes switch queues
├── scripts/
│   ├── slicectl.py              command-line client (incl. live `top` view)
│   ├── record_metrics.py        saves live metrics to CSV
│   └── plot_metrics.py          CSV -> thesis figure (PNG)
├── tests/                       unit tests + fake ovs-vsctl
├── docs/
│   ├── how-it-works.md          SDN and slicing explained (Step 1)
│   ├── step2-rest-api.md        live slice control explained (Step 2)
│   ├── step3-monitoring.md      monitoring explained (Step 3)
│   ├── step1-results.md
│   ├── step2-results.md
│   ├── step3-results.md
│   └── troubleshooting.md
└── results/step1/               raw logs from the Step 1 run
```

## References

1. M. Liyanage et al., "A survey on Zero touch network and Service Management
   (ZSM) for 5G and beyond networks," *J. Network and Computer Applications*,
   vol. 203, 2022.
2. Y. Wei et al., "Intent-based networks for 6G: Insights and challenges,"
   *Digital Communications and Networks*, vol. 6, no. 3, 2020.
3. F. Bahramisirat, M. A. Gregory, S. Li, "Dynamic network slicing for
   resource allocation in 5G/B5G networks: An optimization-based approach,"
   *Ad Hoc Networks*, vol. 186, 2026.
