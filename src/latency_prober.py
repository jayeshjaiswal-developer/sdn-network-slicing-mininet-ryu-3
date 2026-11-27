"""
Latency prober: measures each slice's round-trip time with ping.

The switch can count bytes, but it cannot tell how long a packet waited.
So, every few seconds, this prober pings from one host of each slice to
another host of the same slice. The ping packets go through that slice's
queue on the shared link, so their delay includes the slice's queueing
delay: if the slice is overloaded, its latency goes up.

It runs inside slice_topo.py (it needs the Mininet hosts) and reports each
result to the controller with POST /metrics/latency.

Each slice's prober runs in its own thread and uses host.popen (a separate
process), never host.cmd, so it does not disturb what you type at the
mininet> prompt.
"""
import json
import re
import subprocess
import threading
import urllib.error
import urllib.request

_RTT = re.compile(r"=\s*[\d.]+/([\d.]+)/[\d.]+/[\d.]+\s*ms")
_LOSS = re.compile(r"([\d.]+)% packet loss")


def parse_ping(output):
    """Return (avg_rtt_ms or None, loss_pct) from ping's summary lines."""
    loss_m = _LOSS.search(output)
    loss = float(loss_m.group(1)) if loss_m else 100.0
    rtt_m = _RTT.search(output)
    return (float(rtt_m.group(1)) if rtt_m else None), loss


def probe_pairs(slices, hosts_by_ip):
    """For each slice, pick (source host, destination IP): its first two
    hosts that exist in the network. Slices with fewer than two are skipped."""
    pairs = []
    for s in slices:
        present = [ip for ip in s["hosts"] if ip in hosts_by_ip]
        if len(present) >= 2:
            pairs.append((s["name"], hosts_by_ip[present[0]], present[1]))
    return pairs


def _http(method, url, body=None, timeout=3):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


class LatencyProber:
    def __init__(self, net, api="http://127.0.0.1:8080", interval=2.0, count=3, gap=0.2):
        self.net = net
        self.api = api.rstrip("/")
        self.interval = interval
        self.count = count
        self.gap = gap
        self._stop = threading.Event()
        self._thread = None
        self._warned = False

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="latency-prober", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        hosts_by_ip = {h.IP(): h for h in self.net.hosts}
        while not self._stop.is_set():
            try:
                slices = _http("GET", f"{self.api}/slices")["slices"]
                self._warned = False
            except (urllib.error.URLError, OSError, ValueError, KeyError):
                if not self._warned:
                    print("*** latency prober: controller API not reachable, retrying")
                    self._warned = True
                self._stop.wait(self.interval)
                continue
            # One thread per slice, so a slow slice doesn't delay the others
            workers = [threading.Thread(target=self._probe, args=pair, daemon=True)
                       for pair in probe_pairs(slices, hosts_by_ip)]
            for w in workers:
                w.start()
            for w in workers:
                w.join()
            self._stop.wait(self.interval)

    def _probe(self, slice_name, src_host, dst_ip):
        proc = src_host.popen(
            ["ping", "-n", "-q", "-c", str(self.count), "-i", str(self.gap), "-W", "1", dst_ip],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        rtt, loss = parse_ping(out.decode(errors="replace"))
        try:
            _http("POST", f"{self.api}/metrics/latency",
                  {"slice": slice_name, "src": src_host.IP(), "dst": dst_ip,
                   "rtt_ms": rtt, "loss_pct": loss})
        except (urllib.error.URLError, OSError, ValueError):
            pass   # controller restarting; next round will report again
