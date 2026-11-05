# How Step 1 works (plain-language guide)

This guide explains what happens when you run the testbed, from the big ideas
down to individual lines of code. No prior SDN knowledge needed.

---

## 1. The big ideas

### Network slicing
Imagine one highway shared by ambulances and delivery trucks. If they share
every lane, a traffic jam delays the ambulance. **Slicing** paints separate
lanes on the same road: each kind of traffic gets its own lane with its own
rules, even though there's only one physical road.

In 5G, the standard slice types are:

| Slice | Needs | Example |
|---|---|---|
| **eMBB** (enhanced Mobile Broadband) | Lots of bandwidth | 4K video, downloads |
| **URLLC** (Ultra-Reliable Low-Latency) | Very low, predictable delay | Factory robots, remote surgery |
| **mMTC** (massive Machine-Type) | Many small devices | IoT sensors |

Step 1 builds the first two.

### SDN (Software-Defined Networking)
In a normal network, every switch decides on its own where to send packets.
In SDN, the switches become **simple workers** and one central program, the
**controller**, is the **brain**:

- The **switch** has a table of rules: "if a packet looks like X, do Y."
- If a packet matches no rule, the switch **asks the controller** what to do.
- The controller decides, sends back a **new rule**, and the switch remembers
  it. Later packets of the same kind are handled by the switch directly,
  without asking again.

The language between switch and controller is called **OpenFlow**. This is
what makes slicing programmable: change the controller's code, and the whole
network behaves differently. Later, our AI agent will be the one changing it.

---

## 2. The tools

| Tool | Role in our setup | Real-world equivalent |
|---|---|---|
| **Mininet** | Creates fake hosts, switches and cables inside one Linux machine | The physical network |
| **Open vSwitch (OVS)** | The software switches (s1, s2) Mininet creates | Hardware switches |
| **Ryu** | The SDN controller framework; our Python code runs on it | The network's brain |
| **HTB queues** (Linux traffic control) | Split a link's bandwidth into reserved shares | The painted lanes |
| **iperf** | Sends as much data as possible and measures speed | A speed test |

---

## 3. The network we build

```
    h1 (eMBB)  \                          / h3 (eMBB)
                s1 ====== 10 Mbps ====== s2
    h2 (URLLC) /     shared link          \ h4 (URLLC)
```

- 4 hosts (think: 4 computers), 2 switches, 1 shared link between switches.
- The shared link is where slicing matters: both slices must cross it.
- On that link we create two **queues** (lanes):
  - Queue 0 → eMBB, capped at **7 Mbps**
  - Queue 1 → URLLC, capped at **3 Mbps**

---

## 4. What happens when you run it

### Startup
1. **You start Ryu** (`ryu-manager slice_controller.py`). It waits for
   switches to connect on port 6633.
2. **You start Mininet** (`slice_topo.py`). It creates the hosts, switches
   and links, then tells the switches: "your controller is at 127.0.0.1:6633."
3. **Switches connect.** You see `Switch 1 connected`, `Switch 2 connected`.
   The controller gives each switch one default rule: *"if you don't know what
   to do with a packet, send it to me."*
4. **Mininet creates the queues** on the shared link ports (`s1-eth3`,
   `s2-eth3`). You see `QoS queues configured`.

### A packet's journey: h1 sends to h3 (same slice)
1. h1 sends a packet. It arrives at switch s1.
2. s1 has no rule for it, so it sends it to the controller (a **packet-in**).
3. The controller looks at the source and destination IPs (10.0.0.1 →
   10.0.0.3) and checks its slice table: **both are in eMBB.**
4. It installs a rule on s1: *"packets from 10.0.0.1 to 10.0.0.3 → put in
   queue 0, send out the right port."* You see
   `s1 eMBB: 10.0.0.1 -> 10.0.0.3 via queue 0`.
5. The packet travels through queue 0 on the shared link (max 7 Mbps), reaches
   s2, and the same thing happens there. Then it arrives at h3.
6. All following packets match the rule directly in the switch. The controller
   is no longer involved, which is why the network stays fast.

### A packet's journey: h1 sends to h4 (different slices)
1. The packet reaches s1 and goes to the controller.
2. 10.0.0.1 is eMBB, 10.0.0.4 is URLLC: **no slice contains both.**
3. The controller installs a **drop rule**: a rule with no actions, so the
   switch silently throws matching packets away. You see
   `s1 DROP 10.0.0.1 -> 10.0.0.4 (cross-slice)`.
4. The ping gets no reply: 100% packet loss. **That's isolation working.**

### Why the speeds are 6.68 and 2.86, not exactly 7 and 3
The queue limits count every byte on the wire, including packet headers and
TCP acknowledgements. iperf only counts your actual data. So you always measure
about 5% less than the limit. That's expected.

---

## 5. Code walkthrough

### `src/slice_topo.py`: builds the network

| Part | What it does |
|---|---|
| `SLICE_QUEUES` | The bandwidth for each queue: `{0: 7 Mbps, 1: 3 Mbps}` |
| `SliceTopo.build()` | Adds 4 hosts, 2 switches, 5 links. Link order matters: hosts get switch ports 1 and 2, so the shared link is always port 3 (`s1-eth3`) |
| `configure_queues(port)` | Runs one `ovs-vsctl` command that attaches an HTB QoS with two queues to a switch port |
| `clear_queues()` | Deletes the queues on exit so the next run starts clean |
| `main()` | Starts Mininet with a **remote** controller (our Ryu app), adds queues, opens the `mininet>` prompt |

### `src/slice_controller.py`: the brain

**The slice table**, the single most important piece:
```python
SLICES = {
    "eMBB":  (0, {"10.0.0.1", "10.0.0.3"}),   # queue 0, these hosts
    "URLLC": (1, {"10.0.0.2", "10.0.0.4"}),   # queue 1, these hosts
}
```
Right now it's fixed in the code. **In Step 2 the AI agent will change it
while the network runs.** That's the link between this step and the thesis.

**The functions:**

| Function | When it runs | What it does |
|---|---|---|
| `find_slice(src, dst)` | Every new flow | Returns the slice both IPs belong to, or `None` if they're in different slices |
| `on_switch_connect` | Once per switch at startup | Installs the "ask the controller" default rule (priority 0, lowest) |
| `add_flow(...)` | Whenever a rule is needed | Sends an OpenFlow `FlowMod` message to install a rule on a switch |
| `on_packet_in` | Each packet with no matching rule | The main logic, below |

**Inside `on_packet_in`, step by step:**
1. **Learn:** remember which port the sender's MAC address came from
   (`mac_to_port`). This is how a normal switch learns where hosts are.
2. **Decide output port:** if we know where the destination is, use that
   port; otherwise **flood** (send everywhere). Flooding is needed for the
   first ARP "who has this IP?" messages.
3. **If it's an IP packet with a known destination:**
   - Same slice → rule with `SetQueue(queue_id)` + `Output(port)`, priority 10.
   - Different slices → rule with no actions (drop), priority 20.
4. **Send this first packet on** (`PacketOut`), since it was held by the
   controller while the rule was being made.

**Rule details worth knowing:**
- **Priority:** higher wins. Drop (20) > slice rule (10) > default (0).
- **`idle_timeout=30`:** a rule disappears after 30 seconds without traffic.
  This keeps switch tables small and lets changes to `SLICES` take effect.

---

## 6. Useful commands for exploring

Run these in a normal terminal (not the Mininet prompt) while the network is up:

```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s1   # see the rules on s1
sudo ovs-vsctl list queue                    # see the queues and their rates
sudo ovs-vsctl list qos                      # see the QoS attached to ports
```

`dump-flows` is a great way to *see* the controller's decisions: you'll find
the `set_queue:0` rules for eMBB, `set_queue:1` for URLLC, and the drop rules
(`actions=drop`).

---

## 7. Glossary

| Term | Meaning |
|---|---|
| **Flow rule** | A "match → action" instruction stored in a switch |
| **Packet-in** | A switch asking the controller what to do with a packet |
| **FlowMod** | The controller telling a switch to add or change a rule |
| **PacketOut** | The controller telling a switch to send one specific packet |
| **Datapath (dp)** | Ryu's name for a connected switch; `dp.id` is its number |
| **HTB** | Hierarchical Token Bucket: the Linux mechanism that rate-limits queues |
| **QoS** | Quality of Service: giving traffic different treatment |
| **Flood** | Send a packet out of every port except the one it came in on |
