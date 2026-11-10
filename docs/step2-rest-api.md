# Step 2: Changing slices while the network runs (plain-language guide)

In Step 1 the slices were **written into the code**. Changing a slice meant
stopping everything, editing a file and restarting. A zero-touch system can't
work like that: the AI agent must be able to change slices **live**.

Step 2 adds a **REST API** to the controller: a set of web addresses that
other programs call to read or change the slices. Today you call them with
`slicectl`; in later steps the AI agent calls exactly the same addresses.

---

## 1. What changed, in one picture

```
   You / AI agent                         Controller (Ryu)                    Switches (OVS)
 ┌──────────────┐  HTTP :8080   ┌──────────────────────────────┐
 │ slicectl.py  │ ────────────► │ REST API (SliceRestApi)      │
 │ curl         │               │        │                     │
 │ (later: LLM) │ ◄──────────── │        ▼                     │
 └──────────────┘   JSON reply  │ SliceManager (slice table +  │
                                │   rules: no overlap, no      │
                                │   over-provisioning)         │
                                │        │                     │  ovs-vsctl   ┌──────────┐
                                │        ├── queue rates ──────┼────────────► │ queues   │
                                │        │                     │  OpenFlow    │          │
                                │        └── flush old rules ──┼────────────► │ flow     │
                                └──────────────────────────────┘              │ rules    │
                                                                              └──────────┘
```

Two different "wires" go to the switch, and that's the key idea of this step:

| What to change | How | Why this way |
|---|---|---|
| **Bandwidth of a queue** | `ovs-vsctl` (switch configuration database) | OpenFlow can only say *"use queue 1"*; it cannot set how fast queue 1 is |
| **Which traffic goes in which queue** | OpenFlow flow rules | That's what OpenFlow is for |

---

## 2. The new files

| File | Role |
|---|---|
| `config/slices.json` | Starting slices + link capacity + which ports form the shared link |
| `src/slice_manager.py` | The **slice table** and its rules. Pure Python, no networking, so it's easy to test |
| `src/ovs_qos.py` | Talks to the switch with `ovs-vsctl` to create, resize and delete queues |
| `src/slice_controller.py` | The Ryu app from Step 1, now with the REST API and live updates |
| `scripts/slicectl.py` | A friendly command-line client for the API |
| `tests/` | 22 automatic tests + a fake `ovs-vsctl` so they run without a real switch |

Splitting the logic into `slice_manager.py` matters for later: the
reinforcement-learning simulator in Phase 3 can reuse the same rules without
running Mininet.

---

## 3. The rules the controller enforces

Every request is checked **before** anything changes. A bad request changes
nothing and returns a clear error.

| Rule | Example of a rejected request | Reply |
|---|---|---|
| Total bandwidth ≤ link capacity (**no over-provisioning**) | URLLC to 6 Mbps while eMBB has 7 of 10 | `409 not enough capacity: ... only 3 Mbps free` |
| A host is in at most one slice | New slice containing 10.0.0.1 (already eMBB) | `409 host(s) 10.0.0.1 already in slice 'eMBB'` |
| Names are unique | Creating `eMBB` twice | `409 slice 'eMBB' already exists` |
| Inputs make sense | bandwidth `0`, IP `abc` | `400 ...` |
| Slice must exist | Deleting `xyz` | `404 slice 'xyz' does not exist` |

The "no over-provisioning" rule comes straight from your thesis objectives:
*"high-priority slices receive resources without over-provisioning."* To give
URLLC more, something else must give some up, and in later steps that's the
agent's decision to make.

---

## 4. What happens for each kind of change

### Resize (most common, what the agent will do most)
`slicectl resize URLLC 5`
1. SliceManager checks capacity (is there 5 Mbps free once URLLC's own 3 is counted?).
2. `ovs_qos` changes the queue's rate **in place** on both ends of the shared link.
3. **Flow rules are not touched**: traffic keeps flowing through the same
   queue, which just got faster. No interruption.

### Create
`slicectl create mMTC 10.0.0.5,10.0.0.6 1`
1. Checks: unique name, hosts not used elsewhere, 1 Mbps free.
2. Gets the lowest free queue number (here 2).
3. Adds queue 2 to the switches.
4. Flushes IP flow rules so traffic gets re-classified (see below).

### Change hosts (move a host between slices)
`slicectl hosts eMBB 10.0.0.1,10.0.0.3,10.0.0.4`
1. Checks the new hosts aren't in another slice.
2. Flushes IP flow rules.

### Delete
`slicectl delete mMTC`
1. Removes the slice, detaches and destroys its queue.
2. Flushes IP flow rules (its hosts are now in no slice, so they get dropped).

### Why "flush flow rules"?
Switches remember decisions (Step 1, section 4). If h4 moves from URLLC to
eMBB, the switch still has an old rule saying *"h1 → h4: drop"*. Flushing
deletes all IP rules (the "ask the controller" default rule stays), so the
next packet of every flow goes back to the controller and gets classified
with the **new** slice table. The cost is one extra round trip to the
controller for the first packet of each flow.

### What if updating the switch fails?
The controller saves the slice table before the change. If `ovs-vsctl` fails
(most often because the controller wasn't started with `sudo`), it puts the
old table back, re-applies it to the switch, and replies `500` with the
reason. The controller and the switches never disagree.

---

## 5. Who creates the queues now?

In Step 1, `slice_topo.py` created the queues. Now **the controller owns
them**: whenever a switch connects, the controller pushes the queues from its
slice table. `slice_topo.py` only builds the network and waits until the
queues appear (and warns you if they don't).

Why: if you change slices through the API and then restart Mininet, the
network comes back with your **current** slices, not the defaults from the
config file. One owner, no conflicts.

This is also why the controller now needs `sudo`: changing switch
configuration requires root.

---

## 6. API reference

Base address: `http://127.0.0.1:8080`. All bodies are JSON.

| Method | Address | Body | Does |
|---|---|---|---|
| GET | `/slices` | | List slices + link status |
| GET | `/slices/{name}` | | One slice |
| POST | `/slices` | `{"name": "mMTC", "hosts": ["10.0.0.5"], "bandwidth_mbps": 1}` | Create |
| PUT | `/slices/{name}` | `{"bandwidth_mbps": 5}` and/or `{"hosts": [...]}` | Change |
| DELETE | `/slices/{name}` | | Delete |
| GET | `/link` | | Capacity, allocated, free |

The same with `curl`:
```bash
curl -s localhost:8080/slices
curl -s -X PUT localhost:8080/slices/URLLC -d '{"bandwidth_mbps": 5}'
curl -s -X POST localhost:8080/slices -d '{"name":"mMTC","hosts":["10.0.0.5"],"bandwidth_mbps":1}'
curl -s -X DELETE localhost:8080/slices/mMTC
```

Example reply from `GET /slices`:
```json
{
  "slices": [
    {"name": "eMBB",  "queue": 0, "hosts": ["10.0.0.1", "10.0.0.3"], "bandwidth_mbps": 7.0},
    {"name": "URLLC", "queue": 1, "hosts": ["10.0.0.2", "10.0.0.4"], "bandwidth_mbps": 3.0}
  ],
  "link": {"capacity_mbps": 10.0, "allocated_mbps": 10.0, "free_mbps": 0.0}
}
```

---

## 7. How this connects to the agent (later)

The four operations here (**create, resize, change hosts, delete**) are
exactly the **tools** the agentic AI will be given in Phase 4. For example,
the intent *"URLLC latency is too high, fix it"* will make the agent:

1. `GET /slices` → see that the link is full.
2. Decide: take 2 Mbps from eMBB.
3. `PUT /slices/eMBB {"bandwidth_mbps": 5}`
4. `PUT /slices/URLLC {"bandwidth_mbps": 5}`
5. Measure again (monitoring comes in Step 3).

Note the order in steps 3–4: free bandwidth first, then use it. The
no-over-provisioning rule makes the reverse order fail, and that's a good
thing: the network can never be promised more than it has.

---

## 8. Glossary (new terms)

| Term | Meaning |
|---|---|
| **REST API** | Programs talk to a server by calling web addresses with GET/POST/PUT/DELETE |
| **JSON** | The text format for the data sent and received (`{"key": value}`) |
| **HTTP status** | 200 OK, 201 created, 400 bad input, 404 not found, 409 conflicts with a rule, 500 server/switch problem |
| **OVSDB** | Open vSwitch's configuration database (ports, QoS, queues); changed with `ovs-vsctl` |
| **Flush** | Delete flow rules so the switch asks the controller again |
| **Rollback** | Undo a change that couldn't be completed |
