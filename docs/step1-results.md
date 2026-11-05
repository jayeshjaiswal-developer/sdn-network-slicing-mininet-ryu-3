# Step 1 results

**Date:** 5 November 2026
**Environment:** Ubuntu 26.04 (ARM64) VM in QEMU on Apple Silicon, Ryu 4.34,
Python 3.9 for Ryu, Open vSwitch, Mininet.

## Setup

- Shared link s1 ↔ s2 capped at 10 Mbps using HTB QoS.
- Queue 0 (eMBB): max 7 Mbps. Queue 1 (URLLC): max 3 Mbps.
- Each test ran separately (one slice at a time), 10 seconds, TCP iperf.

## Results

| Test | Expected | Measured | Pass |
|---|---|---|---|
| eMBB throughput, h1 → h3 | ~7 Mbps | 6.68 Mbps (8.38 MB in 10.52 s) | Yes |
| URLLC throughput, h2 → h4 | ~3 Mbps | 2.86 Mbps (3.63 MB in 10.62 s) | Yes |
| Cross-slice ping, h1 → h4 | 100% loss | 3 sent, 0 received, 100% loss | Yes |

## What this shows

1. **Bandwidth slicing works.** Each slice reached about 95% of its configured
   cap. The missing ~5% is protocol overhead (headers, TCP ACKs), which the
   queue counts but iperf does not.
2. **Isolation works.** The controller detected that h1 (eMBB) and h4 (URLLC)
   are in different slices and installed a drop rule.
3. **Correct classification.** The controller log shows every eMBB flow mapped
   to queue 0 and every URLLC flow to queue 1, on both switches and in both
   directions.

## Limitations (to address in later steps)

- Slices are **static**: changing them means editing code and restarting.
- Slices were tested **one at a time**; a contention test (both at once) is
  still to be recorded.
- **No latency** measurement yet for URLLC.
- Caps are **max rates**, not guarantees: an idle slice's unused bandwidth is
  not lent to the other slice. Dynamic allocation (Step 5) will fix this.

## Still to record

- [ ] Contention test: `h1 iperf -c 10.0.0.3 -t 20 &` then `h2 iperf -c 10.0.0.4 -t 20`
- [ ] URLLC latency baseline: `h2 ping -c 10 10.0.0.4`
- [ ] Flow tables: `sudo ovs-ofctl -O OpenFlow13 dump-flows s1`

Raw logs: [`results/step1/`](../results/step1/).
