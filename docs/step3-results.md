# Step 3 results

## Tested here (development machine)

**37 unit tests** pass (`pytest tests`), covering rate calculation,
counter restarts, stale data, latency averaging, every SLA state, ping
parsing, the latency-target API and the Step 1–2 behaviour.

**End-to-end run on a real Mininet network**, with the real Ryu controller,
`slice_topo.py`, the latency prober and `record_metrics.py`. One difference
from your VM: this machine has no Open vSwitch kernel module, so the
switches ran in OVS's **userspace** mode. In that mode the HTB queue limit
works, but OVS ignores the "put this flow in queue N" action, so **both
slices' traffic went into queue 0** (eMBB). Per-slice separation was
already shown on the VM in Step 1 (kernel mode).

Scenario: iperf h1 → h3 and h2 → h4 from about t = 7 s; at t ≈ 22 s,
`PUT /slices/eMBB {"bandwidth_mbps": 3}`. Figures below are from the
recorded CSV of the final run.

| Check | Result |
|---|---|
| Controller finds the shared-link port numbers | Yes, on both switches |
| Queue counters polled every 2 s, turned into rates | Yes |
| Rate accuracy | Saturated queue 0 read 6.991–7.004 Mbps at its 7 Mbps limit, then 2.995–3.004 Mbps after the resize to 3 |
| Resize visible live in the metrics | Yes, within one poll (2 s) |
| Latency prober posts results for both slices | Yes, every ~2.5 s |
| Latency at rest | 1.8–4.6 ms |
| Latency with the queue saturated | 32–165 ms |
| SLA flag | URLLC (10 ms target): `unknown` before the first ping, `ok` at rest, `violated` from the first sample under load |
| CSV recorder | 23 samples × 2 slices over 46 s, no gaps |
| Plot script | Two panels render correctly; overlapping end labels are spread apart |
| `slicectl top` / `metrics` / `history` / `sla` | Correct output |
| Bad input to the new endpoints | `400` / `404` with clear messages |

Problems found and fixed during testing:

1. Line labels in the plot overlapped when two slices ended at the same
   value; labels are now pushed apart.
2. `sudo mn -c` also kills the Ryu controller (its cleanup kills every
   process with "controller" in its command line). The run order in the
   README now says: clean up first, then start the controller.

## To run on the VM (kernel switch, real per-slice queues)

Start in this order: `sudo mn -c`, then the controller, then the network.
Open a third terminal for `slicectl top` and a fourth for the recorder.

### E1: Step 2 test still open: resize under load
- [ ] Record: `python3 scripts/record_metrics.py results/step3/e1_resize.csv --duration 50`
- [ ] Mininet: `h3 iperf -s &` then `h1 iperf -c 10.0.0.3 -t 40 -i 2`
- [ ] At about 15 s: `python3 scripts/slicectl.py resize eMBB 3`
- [ ] Expected: eMBB drops from ~7 to ~3 Mbps **without the transfer stopping**
- [ ] Plot: `python3 scripts/plot_metrics.py results/step3/e1_resize.csv --title "eMBB resized from 7 to 3 Mbps under load"`

### E2: Isolation: a full eMBB slice doesn't slow URLLC
- [ ] Record: `... results/step3/e2_isolation.csv --duration 40`
- [ ] Mininet: `h3 iperf -s &` then `h1 iperf -c 10.0.0.3 -t 30`
- [ ] Expected: eMBB at ~100% use and its own latency rises; **URLLC latency
      stays low and its SLA stays `ok`**

### E3: The manual closed loop (what the agent will automate)
- [ ] Record: `... results/step3/e3_sla.csv --duration 70`
- [ ] Mininet: `h4 iperf -s -u &` then `h2 iperf -c 10.0.0.4 -u -b 5M -t 60`
      (URLLC is offered 5 Mbps of UDP but has only 3)
- [ ] Expected: URLLC at ~100% use, drops > 0, latency above 10 ms,
      `slicectl top` shows **VIOLATED**
- [ ] At about 25 s, act like the agent would: `slicectl resize eMBB 4`
      then `slicectl resize URLLC 6`
- [ ] Expected: URLLC drops fall to 0, latency comes back under 10 ms,
      SLA returns to `ok`

E3 is the key thesis figure for this step: the system **detects** a broken
promise, a change through the API **fixes** it, and the monitor **confirms**
the fix. Phase 5–6 replace "you typing the resize" with the agent.

Save the CSVs and PNGs in `results/step3/` and commit them.
