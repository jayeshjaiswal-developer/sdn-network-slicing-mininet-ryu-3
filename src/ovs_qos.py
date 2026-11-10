"""
Programs bandwidth queues on Open vSwitch ports with `ovs-vsctl`.

OpenFlow can only say "put this packet in queue N"; the queues themselves
(and their rates) live in the switch's OVSDB configuration. This module keeps
that configuration in sync with the slice table.

`apply_queues(port, capacity_bps, {queue_id: rate_bps})` makes the port's
queues match exactly:
    - existing queue  -> rate updated in place (no traffic interruption)
    - new queue       -> created and attached
    - removed queue   -> detached and destroyed

Needs permission to talk to OVS, so the controller must run with sudo.
The controller owns the queues; slice_topo.py only clears leftovers and
checks that the queues appeared.
"""
import re
import subprocess

_QUEUE_MAP = re.compile(r"(\d+)=([0-9a-fA-F-]{36})")


def vsctl(*args):
    """Run one ovs-vsctl command and return its output."""
    result = subprocess.run(["ovs-vsctl", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ovs-vsctl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def port_exists(port):
    """True once a switch has added this port (Mininet adds ports at switch start)."""
    return subprocess.run(["ovs-vsctl", "port-to-br", port], capture_output=True).returncode == 0


def has_qos(port):
    return port_exists(port) and _qos_of(port) is not None


def _qos_of(port):
    uuid = vsctl("get", "port", port, "qos")
    return None if uuid in ("", "[]") else uuid


def _queues_of(qos):
    """{queue_id: queue_uuid} currently attached to a QoS row."""
    return {int(q): u for q, u in _QUEUE_MAP.findall(vsctl("get", "qos", qos, "queues"))}


def apply_queues(port, capacity_bps, rates_bps):
    qos = _qos_of(port)

    if qos is None:
        # First time: create the QoS row and all queues in one transaction
        cmd = ["--", "set", "port", port, "qos=@newqos",
               "--", "--id=@newqos", "create", "qos", "type=linux-htb",
               f"other-config:max-rate={int(capacity_bps)}"]
        if rates_bps:
            cmd.append("queues=" + ",".join(f"{q}=@q{q}" for q in sorted(rates_bps)))
        for q in sorted(rates_bps):
            cmd += ["--", f"--id=@q{q}", "create", "queue",
                    f"other-config:max-rate={int(rates_bps[q])}"]
        vsctl(*cmd)
        return

    current = _queues_of(qos)
    for q, rate in rates_bps.items():
        if q in current:
            vsctl("set", "queue", current[q], f"other-config:max-rate={int(rate)}")
        else:
            vsctl("--", "--id=@q", "create", "queue", f"other-config:max-rate={int(rate)}",
                  "--", "set", "qos", qos, f"queues:{q}=@q")
    for q, uuid in current.items():
        if q not in rates_bps:
            vsctl("remove", "qos", qos, "queues", str(q))
            vsctl("destroy", "queue", uuid)


def clear_all():
    """Remove every QoS and queue (cleanup after a run)."""
    subprocess.run(["ovs-vsctl", "--all", "destroy", "qos", "--", "--all", "destroy", "queue"],
                   capture_output=True)
