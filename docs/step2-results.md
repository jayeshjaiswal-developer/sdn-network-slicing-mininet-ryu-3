# Step 2 results

## Tested here (development machine, no Mininet)

Real Ryu 4.34 controller on Python 3.9, with a fake `ovs-vsctl`
(`tests/fake_ovs_vsctl.py`) standing in for Open vSwitch.

| Test | Result |
|---|---|
| 22 unit tests (`pytest tests`) | All pass |
| List / show / link status | Correct |
| Resize eMBB 7 → 5, URLLC 3 → 4 | Queues on both ports updated in place; no new queue rows |
| Resize URLLC to 6 with 5 free | Rejected `409 not enough capacity`; nothing changed |
| Create mMTC (1 Mbps, queue 2) | Queue 2 added on both ports |
| Create a slice with a host already in eMBB | Rejected `409` |
| Move 10.0.0.4 from URLLC to eMBB | Hosts updated; flow flush triggered |
| Delete mMTC | Queue 2 detached and destroyed on both ports |
| Invalid JSON / empty change | Rejected `400` with a clear message |
| Switch update fails (simulated permission error) | `500` with a sudo hint; slice table rolled back |
| Change while network is not running | Saved; queues pushed when switches connect |

Bug found and fixed during this testing: a REST handler named `link` clashed
with an attribute of Ryu's `ControllerBase`, making `/link` return 500. It was
renamed to `get_link_status`.

## To run on the VM (real network)

Start the controller with sudo and the network as in the README, then:

- [ ] **Resize under load:** start a long eMBB transfer
  (`h3 iperf -s &` then `h1 iperf -c 10.0.0.3 -t 40 -i 2`), and while it runs:
  `python3 scripts/slicectl.py resize URLLC 6` is rejected,
  then `resize eMBB 3`. The 2-second iperf reports should drop from ~7 to ~3 Mbps
  **without the transfer stopping**.
- [ ] **Give the freed bandwidth to URLLC:** `resize URLLC 7`, then
  `h4 iperf -s &` and `h2 iperf -c 10.0.0.4 -t 10` → about 7 Mbps.
- [ ] **Move a host:** `hosts URLLC 10.0.0.2` then
  `hosts eMBB 10.0.0.1,10.0.0.3,10.0.0.4`; `h1 ping -c 3 10.0.0.4` now
  **succeeds** (it failed in Step 1).
- [ ] **Delete:** `delete URLLC`; `h2 ping -c 3 10.0.0.4` fails (h2 is in no slice).
- [ ] Save both terminal outputs to `results/step2/`.

The resize-under-load run is the key result for the thesis: it shows a slice's
resources changing live, with no restart and no traffic interruption.
