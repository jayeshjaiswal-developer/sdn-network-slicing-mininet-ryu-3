# Network Slicing and Orchestration using Agentic AI in 5G/6G

MTech (Computer Science) thesis project, University of Hyderabad.
Guide: Prof. Satish Narayana Srirama.

The goal is an **autonomous, zero-touch network slicing system**: an AI agent
takes a high-level goal (an *intent*, e.g. "keep URLLC latency under 10 ms"),
watches the network, and creates, resizes or removes network slices on its own
to keep that goal met.

This repository is built step by step. **Step 1 (done)** is the foundation: a
software-defined network (SDN) with two isolated slices, each with its own
guaranteed bandwidth share.

---

## Step 1: Two-slice SDN testbed

```
    h1 (eMBB)  \                          / h3 (eMBB)
                s1 ====== 10 Mbps ====== s2
    h2 (URLLC) /     shared link          \ h4 (URLLC)
                         |
                  Ryu controller
              (decides who goes where)
```

| Slice | Hosts | Bandwidth share | Represents |
|---|---|---|---|
| eMBB | h1, h3 | up to 7 Mbps | High-speed traffic (video, downloads) |
| URLLC | h2, h4 | up to 3 Mbps | Low-latency traffic (robots, control) |

What it demonstrates:

1. **Bandwidth slicing:** each slice gets its own share of one shared link.
2. **Isolation:** hosts in different slices cannot talk to each other.
3. **Central control:** one controller program decides all of this; the
   switches just follow its rules.

New to SDN or slicing? Read **[docs/how-it-works.md](docs/how-it-works.md)**.
It explains every part in plain language, including a walk through the code.

---

## Requirements

- Ubuntu 22.04 or newer (a VM is fine; tested on Ubuntu 26.04 ARM in QEMU on
  an Apple Silicon Mac). Mininet needs Linux, so it does not run on macOS directly.
- About 4 GB RAM for this step.

## Setup (once)

```bash
bash setup.sh
```

This installs Mininet, Open vSwitch and iperf, and puts Ryu in its own
Python 3.9 environment at `~/ryu-env` (Ryu does not work on newer Python).
If the install fails, see [docs/troubleshooting.md](docs/troubleshooting.md).

## Run

**Terminal 1: start the controller (leave it running)**
```bash
source ~/ryu-env/bin/activate
ryu-manager src/slice_controller.py
```

**Terminal 2: start the network**
```bash
sudo python3 src/slice_topo.py
```

**At the `mininet>` prompt: test it**
```
h3 iperf -s &
h4 iperf -s &
h1 iperf -c 10.0.0.3 -t 10     # eMBB  -> about 7 Mbps
h2 iperf -c 10.0.0.4 -t 10     # URLLC -> about 3 Mbps
h1 ping -c 3 10.0.0.4          # cross-slice -> 100% packet loss
```

Type `exit` to stop. If something is left over from a crashed run, clean up
with `sudo mn -c`.

## Results (Step 1)

| Test | Expected | Measured |
|---|---|---|
| eMBB throughput (h1 → h3) | ~7 Mbps | 6.68 Mbps |
| URLLC throughput (h2 → h4) | ~3 Mbps | 2.86 Mbps |
| Cross-slice ping (h1 → h4) | 100% loss | 100% loss |

Measured values are about 95% of the limits, which is normal TCP and queueing
overhead. Full details and raw logs: [docs/step1-results.md](docs/step1-results.md)
and [results/step1/](results/step1/).

---

## Project structure

```
.
├── README.md                  you are here
├── setup.sh                   one-time installer
├── src/
│   ├── slice_topo.py          builds the virtual network + bandwidth queues
│   └── slice_controller.py    Ryu app: routing, slicing and isolation logic
├── docs/
│   ├── how-it-works.md        plain-language explanation of everything
│   ├── step1-results.md       what was tested and what it shows
│   └── troubleshooting.md     install problems and their fixes
└── results/step1/             raw logs from the test run
```

## Roadmap

| Step | What | Status |
|---|---|---|
| 1 | SDN testbed with static slices (Mininet + Ryu) | Done |
| 2 | REST API to create / resize / delete slices at runtime | Next |
| 3 | Live per-slice monitoring (throughput, latency, load) | Planned |
| 4 | 5G core with slices (Open5GS + UERANSIM) | Planned |
| 5 | Dynamic resource allocation: LP baseline + DRL (PPO) agent | Planned |
| 6 | Agentic AI orchestrator: LLM intents + DRL, closed loop (ZSM) | Planned |
| 7 | Experiments and evaluation | Planned |

## References

1. M. Liyanage et al., "A survey on Zero touch network and Service Management
   (ZSM) for 5G and beyond networks," *J. Network and Computer Applications*,
   vol. 203, 2022.
2. Y. Wei et al., "Intent-based networks for 6G: Insights and challenges,"
   *Digital Communications and Networks*, vol. 6, no. 3, 2020.
3. F. Bahramisirat, M. A. Gregory, S. Li, "Dynamic network slicing for
   resource allocation in 5G/B5G networks: An optimization-based approach,"
   *Ad Hoc Networks*, vol. 186, 2026.
