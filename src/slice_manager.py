"""
Slice manager: the single source of truth for which slices exist.

Pure Python with no Ryu or OVS imports, so it can be unit-tested anywhere.
The controller calls it from the REST API and from packet handling.

Each slice has:
    name            unique name, e.g. "URLLC"
    queue           OVS queue id on the shared link (assigned automatically)
    hosts           set of member host IPs
    bandwidth_mbps  max rate of the slice's queue on the shared link

Rules enforced here:
    - slice names are unique
    - a host belongs to at most one slice
    - the sum of all slice bandwidths never exceeds the link capacity
      (no over-provisioning)
"""
import ipaddress
import json


class SliceError(Exception):
    """A request that breaks a rule. `status` is the HTTP code to return."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class SliceManager:
    def __init__(self, link_capacity_mbps):
        self.link_capacity_mbps = float(link_capacity_mbps)
        self._slices = {}   # name -> {"queue", "hosts", "bandwidth_mbps"}

    # ---------- loading ----------

    @classmethod
    def from_config(cls, path):
        with open(path) as f:
            cfg = json.load(f)
        mgr = cls(cfg["link_capacity_mbps"])
        for s in cfg["slices"]:
            mgr.create(s["name"], s["hosts"], s["bandwidth_mbps"])
        return mgr

    # ---------- queries ----------

    def get(self, name):
        if name not in self._slices:
            raise SliceError(f"slice '{name}' does not exist", 404)
        return self._describe(name)

    def list(self):
        return [self._describe(n) for n in sorted(self._slices, key=lambda n: self._slices[n]["queue"])]

    def allocated_mbps(self, exclude=None):
        return sum(s["bandwidth_mbps"] for n, s in self._slices.items() if n != exclude)

    def link_status(self):
        used = self.allocated_mbps()
        return {
            "capacity_mbps": self.link_capacity_mbps,
            "allocated_mbps": used,
            "free_mbps": round(self.link_capacity_mbps - used, 6),
        }

    def queue_rates_bps(self):
        """{queue_id: max_rate_bps} for every slice, used to program OVS."""
        return {s["queue"]: int(s["bandwidth_mbps"] * 1_000_000) for s in self._slices.values()}

    def find_slice(self, src_ip, dst_ip):
        """Return (name, queue) if both IPs are in the same slice, else (None, None)."""
        for name, s in self._slices.items():
            if src_ip in s["hosts"] and dst_ip in s["hosts"]:
                return name, s["queue"]
        return None, None

    # ---------- changes ----------

    def create(self, name, hosts, bandwidth_mbps):
        name = self._check_name(name)
        if name in self._slices:
            raise SliceError(f"slice '{name}' already exists", 409)
        hosts = self._check_hosts(hosts, owner=None)
        bw = self._check_bandwidth(bandwidth_mbps, exclude=None)
        self._slices[name] = {"queue": self._free_queue_id(), "hosts": hosts, "bandwidth_mbps": bw}
        return self._describe(name)

    def update(self, name, hosts=None, bandwidth_mbps=None):
        """Change hosts and/or bandwidth. Returns (slice, hosts_changed)."""
        if name not in self._slices:
            raise SliceError(f"slice '{name}' does not exist", 404)
        if hosts is None and bandwidth_mbps is None:
            raise SliceError("nothing to change: give 'hosts' and/or 'bandwidth_mbps'")
        # Validate everything first, so a bad request changes nothing
        new_hosts = self._check_hosts(hosts, owner=name) if hosts is not None else None
        new_bw = self._check_bandwidth(bandwidth_mbps, exclude=name) if bandwidth_mbps is not None else None

        s = self._slices[name]
        hosts_changed = new_hosts is not None and new_hosts != s["hosts"]
        if new_hosts is not None:
            s["hosts"] = new_hosts
        if new_bw is not None:
            s["bandwidth_mbps"] = new_bw
        return self._describe(name), hosts_changed

    def delete(self, name):
        if name not in self._slices:
            raise SliceError(f"slice '{name}' does not exist", 404)
        self._slices.pop(name)
        return {"deleted": name}

    # ---------- undo support ----------

    def snapshot(self):
        """A copy of the current state, to restore if applying a change fails."""
        return {n: {"queue": s["queue"], "hosts": set(s["hosts"]),
                    "bandwidth_mbps": s["bandwidth_mbps"]} for n, s in self._slices.items()}

    def restore(self, snapshot):
        self._slices = {n: {"queue": s["queue"], "hosts": set(s["hosts"]),
                            "bandwidth_mbps": s["bandwidth_mbps"]} for n, s in snapshot.items()}

    # ---------- validation helpers ----------

    def _check_name(self, name):
        if not isinstance(name, str) or not name.strip():
            raise SliceError("'name' must be a non-empty string")
        name = name.strip()
        if not all(c.isalnum() or c in "-_" for c in name):
            raise SliceError("'name' may only contain letters, digits, '-' and '_'")
        return name

    def _check_hosts(self, hosts, owner):
        if not isinstance(hosts, list) or not hosts:
            raise SliceError("'hosts' must be a non-empty list of IP addresses")
        clean = set()
        for h in hosts:
            try:
                clean.add(str(ipaddress.IPv4Address(str(h).strip())))
            except ValueError:
                raise SliceError(f"'{h}' is not a valid IPv4 address")
        for other, s in self._slices.items():
            taken = clean & s["hosts"]
            if other != owner and taken:
                raise SliceError(
                    f"host(s) {', '.join(sorted(taken))} already in slice '{other}'", 409)
        return clean

    def _check_bandwidth(self, bw, exclude):
        if isinstance(bw, bool) or not isinstance(bw, (int, float)) or bw <= 0:
            raise SliceError("'bandwidth_mbps' must be a positive number")
        free = self.link_capacity_mbps - self.allocated_mbps(exclude=exclude)
        if bw > free + 1e-9:
            raise SliceError(
                f"not enough capacity: asked {bw} Mbps, only {round(free, 6)} Mbps free "
                f"on the {self.link_capacity_mbps:g} Mbps link", 409)
        return float(bw)

    def _free_queue_id(self):
        used = {s["queue"] for s in self._slices.values()}
        q = 0
        while q in used:
            q += 1
        return q

    def _describe(self, name):
        s = self._slices[name]
        return {"name": name, "queue": s["queue"], "hosts": sorted(s["hosts"]),
                "bandwidth_mbps": s["bandwidth_mbps"]}
