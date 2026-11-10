#!/usr/bin/env python3
"""
A fake `ovs-vsctl` for tests, so the queue logic can be checked on any
machine without Open vSwitch. It keeps a tiny OVSDB (ports, QoS, queues) in
the JSON file named by $FAKE_OVS_STATE and supports only the commands that
ovs_qos.py uses.

State file: {"ports": {"s1-eth3": null}, "qos": {uuid: {"queues": {"0": uuid}}},
             "queue": {uuid: {"max_rate": 7000000}}}
"""
import json
import os
import sys
import uuid


def fail(msg):
    print(f"ovs-vsctl: {msg}", file=sys.stderr)
    sys.exit(1)


def main(argv):
    path = os.environ["FAKE_OVS_STATE"]
    # Simulate a permission error (e.g. controller not run with sudo)
    if os.path.exists(path + ".fail") and argv[:1] != ["port-to-br"]:
        fail("database connection failed (Permission denied)")
    with open(path) as f:
        db = json.load(f)

    # Split into commands on "--"; pull out per-command options
    cmds, cur = [], []
    for tok in argv:
        if tok == "--":
            if cur:
                cmds.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        cmds.append(cur)

    refs, out = {}, []
    parsed = []
    for c in cmds:
        opts = [t for t in c if t.startswith("--")]
        words = [t for t in c if not t.startswith("--")]
        parsed.append((opts, words))

    # Pass 1: create rows so @names can be used anywhere in the transaction
    for opts, words in parsed:
        if words and words[0] == "create":
            ident = next(o.split("=", 1)[1] for o in opts if o.startswith("--id="))
            u = str(uuid.uuid4())
            refs[ident] = u
            table, kv = words[1], dict(w.split("=", 1) for w in words[2:])
            if table == "queue":
                db["queue"][u] = {"max_rate": int(kv["other-config:max-rate"])}
            elif table == "qos":
                db["qos"][u] = {"queues": {}, "_queues_spec": kv.get("queues")}
            else:
                fail(f"unsupported table {table}")

    def ref(v):
        return refs.get(v, v)

    # Pass 2: everything else
    for opts, words in parsed:
        if not words:
            continue
        op = words[0]
        if op == "create":
            continue
        if op == "port-to-br":
            if words[1] not in db["ports"]:
                fail(f"no port named {words[1]}")
            out.append("br")
        elif op == "get":
            table, rec, col = words[1:4]
            if table == "port":
                if rec not in db["ports"]:
                    fail(f"no row \"{rec}\" in table Port")
                out.append(db["ports"][rec] or "[]")
            elif table == "qos":
                q = db["qos"].get(rec) or fail(f"no row {rec} in table QoS")
                out.append("{" + ", ".join(f"{k}={v}" for k, v in sorted(q["queues"].items(), key=lambda x: int(x[0]))) + "}")
        elif op == "set":
            table, rec = words[1], ref(words[2])
            for kv in words[3:]:
                k, v = kv.split("=", 1)
                if table == "port" and k == "qos":
                    if rec not in db["ports"]:
                        fail(f"no row \"{rec}\" in table Port")
                    db["ports"][rec] = ref(v)
                elif table == "queue" and k == "other-config:max-rate":
                    db["queue"][rec]["max_rate"] = int(v)
                elif table == "qos" and k.startswith("queues:"):
                    db["qos"][rec]["queues"][k.split(":", 1)[1]] = ref(v)
                else:
                    fail(f"unsupported set {table} {kv}")
        elif op == "remove":
            db["qos"][words[2]]["queues"].pop(words[4], None)
        elif op == "destroy":
            table = words[1]
            if "--all" in opts:
                db[table] = {}
                if table == "qos":
                    db["ports"] = {p: None for p in db["ports"]}
            else:
                db[table].pop(words[2], None)
        else:
            fail(f"unsupported command {op}")

    # Resolve "queues=0=@q0,1=@q1" given at QoS creation
    for q in db["qos"].values():
        spec = q.pop("_queues_spec", None)
        if spec:
            for item in spec.split(","):
                k, v = item.split("=", 1)
                q["queues"][k] = ref(v)

    with open(path, "w") as f:
        json.dump(db, f, indent=1)
    if out:
        print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1:])
